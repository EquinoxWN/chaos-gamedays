"""Judge a hypothesis and its abort conditions against a recorded metric series."""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .model import Experiment, Threshold, holds

MAX_SERIES_BYTES = 8 * 1024 * 1024
MAX_SAMPLES = 200_000
Verdict = Literal["held", "refuted", "aborted", "inconclusive"]


@dataclass(frozen=True)
class Series:
    """Samples per metric as (seconds since start, value), with the fault start offset."""

    fault_start_s: float
    samples: dict[str, list[tuple[float, float]]]


@dataclass
class Result:
    """Outcome of one evaluation, with the evidence behind it."""

    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    aborted_at_s: float | None = None
    breaches: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form."""
        return {"verdict": self.verdict, "reasons": self.reasons, "aborted_at_s": self.aborted_at_s, "breaches": self.breaches}


def parse_series(doc: object) -> Series:
    """Validated series from a parsed JSON document."""
    if not isinstance(doc, dict):
        raise ValueError("series must be a JSON object")
    start, raw = doc.get("fault_start_s"), doc.get("samples")
    if not isinstance(start, int | float) or isinstance(start, bool) or not math.isfinite(start) or start < 0:
        raise ValueError("fault_start_s must be a number >= 0")
    if not isinstance(raw, dict):
        raise ValueError("samples must be an object of metric -> [[t, value], ...]")
    out: dict[str, list[tuple[float, float]]] = {}
    total = 0
    for metric, points in raw.items():
        if not isinstance(points, list):
            raise ValueError(f"samples for {metric} must be a list")
        total += len(points)
        if total > MAX_SAMPLES:
            raise ValueError(f"more than {MAX_SAMPLES} samples")
        pts: list[tuple[float, float]] = []
        for p in points:
            if not (
                isinstance(p, list)
                and len(p) == 2
                and all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in p)
            ):
                raise ValueError(f"sample {p!r} for {metric} must be [t, value] with finite numbers")
            pts.append((float(p[0]), float(p[1])))
        if any(b[0] <= a[0] for a, b in itertools.pairwise(pts)):
            raise ValueError(f"sample times for {metric} must strictly increase")
        out[str(metric)] = pts
    return Series(float(start), out)


def load_series(path: Path) -> Series:
    """Read and validate a series file."""
    if path.stat().st_size > MAX_SERIES_BYTES:
        raise ValueError(f"{path}: series file larger than {MAX_SERIES_BYTES} bytes")
    return parse_series(json.loads(path.read_text(encoding="utf-8")))


def first_sustained(points: list[tuple[float, float]], bound: Threshold, for_s: float, since: float) -> float | None:
    """Earliest time at or after since when the bound has held continuously for for_s seconds."""
    run_start: float | None = None
    for t, v in points:
        if t < since:
            continue
        if holds(v, bound.op, bound.value):
            run_start = t if run_start is None else run_start
            if t - run_start >= for_s:
                return t
        else:
            run_start = None
    return None


def evaluate(e: Experiment, s: Series) -> Result:
    """Steady state before the fault, then abort conditions, then the hypothesis during the fault."""
    fault_end = s.fault_start_s + e.fault.duration_s
    reasons: list[str] = []
    for st in e.steady_state:
        before = [v for t, v in s.samples.get(st.metric, []) if t < s.fault_start_s]
        if not before:
            reasons.append(f"no steady-state samples for {st.metric} before the fault")
        elif not all(holds(v, st.slo.op, st.slo.value) for v in before):
            reasons.append(f"steady state not met before the fault: {st.slo.text()} was breached")
    if reasons:
        return Result("inconclusive", reasons)

    fired: list[tuple[float, str]] = []
    for a in e.abort_conditions:
        t = first_sustained(s.samples.get(a.bound.metric, []), a.bound, a.for_s, s.fault_start_s)
        if t is not None:
            fired.append((t, f"abort condition {a.bound.text()} for {a.for_text} fired at t={t:g}s"))
    stop = min(fired)[0] if fired else None

    breaches: list[str] = []
    for x in e.expectations:
        window = [(t, v) for t, v in s.samples.get(x.metric, []) if s.fault_start_s <= t <= fault_end and (stop is None or t <= stop)]
        if not window:
            reasons.append(f"no samples for {x.metric} during the fault")
            continue
        breaches += [f"{x.metric}={v:g} at t={t:g}s breaks {x.text()}" for t, v in window if not holds(v, x.op, x.value)]

    if fired:
        return Result("aborted", [m for _, m in sorted(fired)], stop, breaches)
    if breaches:
        return Result("refuted", [f"{len(breaches)} sample(s) broke the hypothesis"], None, breaches)
    if reasons:
        return Result("inconclusive", reasons)
    return Result("held", [f"all {len(e.expectations)} expectation(s) held for the whole fault window ({e.fault.duration_text})"])
