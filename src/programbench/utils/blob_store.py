import os
from pathlib import Path

from programbench.constants import HF_REPO_ID, HF_REVISION

BLOB_LOCAL_DIR = os.environ.get("PROGRAMBENCH_BLOB_DIR", "")


def get_blob_dir(instance_id: str) -> Path | None:
    """Return local path to blobs for an instance, downloading from HF if needed.

    If PROGRAMBENCH_BLOB_DIR is set, uses that local directory instead of HF.
    """
    if BLOB_LOCAL_DIR:
        result = Path(BLOB_LOCAL_DIR) / instance_id
        return result if result.exists() else None
    if not HF_REVISION:
        return None
    from huggingface_hub import snapshot_download

    base = Path(
        snapshot_download(
            HF_REPO_ID,
            revision=HF_REVISION,
            allow_patterns=f"{instance_id}/**",
        )
    )
    result = base / instance_id
    return result if result.exists() else None


def sync_all() -> Path | None:
    """Eagerly download all blobs. Returns cache root or None."""
    if BLOB_LOCAL_DIR:
        return Path(BLOB_LOCAL_DIR)
    if not HF_REVISION:
        return None
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(HF_REPO_ID, revision=HF_REVISION))
