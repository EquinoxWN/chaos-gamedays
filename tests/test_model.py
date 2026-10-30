"""Plan loading: durations, timestamps, comparisons, JSON Schema checks and safe YAML parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from chaos_gamedays import model
from chaos_gamedays.model import PlanError, from_dict, holds, load, parse_duration, parse_yaml
from conftest import EXPERIMENTS, GD001, TEMPLATE, set_path


@pytest.mark.parametrize(("text", "seconds"), [("30s", 30), ("5m", 300), ("1h", 3600), ("24h", 86400), ("90s", 90)])
def test_parse_duration(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["0s", "5", "5d", "-1m", "1.5m", "25h", "99999m", ""])
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="duration"):
        parse_duration(text)


@pytest.mark.parametrize(
    ("value", "op", "threshold", "expected"),
    [(1, "<", 2, True), (2, "<", 2, False), (2, "<=", 2, True), (3, ">", 2, True), (2, ">", 2, False), (2, ">=", 2, True)],
)
def test_holds(value: float, op: model.Op, threshold: float, expected: bool) -> None:
    assert holds(value, op, threshold) is expected


def test_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(model.schema())


def test_load_shipped_plan() -> None:
    e = load(GD001)
    assert (e.id, e.fault.kind, e.fault.duration_s, e.load_duration_s) == ("GD-001", "pod-kill", 300, 600)
    assert [x.text() for x in e.expectations] == ["checkout_p99_ms < 300", "checkout_error_rate_pct < 1"]
    assert e.abort_conditions[0].for_s == 30
    assert e.scheduled_start.isoformat() == "2026-11-05T14:00:00+00:00"


def test_every_shipped_plan_and_the_template_load() -> None:
    assert sorted(load(p).id for p in EXPERIMENTS.glob("*.yaml")) == ["GD-001", "GD-002", "GD-003"]
    assert load(TEMPLATE).id == "GD-000"


def test_timestamps_stay_strings() -> None:
    assert parse_yaml("t: 2026-11-05T14:00:00Z\n") == {"t": "2026-11-05T14:00:00Z"}


def test_yaml_aliases_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bomb.yaml"
    p.write_text("a: &x [1, 2]\nb: *x\n", encoding="utf-8")
    with pytest.raises(PlanError, match="aliases"):
        load(p)


def test_unsafe_yaml_tags_rejected(tmp_path: Path) -> None:
    p = tmp_path / "tag.yaml"
    p.write_text("a: !!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    with pytest.raises(PlanError, match="not valid YAML"):
        load(p)


def test_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model, "MAX_PLAN_BYTES", 100)
    p = tmp_path / "big.yaml"
    p.write_text("x: " + "a" * 200, encoding="utf-8")
    with pytest.raises(PlanError, match="larger than"):
        load(p)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(PlanError, match="mapping"):
        load(p)


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        ("hypothesis", None, "'hypothesis' is a required property"),
        ("abort_conditions", [], "should be non-empty"),
        ("rollback", [], "should be non-empty"),
        ("blast_radius/max_duration", None, "'max_duration' is a required property"),
        ("id", "GD-1", "does not match"),
        ("apiVersion", "gameday/v2", "was expected"),
        ("fault/kind", "rm-rf", "is not one of"),
        ("fault/duration", "5 minutes", "does not match"),
        ("abort_conditions/0/op", "!=", "is not one of"),
        ("scheduled_start", "next tuesday", "does not match"),
        ("blast_radius/environment", "prod-ish", "is not one of"),
        ("blast_radius/max_traffic_pct", 150, "is greater than the maximum"),
        ("fault/target/namespace", "Shop_NS", "does not match"),
        ("extra_field", "x", "Additional properties are not allowed"),
        ("steady_state/0/metric", "P99 latency", "does not match"),
    ],
)
def test_schema_rejects(doc: dict[str, Any], path: str, value: object, fragment: str) -> None:
    set_path(doc, path, value)
    with pytest.raises(PlanError) as exc:
        from_dict(doc)
    assert any(fragment in p for p in exc.value.problems), exc.value.problems
