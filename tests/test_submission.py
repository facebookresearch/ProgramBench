# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

"""Tests for submission helpers that don't need the benchmark data."""

import json

from programbench.submission import recombine_eval_json, split_eval_json


def test_split_recombine_roundtrip_is_lossless(tmp_path):
    iid = "org__tool.abc1234"
    d = tmp_path / iid
    d.mkdir()
    original = {
        "instance_id": iid,
        "log": [{"step": 1, "out": "x" * 200}, {"step": 2, "out": "y"}],
        "test_results": [
            {"branch": "main", "name": "t_pass", "is_resolved": True, "extra": {"duration": 0.5}},
            {
                "branch": "main",
                "name": "t_fail",
                "is_resolved": False,
                "extra": {"message": "assertion failed", "text": "trace " * 50, "duration": 1.2},
            },
            {"branch": "feat", "name": "t_other", "is_resolved": False, "extra": {"text": "boom"}},
        ],
    }
    eval_json = d / f"{iid}.eval.json"
    eval_json.write_text(json.dumps(original, indent=2))

    split_eval_json(d, iid)
    light = json.loads(eval_json.read_text())
    assert light["log"] == []
    assert "message" not in light["test_results"][1]["extra"]
    assert (d / f"{iid}.eval.log.json").exists()

    assert recombine_eval_json(d, iid) is True
    assert json.loads(eval_json.read_text()) == original
    assert not (d / f"{iid}.eval.log.json").exists()


def test_split_is_idempotent_and_noop_when_light(tmp_path):
    iid = "org__tool.def5678"
    d = tmp_path / iid
    d.mkdir()
    light = {"instance_id": iid, "log": [], "test_results": [{"branch": "main", "name": "t", "is_resolved": True}]}
    (d / f"{iid}.eval.json").write_text(json.dumps(light))
    split_eval_json(d, iid)
    assert not (d / f"{iid}.eval.log.json").exists()  # nothing heavy -> no split file written
