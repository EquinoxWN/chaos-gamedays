"""Safety rules a plan must pass before a game day may start (beyond the JSON Schema)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .model import Experiment, Op, Threshold

PROTECTED_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease", "chaos-mesh", "monitoring"})
PRODUCTION_MAX_TRAFFIC_PCT = 10
PRODUCTION_MIN_APPROVERS = 2


@dataclass(frozen=True)
class Finding:
    """One rule violation in one plan."""

    rule: str
    message: str


def _direction(op: Op) -> int:
    """-1 for upper bounds (< <=), +1 for lower bounds (> >=)."""
    return -1 if op in ("<", "<=") else 1


def _looser(a: Threshold, b: Threshold) -> bool:
    """Whether bound a allows values that bound b forbids (same direction assumed)."""
    if _direction(a.op) < 0:
        return a.value > b.value or (a.value == b.value and a.op == "<=" and b.op == "<")
    return a.value < b.value or (a.value == b.value and a.op == ">=" and b.op == ">")


def hypothesis_metrics_known(e: Experiment) -> Iterable[Finding]:
    """Every hypothesis expectation measures a declared steady-state metric."""
    for x in e.expectations:
        if e.slo_for(x.metric) is None:
            yield Finding("hypothesis-metric-known", f"expectation on {x.metric} has no steady_state entry")


def hypothesis_within_slo(e: Experiment) -> Iterable[Finding]:
    """An expectation points the same way as its SLO and is not looser than it."""
    for x in e.expectations:
        s = e.slo_for(x.metric)
        if s is None:
            continue
        if _direction(x.op) != _direction(s.slo.op):
            yield Finding("hypothesis-within-slo", f"{x.text()} points the opposite way to SLO {s.slo.text()}")
        elif _looser(x, s.slo):
            yield Finding("hypothesis-within-slo", f"{x.text()} is looser than SLO {s.slo.text()}: it could hold while the SLO is breached")


def abort_metrics_known(e: Experiment) -> Iterable[Finding]:
    """Every abort condition watches a declared steady-state metric."""
    for a in e.abort_conditions:
        if e.slo_for(a.bound.metric) is None:
            yield Finding("abort-metric-known", f"abort condition on {a.bound.metric} has no steady_state entry")


def abort_covers_hypothesis(e: Experiment) -> Iterable[Finding]:
    """At least one abort condition watches a metric the hypothesis is about."""
    watched = {a.bound.metric for a in e.abort_conditions}
    if not watched & {x.metric for x in e.expectations}:
        yield Finding("abort-covers-hypothesis", "no abort condition watches any hypothesis metric")


def abort_beyond_hypothesis(e: Experiment) -> Iterable[Finding]:
    """An abort fires on the bad side of the expectation, strictly beyond its threshold."""
    for a in e.abort_conditions:
        for x in (x for x in e.expectations if x.metric == a.bound.metric):
            if _direction(a.bound.op) == _direction(x.op):
                yield Finding("abort-beyond-hypothesis", f"abort {a.bound.text()} fires while the hypothesis {x.text()} still holds")
            elif (_direction(x.op) < 0 and a.bound.value < x.value) or (_direction(x.op) > 0 and a.bound.value > x.value):
                yield Finding("abort-beyond-hypothesis", f"abort {a.bound.text()} would stop the run before {x.text()} can be judged")


def abort_can_fire(e: Experiment) -> Iterable[Finding]:
    """Each abort window is shorter than the fault, otherwise it can never trigger in time."""
    for a in e.abort_conditions:
        if a.for_s >= e.fault.duration_s:
            yield Finding("abort-can-fire", f"abort on {a.bound.metric} needs {a.for_text} but the fault lasts {e.fault.duration_text}")


def blast_namespaces(e: Experiment) -> Iterable[Finding]:
    """The fault target lies inside the blast radius and never in a protected namespace."""
    br = e.blast_radius
    for ns in sorted(PROTECTED_NAMESPACES & ({e.fault.namespace} | set(br.namespaces))):
        yield Finding("blast-namespaces", f"namespace {ns} is protected and may not be in a blast radius")
    if e.fault.namespace not in br.namespaces:
        yield Finding("blast-namespaces", f"fault targets namespace {e.fault.namespace}, outside the blast radius {list(br.namespaces)}")


def blast_pods(e: Experiment) -> Iterable[Finding]:
    """The fault touches no more pods than allowed and leaves at least one replica untouched."""
    f, br = e.fault, e.blast_radius
    if f.affected > br.max_pods_affected:
        yield Finding("blast-pods", f"fault affects {f.affected} pods, blast radius allows {br.max_pods_affected}")
    if f.affected >= f.replicas:
        yield Finding("blast-pods", f"fault affects {f.affected} of {f.replicas} replicas: none would survive")


def blast_duration(e: Experiment) -> Iterable[Finding]:
    """The fault fits the blast radius time limit and the load runs at least as long as the fault."""
    if e.fault.duration_s > e.blast_radius.max_duration_s:
        yield Finding("blast-duration", f"fault lasts {e.fault.duration_text}, blast radius allows {e.blast_radius.max_duration_text}")
    if e.load_duration_s < e.fault.duration_s:
        yield Finding("blast-duration", f"load runs {e.load_duration_text}, shorter than the fault ({e.fault.duration_text})")


def production_guard(e: Experiment) -> Iterable[Finding]:
    """Production runs need two approvers and at most 10% of traffic."""
    if e.blast_radius.environment != "production":
        return
    if len(e.approved_by) < PRODUCTION_MIN_APPROVERS:
        yield Finding("production-guard", f"production needs {PRODUCTION_MIN_APPROVERS} approvers, plan has {len(e.approved_by)}")
    if e.blast_radius.max_traffic_pct > PRODUCTION_MAX_TRAFFIC_PCT:
        yield Finding(
            "production-guard",
            f"production allows at most {PRODUCTION_MAX_TRAFFIC_PCT}% of traffic, plan has {e.blast_radius.max_traffic_pct}%",
        )


def written_before_start(e: Experiment) -> Iterable[Finding]:
    """Approval is recorded before the scheduled start, by someone other than the owner."""
    if e.approved_at >= e.scheduled_start:
        yield Finding(
            "written-before-start", f"approved at {e.approved_at:%Y-%m-%dT%H:%MZ}, not before the start {e.scheduled_start:%Y-%m-%dT%H:%MZ}"
        )
    if e.owner in e.approved_by:
        yield Finding("written-before-start", f"owner {e.owner} may not approve their own experiment")


RULES: tuple[Callable[[Experiment], Iterable[Finding]], ...] = (
    hypothesis_metrics_known,
    hypothesis_within_slo,
    abort_metrics_known,
    abort_covers_hypothesis,
    abort_beyond_hypothesis,
    abort_can_fire,
    blast_namespaces,
    blast_pods,
    blast_duration,
    production_guard,
    written_before_start,
)


def check(e: Experiment) -> list[Finding]:
    """All rule violations in one plan."""
    return [f for rule in RULES for f in rule(e)]


def check_unique_ids(plans: Iterable[Experiment]) -> list[Finding]:
    """Experiment ids are unique across a set of plans."""
    seen: set[str] = set()
    out: list[Finding] = []
    for e in plans:
        if e.id in seen:
            out.append(Finding("unique-id", f"duplicate experiment id {e.id}"))
        seen.add(e.id)
    return out
