"""Safety rules: each one passes on the shipped plans and fires on a minimal unsafe change."""

from __future__ import annotations

from typing import Any

import pytest

from chaos_gamedays import rules
from chaos_gamedays.model import from_dict, load
from conftest import EXPERIMENTS, UNSAFE, set_path


def _fired(doc: dict[str, Any]) -> set[str]:
    """Rule ids that fire for a document."""
    return {f.rule for f in rules.check(from_dict(doc))}


def test_shipped_plans_pass_every_rule() -> None:
    for p in sorted(EXPERIMENTS.glob("*.yaml")):
        assert rules.check(load(p)) == [], p.name


def test_valid_plan_has_no_findings(doc: dict[str, Any]) -> None:
    assert _fired(doc) == set()


CASES: list[tuple[str, str, object]] = [
    ("hypothesis-metric-known", "hypothesis/expectations/0/metric", "cart_items"),
    ("hypothesis-within-slo", "hypothesis/expectations/0/value", 500),
    ("hypothesis-within-slo", "hypothesis/expectations/0/op", "<="),
    ("hypothesis-within-slo", "hypothesis/expectations/0/op", ">"),
    ("abort-metric-known", "abort_conditions/0/metric", "cpu_pct"),
    ("abort-beyond-hypothesis", "abort_conditions/1/value", 250),
    ("abort-beyond-hypothesis", "abort_conditions/1/op", "<"),
    ("abort-can-fire", "abort_conditions/0/for", "5m"),
    ("blast-namespaces", "fault/target/namespace", "payments"),
    ("blast-namespaces", "blast_radius/namespaces", ["shop", "kube-system"]),
    ("blast-namespaces", "blast_radius/namespaces", ["shop", "chaos-mesh"]),
    ("blast-pods", "fault/affected", 2),
    ("blast-pods", "fault/target/replicas", 1),
    ("blast-duration", "fault/duration", "15m"),
    ("blast-duration", "load/duration", "4m"),
    ("production-guard", "blast_radius/environment", "production"),
    ("written-before-start", "approval/approved_at", "2026-11-05T14:00:00Z"),
    ("written-before-start", "approval/approved_by", ["platform-team"]),
]


@pytest.mark.parametrize(("rule", "path", "value"), CASES, ids=[f"{r}-{i}" for i, (r, _, _) in enumerate(CASES)])
def test_rule_fires(doc: dict[str, Any], rule: str, path: str, value: object) -> None:
    set_path(doc, path, value)
    assert rule in _fired(doc)


def test_abort_covers_hypothesis_needs_a_hypothesis_metric(doc: dict[str, Any]) -> None:
    doc["hypothesis"]["expectations"] = [{"metric": "checkout_error_rate_pct", "op": "<", "value": 1}]
    doc["abort_conditions"] = [{"metric": "checkout_p99_ms", "op": ">", "value": 900, "for": "30s"}]
    assert "abort-covers-hypothesis" in _fired(doc)


def test_production_passes_with_two_approvers_and_small_traffic(doc: dict[str, Any]) -> None:
    doc["blast_radius"].update(environment="production", max_traffic_pct=10)
    doc["approval"]["approved_by"] = ["sre-lead", "incident-commander"]
    assert _fired(doc) == set()


def test_stricter_hypothesis_than_slo_is_fine(doc: dict[str, Any]) -> None:
    doc["hypothesis"]["expectations"][0]["value"] = 250
    assert _fired(doc) == set()


def test_abort_equal_to_hypothesis_threshold_is_fine(doc: dict[str, Any]) -> None:
    doc["abort_conditions"][1]["value"] = 300
    assert _fired(doc) == set()


def test_unsafe_example_trips_nine_rules_with_fourteen_findings() -> None:
    found = rules.check(load(UNSAFE))
    assert {f.rule for f in found} == {
        "hypothesis-metric-known",
        "hypothesis-within-slo",
        "abort-beyond-hypothesis",
        "abort-can-fire",
        "blast-namespaces",
        "blast-pods",
        "blast-duration",
        "production-guard",
        "written-before-start",
    }
    assert len(found) == 14


def test_unique_ids(doc: dict[str, Any]) -> None:
    e = from_dict(doc)
    assert rules.check_unique_ids([e]) == []
    assert [f.rule for f in rules.check_unique_ids([e, e])] == ["unique-id"]
