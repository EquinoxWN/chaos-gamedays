"""Shared fixtures: paths to the shipped plans and a fresh copy of a valid plan document."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from chaos_gamedays.model import parse_yaml

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "experiments"
GD001 = EXPERIMENTS / "gd-001-valkey-replica-kill.yaml"
LOCK = EXPERIMENTS / "plans.lock.json"
SERIES = ROOT / "examples" / "series"
UNSAFE = ROOT / "examples" / "unsafe" / "gd-900-unsafe.yaml"
TEMPLATE = ROOT / "docs" / "templates" / "experiment.yaml"
BRIEFS = ROOT / "docs" / "gamedays"
_BASE: Any = parse_yaml(GD001.read_text(encoding="utf-8"))


@pytest.fixture
def doc() -> dict[str, Any]:
    """A deep copy of the valid GD-001 plan document, safe to mutate."""
    out: dict[str, Any] = copy.deepcopy(_BASE)
    return out


def set_path(d: dict[str, Any], path: str, value: object) -> None:
    """Set a nested key given as a/b/0/c; value None deletes it."""
    *parents, last = path.split("/")
    node: Any = d
    for part in parents:
        node = node[int(part)] if part.isdigit() else node[part]
    if value is None:
        del node[last]
    else:
        node[last] = value
