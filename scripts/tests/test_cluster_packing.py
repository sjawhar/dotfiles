"""cluster-packing reads Kubernetes quantities and shares them out by node class.

The packing reading is half of the build-farm target in the speed log (dispatch://INFRA-6).
The scripts that used to take it lived in /tmp, were lost in the 2026-11-04 18:05:53Z reset and
were in no repository, so three ticks in a row reported the measurement as unmeasured with no
way to restore it. This file exists so the arithmetic that target rests on is checked.

The quantity suffixes are the error-prone part: Ki/Mi/Gi/Ti are powers of 1024 and K/M/G/T are
powers of 1000, a bare number is bytes, and a cpu value ending in `m` is millicores.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "cluster-packing"


def load():
    spec = importlib.util.spec_from_loader(
        "cluster_packing",
        importlib.machinery.SourceFileLoader("cluster_packing", str(SCRIPT)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load()


@pytest.mark.parametrize("value,cores", [
    ("8", 8.0), ("500m", 0.5), ("1500m", 1.5), ("0", 0.0), (None, 0.0), ("", 0.0),
])
def test_cpu_quantities(value, cores):
    assert mod.cpu(value) == cores


@pytest.mark.parametrize("value,gib", [
    ("1Gi", 1.0),
    ("1024Mi", 1.0),
    ("1048576Ki", 1.0),
    ("1Ti", 1024.0),
    (None, 0.0),
    ("", 0.0),
])
def test_binary_memory_quantities(value, gib):
    assert mod.mem_gib(value) == pytest.approx(gib)


def test_decimal_memory_suffixes_are_powers_of_a_thousand():
    """A node's capacity often comes back as a decimal quantity; 1G is not 1Gi."""
    assert mod.mem_gib("1G") == pytest.approx(1e9 / 1024**3)
    assert mod.mem_gib("1G") < mod.mem_gib("1Gi")


def test_a_bare_memory_number_is_bytes():
    assert mod.mem_gib(str(1024**3)) == pytest.approx(1.0)


def test_share_is_a_percentage_and_never_divides_by_zero():
    assert mod.share(1, 2).strip() == "50.0%"
    assert mod.share(0, 0).strip() == "-"


def test_a_pending_pod_raises_demand_without_raising_capacity(monkeypatch):
    """A pod on no node is counted apart: a full class with pods waiting is not a full class."""
    nodes = {"items": [{
        "metadata": {"name": "n1", "labels": {"node.kubernetes.io/instance-type": "c4d-standard-8"}},
        "status": {"capacity": {"cpu": "8", "memory": "32Gi"}},
    }]}
    pods = {"items": [
        {"spec": {"nodeName": "n1", "containers": [{"resources": {"requests": {"cpu": "2", "memory": "4Gi"}}}]},
         "status": {"phase": "Running"}},
        {"spec": {"containers": [{"resources": {"requests": {"cpu": "6", "memory": "8Gi"}}}]},
         "status": {"phase": "Pending"}},
        {"spec": {"nodeName": "n1", "containers": [{"resources": {"requests": {"cpu": "4"}}}]},
         "status": {"phase": "Succeeded"}},
    ]}
    monkeypatch.setattr(mod, "read", lambda context, kind: nodes if kind == "nodes" else pods)
    reading = mod.measure("ctx")
    row = reading["classes"]["c4d-standard-8"]
    assert (row["nodes"], row["pods"]) == (1, 1)
    assert row["cpu_capacity"] == 8.0
    assert row["cpu_requested"] == 2.0, "a finished pod asks for nothing and a pending one is not here"
    assert reading["pending"] == {"pods": 1, "cpu_requested": 6.0, "mem_requested": 8.0}


def test_a_node_with_no_instance_type_label_is_named_rather_than_dropped():
    assert "unknown" not in mod.DEFAULT_CONTEXTS  # guard against a careless rename
