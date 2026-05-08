# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import gzip
import json
import posixpath
import tarfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import BinaryIO, Iterable


DEFAULT_REGISTRY = "registry-1.docker.io"
DOCKER_AUTH_URL = "https://auth.docker.io/token"
MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
WORKSPACE_PREFIX = "workspace/"
DOC_DIRS = {"doc", "docs", "documentation"}


@dataclass(frozen=True)
class ImageRef:
    repository: str
    tag: str
    registry: str = DEFAULT_REGISTRY


@dataclass(frozen=True)
class Whiteout:
    layer: int
    path: str
    target: str
    removed_paths: int


@dataclass
class LayeredFileTree:
    entries: dict[str, str] = field(default_factory=dict)
    whiteouts: list[Whiteout] = field(default_factory=list)

    def apply_member(self, member: tarfile.TarInfo, *, layer: int) -> None:
        name = member.name
        while name.startswith("./"):
            name = name[2:]
        if not name:
            return

        parent, base = posixpath.split(name)
        if base.startswith(".wh."):
            self._apply_whiteout(parent, base, layer=layer)
            return

        if member.isdir():
            typ = "dir"
        elif member.isfile():
            typ = "file"
        elif member.issym() or member.islnk():
            typ = "link"
        else:
            typ = "other"
        self.entries[name] = typ

    def _apply_whiteout(self, parent: str, base: str, *, layer: int) -> None:
        if base == ".wh..wh..opq":
            prefix = parent.rstrip("/") + "/"
            removed = [p for p in self.entries if p.startswith(prefix) and p != parent]
            target = parent
        else:
            target = posixpath.join(parent, base[4:]) if parent else base[4:]
            prefix = target.rstrip("/") + "/"
            removed = [p for p in self.entries if p == target or p.startswith(prefix)]

        for path in removed:
            self.entries.pop(path, None)
        self.whiteouts.append(
            Whiteout(layer=layer, path=posixpath.join(parent, base), target=target, removed_paths=len(removed))
        )


@dataclass(frozen=True)
class CleanroomDocsAudit:
    image: str
    workspace_entries: dict[str, str]
    doc_entries: tuple[str, ...]
    readme_entries: tuple[str, ...]
    license_entries: tuple[str, ...]
    doc_whiteouts: tuple[Whiteout, ...]

    @property
    def has_doc_directory(self) -> bool:
        return bool(self.doc_entries)

    @property
    def ok(self) -> bool:
        return self.has_doc_directory


def parse_image_ref(image: str, default_tag: str = "latest") -> ImageRef:
    tag = default_tag
    repository = image
    if ":" in image.rsplit("/", 1)[-1]:
        repository, tag = image.rsplit(":", 1)
    if repository.startswith("docker.io/"):
        repository = repository[len("docker.io/") :]
    if repository.startswith("library/"):
        repository = f"library/{repository.split('/', 1)[1]}"
    return ImageRef(repository=repository, tag=tag)


def _json_request(url: str, headers: dict[str, str] | None = None, *, timeout: float) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def _bearer_token(image_ref: ImageRef, *, timeout: float) -> str:
    query = urllib.parse.urlencode(
        {"service": "registry.docker.io", "scope": f"repository:{image_ref.repository}:pull"}
    )
    data = _json_request(f"{DOCKER_AUTH_URL}?{query}", timeout=timeout)
    return data["token"]


def _manifest(image_ref: ImageRef, token: str, *, timeout: float) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT}
    return _json_request(
        f"https://{image_ref.registry}/v2/{image_ref.repository}/manifests/{image_ref.tag}",
        headers,
        timeout=timeout,
    )


def _blob_url(image_ref: ImageRef, digest: str) -> str:
    return f"https://{image_ref.registry}/v2/{image_ref.repository}/blobs/{digest}"


def _open_layer(stream: BinaryIO, media_type: str) -> tarfile.TarFile:
    if media_type.endswith("+gzip") or media_type.endswith(".gzip"):
        return tarfile.open(fileobj=gzip.GzipFile(fileobj=stream), mode="r|")
    return tarfile.open(fileobj=stream, mode="r|")


def apply_tar_layer(stream: BinaryIO, media_type: str, *, layer_index: int, tree: LayeredFileTree) -> None:
    with _open_layer(stream, media_type) as tar:
        for member in tar:
            tree.apply_member(member, layer=layer_index)


def apply_tar_layers(layers: Iterable[tuple[BinaryIO, str]], tree: LayeredFileTree | None = None) -> LayeredFileTree:
    tree = tree or LayeredFileTree()
    for layer_index, (stream, media_type) in enumerate(layers, 1):
        apply_tar_layer(stream, media_type, layer_index=layer_index, tree=tree)
    return tree


def audit_entries(image: str, tree: LayeredFileTree) -> CleanroomDocsAudit:
    workspace_entries = {
        path[len(WORKSPACE_PREFIX) :]: typ
        for path, typ in sorted(tree.entries.items())
        if path.startswith(WORKSPACE_PREFIX) and path != "workspace"
    }
    doc_entries = tuple(
        path for path, typ in workspace_entries.items() if typ != "dir" and path.split("/", 1)[0].lower() in DOC_DIRS
    )
    readme_entries = tuple(path for path in workspace_entries if posixpath.basename(path).lower().startswith("readme"))
    license_entries = tuple(path for path in workspace_entries if "license" in posixpath.basename(path).lower())
    doc_whiteouts = tuple(
        whiteout
        for whiteout in tree.whiteouts
        if whiteout.target.startswith(WORKSPACE_PREFIX)
        and whiteout.target[len(WORKSPACE_PREFIX) :].split("/", 1)[0].lower() in DOC_DIRS
    )
    return CleanroomDocsAudit(
        image=image,
        workspace_entries=workspace_entries,
        doc_entries=doc_entries,
        readme_entries=readme_entries,
        license_entries=license_entries,
        doc_whiteouts=doc_whiteouts,
    )


def audit_cleanroom_image(image: str, *, timeout: float = 60) -> CleanroomDocsAudit:
    image_ref = parse_image_ref(image)
    token = _bearer_token(image_ref, timeout=timeout)
    manifest = _manifest(image_ref, token, timeout=timeout)

    layers = manifest.get("layers")
    if layers is None:
        raise ValueError(f"Expected image manifest for {image}, got {manifest.get('mediaType')}")

    headers = {"Authorization": f"Bearer {token}"}
    tree = LayeredFileTree()
    for layer_index, layer in enumerate(layers, 1):
        req = urllib.request.Request(_blob_url(image_ref, layer["digest"]), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            apply_tar_layer(response, layer["mediaType"], layer_index=layer_index, tree=tree)

    return audit_entries(image, tree)
