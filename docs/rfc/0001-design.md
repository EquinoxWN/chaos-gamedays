# RFC 0001: chaos-gamedays design

- **Status:** Accepted (M1 built)
- **Author:** EquinoxWN
- **Created:** 2026-10-23

## Problem

Teams run chaos experiments ("game days") to learn whether a system survives failures before
customers find out. In practice many game days start with a vague goal ("let's kill a pod and see"),
no agreed limit on what may break and no rule for when to stop. The result is either an experiment
that proves nothing (no falsifiable claim) or one that causes a real incident (no blast radius, no
abort). The plan is also often edited during or after the run, so the claim is fitted to the outcome.

Users: platform and SRE teams who plan game days, and the service owners and approvers who sign
them off.

## Define: critical-to-quality measures

| CTQ | Target (M1) | How it is checked |
|---|---|---|
| Every plan has a falsifiable hypothesis | 100% of plans: at least one expectation on a declared steady-state metric, not looser than its SLO | JSON Schema + rules `hypothesis-metric-known`, `hypothesis-within-slo` |
| Blast radius written down before the start | 100% of plans: environment, namespaces, pod, traffic and time limits; fault fits inside them | Schema + `blast-namespaces`, `blast-pods`, `blast-duration`, `production-guard` |
| Abort conditions are executable, not prose | 100% of plans: at least one metric-based abort that watches a hypothesis metric, fires beyond the hypothesis threshold and within the fault window | `abort-metric-known`, `abort-covers-hypothesis`, `abort-beyond-hypothesis`, `abort-can-fire` |
| Plan fixed before the start | 0 plans edited after approval without detection | approval before start (`written-before-start`), SHA-256 seal in `experiments/plans.lock.json`, `gameday verify` in CI |
| Unsafe plans rejected | 100% of the rule cases in the test suite caught; the shipped unsafe example rejected | 26 rule tests, CI step that fails if the unsafe plan is accepted |

## Goals

- M1: each experiment starts with a machine-checked hypothesis; blast radius and abort conditions
  are written down, checked and sealed before starting; the hypothesis and abort conditions can be
  judged against a metric series.
- M2: Chaos Mesh injects the fault while k6 generates load; dashboards compare steady state with the
  fault, against the SLO.
- M3: a failed hypothesis becomes a blameless postmortem; the experiment is re-run after the fix.

## Non-goals

- Injecting faults in M1. M1 contains no Chaos Mesh objects and touches no cluster.
- A hosted service or UI. Plans are files in Git, reviewed in pull requests.
- Replacing Chaos Mesh's own scheduling or safety features; this layer decides whether a run may
  start and how it is judged.

## Proposed design

```text
experiments/*.yaml ──> JSON Schema ──> 11 safety rules ──> gameday seal ──> plans.lock.json
        │                                     │                                  │
        └──> gameday brief ──> docs/gamedays/GD-NNN.md (pre-flight brief)        └──> gameday verify (CI)
recorded metric series ──> gameday evaluate ──> held | refuted | aborted | inconclusive
```

- **Plan format** (`gameday/v1`, YAML): steady-state metrics with SLOs, hypothesis statement plus
  structured expectations, fault (kind, namespace, selector, replicas, affected pods, duration), k6
  load, blast radius, abort conditions (metric, operator, threshold, sustain window), rollback steps,
  approval. The JSON Schema is packaged with the code and can be used by editors.
- **Parsing:** a restricted `SafeLoader` that keeps timestamps as strings and refuses YAML aliases
  (no alias expansion), plus a 256 KiB size limit. Durations are bounded to 24 h.
- **Rules** (`rules.py`): eleven pure functions, each with an id that appears in every finding.
- **Seal** (`sealing.py`): SHA-256 of the canonical JSON of the plan, recorded with the seal time;
  sealing is refused at or after the scheduled start, and a changed plan needs `--reseal` (meaning a
  new approval). `verify` reports unsealed plans, edits after sealing, late seals and stale entries.
- **Evaluate** (`evaluate.py`): steady state must hold on all samples before the fault (otherwise
  inconclusive); the earliest abort condition that holds continuously for its window stops the run
  (aborted); otherwise every sample in the fault window must meet every expectation (held or
  refuted).
- **Brief** (`brief.py`): a Markdown brief per plan, committed to `docs/gamedays/`; CI fails if a
  brief is stale.

## Alternatives considered

| Option | Why not (yet) |
|---|---|
| Free-form Markdown hypothesis template only | Easy to write, but nothing checks it: an abort threshold tighter than the hypothesis or a fault touching every replica passes review unnoticed. A schema plus rules makes those mistakes fail CI (poka-yoke). |
| Chaos Mesh `Workflow` / `Schedule` objects as the plan | Chaos Mesh describes how to inject, not the hypothesis, SLO or approval. Abort on SLO metrics would need extra controllers. Kept for M2 as the injection layer, generated from the plan. |
| Open Policy Agent (Rego) for the rules | Strong for admission control, but the rules compare thresholds across sections and need typed durations; Python keeps the rules, the evaluator and the tests in one typed codebase. ADR 0002 records this. |
| LitmusChaos probes with built-in pass/fail | Probes judge one check during a run, but do not cover pre-start approval, blast radius limits or sealing. |

## Measurement plan

- **Measure:** run the suite and the CLI on this machine; record the number of rules, rule cases
  caught, plans validated and sealed, and verdicts on the demo series (`docs/results/m1.md`).
- **Analyze:** any rule case that passes when it should fail is a defect; record its root cause.
- **Control:** CI runs lint, mypy `--strict`, the tests, `gameday validate`, `brief --check` and
  `verify`, fails if the unsafe example is accepted, and runs pip-audit.
- M2/M3 measure real fault runs (p99, error rate under fault) against the plans' hypotheses.

## Milestones

- **M1:** hypothesis per experiment; blast radius and abort conditions written down, checked and
  sealed before starting.
- **M2:** Chaos Mesh injects the fault under k6 load; dashboards compare steady state and fault
  against the SLO.
- **M3:** blameless postmortems for failed hypotheses; re-run after the fix; three postmortems in
  the README.

## Risks and open questions

- The evaluator judges every sample in the fault window; a single noisy scrape refutes the
  hypothesis. M2 may need percentile-over-window expectations once real data shows the noise.
- Metric names in plans must match the Prometheus queries added in M2; a mapping layer is open.
- The seal proves a plan did not change after sealing, not who approved it; signed commits or
  pull request approvals cover identity.
