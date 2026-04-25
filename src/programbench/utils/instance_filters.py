import logging
import random
import re

log = logging.getLogger(__name__)


def _apply_filter(instances: list[dict], predicate, label: str) -> list[dict]:
    before = len(instances)
    instances = [i for i in instances if predicate(i)]
    if (after := len(instances)) != before:
        log.info("%s: %d -> %d instances", label, before, after)
    return instances


def filter_instances(
    instances: list[dict],
    *,
    filter_spec: str = "",
    slice_spec: str = "",
    shuffle: bool = False,
    has_test_branch: bool = False,
) -> list[dict]:
    """Filter, slice, and optionally shuffle instances by instance_id."""
    from programbench.utils.load_data import get_active_branches

    if shuffle:
        instances = sorted(instances.copy(), key=lambda x: x["instance_id"])
        random.seed(42)
        random.shuffle(instances)
    if filter_spec:
        instances = _apply_filter(
            instances,
            lambda i: re.match(filter_spec, i["instance_id"]),
            "Instance filter",
        )
    if slice_spec:
        before = len(instances)
        values = [int(x) if x else None for x in slice_spec.split(":")]
        instances = instances[slice(*values)]
        if (after := len(instances)) != before:
            log.info("Instance slice: %d -> %d instances", before, after)
    if has_test_branch:
        instances = _apply_filter(instances, lambda i: bool(get_active_branches(i)), "Test branch filter")
    return instances
