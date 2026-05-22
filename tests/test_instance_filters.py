# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

from programbench.utils.instance_filters import filter_instances


def _inst(iid: str, branches: dict | None = None) -> dict:
    return {"instance_id": iid, "branches": branches or {}}


INSTANCES = [
    _inst("alpha__foo.abc", {"b1": {"tests": ["t1"]}}),
    _inst("beta__bar.def", {"b2": {"tests": ["t2"]}}),
    _inst("gamma__baz.ghi"),
]


class TestFilterInstances:
    def test_no_filters_returns_all(self):
        assert filter_instances(INSTANCES) == INSTANCES

    def test_regex_filter(self):
        assert [i["instance_id"] for i in filter_instances(INSTANCES, filter_spec="alpha.*")] == ["alpha__foo.abc"]

    def test_regex_filter_no_match(self):
        assert filter_instances(INSTANCES, filter_spec="nonexistent") == []

    def test_slice_spec(self):
        assert [i["instance_id"] for i in filter_instances(INSTANCES, slice_spec="0:2")] == [
            "alpha__foo.abc",
            "beta__bar.def",
        ]

    def test_slice_from_end(self):
        assert [i["instance_id"] for i in filter_instances(INSTANCES, slice_spec="-1:")] == ["gamma__baz.ghi"]

    def test_has_test_branch(self):
        result = filter_instances(INSTANCES, has_test_branch=True)
        assert [i["instance_id"] for i in result] == ["alpha__foo.abc", "beta__bar.def"]

    def test_filter_and_slice_combined(self):
        result = filter_instances(INSTANCES, filter_spec="(alpha|beta).*", slice_spec="0:1")
        assert [i["instance_id"] for i in result] == ["alpha__foo.abc"]

    def test_shuffle_is_deterministic(self):
        r1 = [i["instance_id"] for i in filter_instances(INSTANCES, shuffle=True)]
        r2 = [i["instance_id"] for i in filter_instances(INSTANCES, shuffle=True)]
        assert r1 == r2
