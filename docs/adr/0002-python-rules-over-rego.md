# ADR 0002: Plan rules in typed Python with a JSON Schema, not Rego

- **Status:** Accepted

## Context

Plans must be rejected before a game day if they are unsafe: an abort threshold that would stop
the run before the hypothesis can be judged, a fault that touches every replica, a target outside
the blast radius, approval after the start. Two options were weighed: Open Policy Agent policies
(Rego, run with conftest) over the YAML, or a JSON Schema for structure plus Python rules.

## Decision

Structure is checked by a packaged JSON Schema (Draft 2020-12). Cross-field safety rules are pure
Python functions over typed dataclasses, checked by mypy `--strict`, each with a rule id. The same
model is used by the evaluator that judges a hypothesis against a metric series.

## Consequences

- Positive: one typed model for validation, sealing, briefs and evaluation; durations and
  thresholds are parsed once; each rule has unit tests that mutate one field of a valid plan.
- Positive: the JSON Schema gives editor completion and can be reused by other tools.
- Negative: the rules cannot be used as a Kubernetes admission policy as they stand. If M2 needs
  in-cluster enforcement (for example blocking Chaos Mesh objects outside the blast radius), a
  small Rego or Kyverno policy will be added for that narrow case, generated from the same plan.
- Negative: contributors need Python to change rules.
