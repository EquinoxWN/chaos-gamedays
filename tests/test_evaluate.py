"""Hypothesis judging: steady state first, then abort conditions, then every sample in the fault window."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from chaos_gamedays.evaluate import Series, evaluate, first_sustained, load_series, parse_series
from chaos_gamedays.model import Experiment, Threshold, from_dict, load
from conftest import _BASE, GD001, SERIES

PLAN = load(GD001)  # fault starts at t=120 in the series below and lasts 300 s


def _series(p99: list[float], err: list[float] | None = None, start: float = 120, step: float = 15) -> Series:
    """Series with samples every step seconds."""
    err = err if err is not None else [0.2] * len(p99)
    return Series(
        start,
        {
            "checkout_p99_ms": [(i * step, v) for i, v in enumerate(p99)],
            "checkout_error_rate_pct": [(i * step, v) for i, v in enumerate(err)],
        },
    )


def test_held() -> None:
    r = evaluate(PLAN, _series([200.0] * 41))
    assert r.verdict == "held"
    assert r.breaches == []


def test_refuted_lists_each_breach() -> None:
    p99 = [200.0] * 41
    p99[14] = p99[15] = 350.0  # t=210 and t=225, inside the fault window
    r = evaluate(PLAN, _series(p99))
    assert r.verdict == "refuted"
    assert r.breaches == [
        "checkout_p99_ms=350 at t=210s breaks checkout_p99_ms < 300",
        "checkout_p99_ms=350 at t=225s breaks checkout_p99_ms < 300",
    ]


def test_breach_outside_fault_window_does_not_count() -> None:
    p99 = [200.0] * 41
    p99[30] = 350.0  # t=450, after the fault ended at t=420
    assert evaluate(PLAN, _series(p99)).verdict == "held"


def test_aborted_when_condition_is_sustained() -> None:
    err = [0.2] * 41
    for i in range(12, 16):  # t=180..225, error rate 6% for 45 s (abort needs 30 s)
        err[i] = 6.0
    r = evaluate(PLAN, _series([200.0] * 41, err))
    assert r.verdict == "aborted"
    assert r.aborted_at_s == 210
    assert r.reasons == ["abort condition checkout_error_rate_pct > 5 for 30s fired at t=210s"]


def test_short_spike_does_not_abort() -> None:
    err = [0.2] * 41
    err[12] = err[13] = 6.0  # only 15 s above 5%
    assert evaluate(PLAN, _series([200.0] * 41, err)).verdict == "refuted"


def test_inconclusive_when_steady_state_is_broken() -> None:
    p99 = [200.0] * 41
    p99[2] = 320.0  # before the fault
    r = evaluate(PLAN, _series(p99))
    assert r.verdict == "inconclusive"
    assert "steady state not met" in r.reasons[0]


def test_inconclusive_without_samples() -> None:
    assert evaluate(PLAN, Series(120, {})).verdict == "inconclusive"
    only_before = _series([200.0] * 8)  # samples stop at t=105
    assert evaluate(PLAN, only_before).verdict == "inconclusive"


def test_first_sustained() -> None:
    pts = [(0.0, 1.0), (10.0, 9.0), (20.0, 9.0), (30.0, 1.0), (40.0, 9.0), (50.0, 9.0), (60.0, 9.0)]
    bound = Threshold("m", ">", 5)
    assert first_sustained(pts, bound, 10, 0) == 20
    assert first_sustained(pts, bound, 20, 0) == 60
    assert first_sustained(pts, bound, 30, 0) is None
    assert first_sustained(pts, bound, 0, 45) == 50


@pytest.mark.parametrize(
    ("doc", "fragment"),
    [
        ([], "JSON object"),
        ({"samples": {}}, "fault_start_s"),
        ({"fault_start_s": -1, "samples": {}}, "fault_start_s"),
        ({"fault_start_s": True, "samples": {}}, "fault_start_s"),
        ({"fault_start_s": 0, "samples": []}, "samples must be"),
        ({"fault_start_s": 0, "samples": {"m": [[0, "x"]]}}, "must be \\[t, value\\]"),
        ({"fault_start_s": 0, "samples": {"m": [[0, 1, 2]]}}, "must be \\[t, value\\]"),
        ({"fault_start_s": 0, "samples": {"m": [[5, 1], [5, 2]]}}, "strictly increase"),
        ({"fault_start_s": 0, "samples": {"m": {"t": 1}}}, "must be a list"),
    ],
)
def test_parse_series_rejects(doc: Any, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        parse_series(doc)


@pytest.mark.parametrize(("name", "verdict"), [("held", "held"), ("refuted", "refuted"), ("aborted", "aborted")])
def test_shipped_demo_series(name: str, verdict: str) -> None:
    assert evaluate(PLAN, load_series(SERIES / f"gd-001-{name}.json")).verdict == verdict


def _single_metric_plan(doc: dict[str, Any]) -> Experiment:
    """GD-001 reduced to the p99 metric only."""
    doc["steady_state"] = doc["steady_state"][:1]
    doc["hypothesis"]["expectations"] = doc["hypothesis"]["expectations"][:1]
    doc["abort_conditions"] = [{"metric": "checkout_p99_ms", "op": ">", "value": 1000, "for": "60s"}]
    return from_dict(doc)


@settings(max_examples=200, deadline=None)
@given(during=st.lists(st.floats(min_value=0, max_value=999, allow_nan=False), min_size=21, max_size=21))
def test_property_verdict_matches_window_samples(during: list[float]) -> None:
    plan = _single_metric_plan(copy.deepcopy(_BASE))
    values = [200.0] * 8 + during + [200.0] * 12  # fault window is samples 8..28 (t=120..420)
    r = evaluate(plan, Series(120, {"checkout_p99_ms": [(i * 15.0, v) for i, v in enumerate(values)]}))
    bad = [v for v in during if not v < 300]
    assert r.verdict == ("refuted" if bad else "held")
    assert len(r.breaches) == len(bad)


def test_breach_at_the_abort_sample_is_reported_and_later_ones_are_not() -> None:
    err = [0.2] * 41
    for i in range(12, 16):  # abort fires at t=210
        err[i] = 6.0
    p99 = [200.0] * 41
    p99[14] = p99[15] = 350.0  # t=210 (the abort sample) and t=225 (after the stop)
    r = evaluate(PLAN, _series(p99, err))
    assert r.verdict == "aborted"
    assert [b for b in r.breaches if b.startswith("checkout_p99_ms")] == ["checkout_p99_ms=350 at t=210s breaks checkout_p99_ms < 300"]


def test_parse_series_enforces_the_sample_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chaos_gamedays.evaluate.MAX_SAMPLES", 10)
    b: list[list[int]] = [[i, 1] for i in range(4)]
    ok = {"fault_start_s": 0, "samples": {"a": [[i, 1] for i in range(6)], "b": b}}
    assert sum(len(v) for v in parse_series(ok).samples.values()) == 10
    b.append([4, 1])
    with pytest.raises(ValueError, match="more than 10 samples"):
        parse_series(ok)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_parse_series_rejects_non_finite_numbers(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        parse_series({"fault_start_s": 0, "samples": {"m": [[0, 1], [1, bad]]}})
    with pytest.raises(ValueError, match="finite"):
        parse_series({"fault_start_s": 0, "samples": {"m": [[bad, 1]]}})
    with pytest.raises(ValueError, match="fault_start_s"):
        parse_series({"fault_start_s": bad, "samples": {}})


def test_load_series_rejects_nan_in_json(tmp_path: Any) -> None:
    p = tmp_path / "s.json"
    p.write_text('{"fault_start_s": 0, "samples": {"m": [[0, NaN]]}}', encoding="utf-8")
    with pytest.raises(ValueError, match="finite"):
        load_series(p)
