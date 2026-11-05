"""Seeded mutation fuzz: stale-answer derivations fail closed with ValueError, never KeyError/TypeError/IndexError."""
from copy import deepcopy
from pathlib import Path
import random

import pytest

from stalecheck.contracts import read_json
from stalecheck.stale_check import make_report_v3
from stalecheck.stale_check_v4 import make_report_v4

FIXTURES = Path(__file__).resolve().parents[1] / "src" / "stalecheck" / "fixtures"
REPLACEMENTS = [None, True, False, 0, 1, -1, 10**12, 2.5, "", "x", "x" * 5000, [], {}, [None], {"a": 1}, "2026-11-01", "claim-1", "0" * 64, "\ud800", "https://example.com/x"]


def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for key, value in node.items():
            yield from paths(value, prefix + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from paths(value, prefix + (index,))


def mutate(tree, rng):
    path = rng.choice([p for p in paths(tree) if p])
    parent = tree
    for step in path[:-1]:
        parent = parent[step]
    key = path[-1]
    kind = rng.choice(["delete", "replace", "replace", "replace", "duplicate", "truncate", "rename"])
    if kind == "delete":
        del parent[key]
    elif kind == "duplicate" and isinstance(parent, list):
        parent.append(deepcopy(parent[key]))
    elif kind == "truncate" and isinstance(parent[key], str):
        parent[key] = parent[key][:rng.randint(0, max(len(parent[key]) - 1, 0))]
    elif kind == "rename" and isinstance(parent, dict):
        parent[key + "_x"] = parent.pop(key)
    else:
        parent[key] = deepcopy(rng.choice(REPLACEMENTS))
    return f"{kind}:{'/'.join(map(str, path))}"


@pytest.mark.parametrize("fixture,builder", [("stale-check-demo.json", make_report_v3), ("stale-check-v4-demo.json", make_report_v4)])
def test_mutated_transcripts_fail_closed_with_value_errors(fixture, builder):
    base = read_json(FIXTURES / fixture)
    builder(deepcopy(base), "fd-base")
    rng = random.Random(20261101)
    accepted = rejected = 0
    for _ in range(1500):
        tree = deepcopy(base)
        steps = [mutate(tree, rng) for _ in range(rng.choice([1, 1, 2]))]
        try:
            builder(tree, "fd-fuzz")
            accepted += 1
        except ValueError:  # ValidationError, or a lone surrogate that cannot be encoded.
            rejected += 1
        except Exception as exc:  # noqa: BLE001 - any other type means a validator missed a case.
            pytest.fail(f"{type(exc).__name__} escaped for {steps}: {exc}")
    assert rejected > 10 * accepted
