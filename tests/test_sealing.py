"""Sealing: plans are fixed before the start, and any later edit or late seal is detected."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from chaos_gamedays import sealing
from chaos_gamedays.model import from_dict, load
from conftest import EXPERIMENTS, LOCK

BEFORE = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
AFTER = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)


def test_digest_ignores_key_order(doc: dict[str, Any]) -> None:
    reordered = dict(reversed(list(doc.items())))
    assert sealing.digest(from_dict(doc)) == sealing.digest(from_dict(reordered))


def test_digest_changes_with_any_field(doc: dict[str, Any]) -> None:
    before = sealing.digest(from_dict(doc))
    doc["abort_conditions"][0]["value"] = 6
    assert sealing.digest(from_dict(doc)) != before


def test_seal_before_start(doc: dict[str, Any]) -> None:
    seals: dict[str, sealing.Seal] = {}
    s = sealing.seal(from_dict(doc), seals, BEFORE)
    assert seals["GD-001"] == s
    assert s.sealed_at == BEFORE


def test_seal_refused_at_or_after_start(doc: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="cannot seal"):
        sealing.seal(from_dict(doc), {}, AFTER)


def test_reseal_same_plan_keeps_original_time(doc: dict[str, Any]) -> None:
    seals: dict[str, sealing.Seal] = {}
    first = sealing.seal(from_dict(doc), seals, BEFORE)
    again = sealing.seal(from_dict(doc), seals, datetime(2026, 11, 1, tzinfo=UTC))
    assert again == first


def test_changed_plan_needs_reseal(doc: dict[str, Any]) -> None:
    seals: dict[str, sealing.Seal] = {}
    sealing.seal(from_dict(doc), seals, BEFORE)
    doc["fault"]["duration"] = "6m"
    with pytest.raises(ValueError, match="re-approved"):
        sealing.seal(from_dict(doc), seals, BEFORE)
    later = datetime(2026, 11, 1, tzinfo=UTC)
    assert sealing.seal(from_dict(doc), seals, later, reseal=True).sealed_at == later


def test_verify_detects_edit_after_seal(doc: dict[str, Any]) -> None:
    seals: dict[str, sealing.Seal] = {}
    sealing.seal(from_dict(doc), seals, BEFORE)
    assert sealing.verify([from_dict(doc)], seals) == []
    doc["blast_radius"]["max_pods_affected"] = 2
    assert sealing.verify([from_dict(doc)], seals) == ["GD-001: plan changed after it was sealed"]


def test_verify_reports_unsealed_late_and_stale(doc: dict[str, Any]) -> None:
    e = from_dict(doc)
    assert sealing.verify([e], {}) == ["GD-001: not sealed"]
    late = {"GD-001": sealing.Seal(sealing.digest(e), AFTER)}
    assert sealing.verify([e], late) == ["GD-001: sealed at or after its scheduled start"]
    stale = {"GD-999": sealing.Seal("0" * 64, BEFORE)}
    assert sealing.verify([], stale) == ["GD-999: lock entry has no plan"]


def test_lock_round_trip(tmp_path: Path, doc: dict[str, Any]) -> None:
    seals: dict[str, sealing.Seal] = {}
    sealing.seal(from_dict(doc), seals, BEFORE)
    lock = tmp_path / "plans.lock.json"
    sealing.write_lock(lock, seals)
    assert sealing.read_lock(lock) == seals
    assert sealing.read_lock(tmp_path / "absent.json") == {}


@pytest.mark.parametrize("text", ["[]", '{"version": 2, "plans": {}}', '{"version": 1, "plans": {"GD-001": {"sha256": 1}}}'])
def test_bad_lock_rejected(tmp_path: Path, text: str) -> None:
    lock = tmp_path / "bad.json"
    lock.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"lock|entry"):
        sealing.read_lock(lock)


def test_shipped_lock_matches_shipped_plans() -> None:
    plans = [load(p) for p in sorted(EXPERIMENTS.glob("*.yaml"))]
    assert sealing.verify(plans, sealing.read_lock(LOCK)) == []
