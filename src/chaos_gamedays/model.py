"""Experiment plan model: load a YAML plan, check it against the JSON Schema, build typed objects."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from jsonschema import Draft202012Validator

MAX_PLAN_BYTES = 256 * 1024
MAX_DURATION_S = 24 * 3600
Op = Literal["<", "<=", ">", ">="]
_DURATION = re.compile(r"^([1-9][0-9]{0,4})(s|m|h)$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600}


class PlanError(ValueError):
    """A plan that cannot be read or does not match the schema."""

    def __init__(self, source: str, problems: list[str]) -> None:
        super().__init__(f"{source}: " + "; ".join(problems))
        self.source, self.problems = source, problems


def parse_duration(text: str) -> int:
    """Seconds in a duration such as 30s, 5m or 1h."""
    m = _DURATION.match(text)
    if not m:
        raise ValueError(f"bad duration {text!r} (use e.g. 30s, 5m, 1h)")
    seconds = int(m.group(1)) * _UNIT_S[m.group(2)]
    if seconds > MAX_DURATION_S:
        raise ValueError(f"duration {text!r} exceeds 24h")
    return seconds


def parse_time(text: str) -> datetime:
    """UTC datetime from an ISO 8601 timestamp with Z or an offset; naive times are refused."""
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"timestamp {text!r} has no time zone (end it in Z)")
    return t.astimezone(UTC)


def holds(value: float, op: Op, threshold: float) -> bool:
    """Whether value op threshold is true."""
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == ">":
        return value > threshold
    return value >= threshold


@dataclass(frozen=True)
class Threshold:
    """A metric bound such as p99 < 300."""

    metric: str
    op: Op
    value: float

    def text(self) -> str:
        """Human-readable form of the bound."""
        return f"{self.metric} {self.op} {self.value:g}"


@dataclass(frozen=True)
class SteadyState:
    """A metric that describes normal behaviour, with its SLO."""

    metric: str
    description: str
    unit: str
    slo: Threshold


@dataclass(frozen=True)
class AbortCondition:
    """Stop the experiment when the bound holds continuously for the window."""

    bound: Threshold
    for_s: int
    for_text: str


@dataclass(frozen=True)
class Fault:
    """The fault to inject and where."""

    kind: str
    namespace: str
    selector: dict[str, str]
    replicas: int
    affected: int
    duration_s: int
    duration_text: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class BlastRadius:
    """The most the experiment is allowed to touch."""

    environment: str
    namespaces: tuple[str, ...]
    max_pods_affected: int
    max_traffic_pct: int
    max_duration_s: int
    max_duration_text: str
    user_impact: str
    dependencies: tuple[str, ...]


@dataclass(frozen=True)
class Experiment:
    """A complete game day plan."""

    id: str
    title: str
    owner: str
    service: str
    scheduled_start: datetime
    steady_state: tuple[SteadyState, ...]
    statement: str
    expectations: tuple[Threshold, ...]
    fault: Fault
    load_rps: int
    load_duration_s: int
    load_duration_text: str
    blast_radius: BlastRadius
    abort_conditions: tuple[AbortCondition, ...]
    rollback: tuple[str, ...]
    approved_by: tuple[str, ...]
    approved_at: datetime
    raw: dict[str, Any]

    def slo_for(self, metric: str) -> SteadyState | None:
        """The steady-state entry for a metric, if declared."""
        return next((s for s in self.steady_state if s.metric == metric), None)


class _PlanLoader(yaml.SafeLoader):
    """Safe YAML loader that keeps timestamps as strings and rejects aliases."""

    def compose_node(self, parent: yaml.Node | None, index: object) -> yaml.Node | None:
        """Refuse alias nodes so a small file cannot expand into a huge document."""
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("YAML aliases are not allowed in plans")
        return super().compose_node(parent, index)  # type: ignore[arg-type]


_PlanLoader.yaml_implicit_resolvers = {
    key: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def parse_yaml(text: str) -> object:
    """Parse YAML text with the restricted plan loader."""
    return yaml.load(text, Loader=_PlanLoader)  # noqa: S506 - _PlanLoader is a SafeLoader subclass


@cache
def schema() -> dict[str, Any]:
    """The packaged JSON Schema for plans."""
    text = resources.files("chaos_gamedays").joinpath("experiment.schema.json").read_text("utf-8")
    return cast("dict[str, Any]", json.loads(text))


def schema_problems(doc: object) -> list[str]:
    """Schema violations in a parsed document, as path: message lines."""
    validator = Draft202012Validator(schema())
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]


def _threshold(metric: str, d: dict[str, Any]) -> Threshold:
    """Threshold from a mapping with op and value."""
    return Threshold(metric, cast("Op", d["op"]), float(d["value"]))


def from_dict(doc: dict[str, Any], source: str = "<plan>") -> Experiment:
    """Typed experiment from a parsed document; raises PlanError on schema violations."""
    problems = schema_problems(doc)
    if problems:
        raise PlanError(source, problems)
    try:
        f, br, ld, ap = doc["fault"], doc["blast_radius"], doc["load"], doc["approval"]
        return Experiment(
            id=doc["id"],
            title=doc["title"],
            owner=doc["owner"],
            service=doc["service"],
            scheduled_start=parse_time(doc["scheduled_start"]),
            steady_state=tuple(
                SteadyState(s["metric"], s["description"], s["unit"], _threshold(s["metric"], s["slo"])) for s in doc["steady_state"]
            ),
            statement=doc["hypothesis"]["statement"],
            expectations=tuple(_threshold(e["metric"], e) for e in doc["hypothesis"]["expectations"]),
            fault=Fault(
                kind=f["kind"],
                namespace=f["target"]["namespace"],
                selector=dict(f["target"]["selector"]),
                replicas=f["target"]["replicas"],
                affected=f["affected"],
                duration_s=parse_duration(f["duration"]),
                duration_text=f["duration"],
                parameters=dict(f.get("parameters", {})),
            ),
            load_rps=ld["rps"],
            load_duration_s=parse_duration(ld["duration"]),
            load_duration_text=ld["duration"],
            blast_radius=BlastRadius(
                environment=br["environment"],
                namespaces=tuple(br["namespaces"]),
                max_pods_affected=br["max_pods_affected"],
                max_traffic_pct=br["max_traffic_pct"],
                max_duration_s=parse_duration(br["max_duration"]),
                max_duration_text=br["max_duration"],
                user_impact=br["user_impact"],
                dependencies=tuple(br.get("dependencies", [])),
            ),
            abort_conditions=tuple(
                AbortCondition(_threshold(a["metric"], a), parse_duration(a["for"]), a["for"]) for a in doc["abort_conditions"]
            ),
            rollback=tuple(doc["rollback"]),
            approved_by=tuple(ap["approved_by"]),
            approved_at=parse_time(ap["approved_at"]),
            raw=doc,
        )
    except ValueError as exc:
        raise PlanError(source, [str(exc)]) from exc


def write_atomic(path: Path, text: str) -> None:
    """Write to a sibling temp file and rename it over path, so a crash leaves the old or the new content."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path) -> Experiment:
    """Read, size-check, safely parse and validate one YAML plan."""
    if path.stat().st_size > MAX_PLAN_BYTES:
        raise PlanError(str(path), [f"file larger than {MAX_PLAN_BYTES} bytes"])
    try:
        doc = parse_yaml(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise PlanError(str(path), [f"not valid YAML: {exc}"]) from exc
    if not isinstance(doc, dict):
        raise PlanError(str(path), ["top level must be a mapping"])
    return from_dict(doc, str(path))
