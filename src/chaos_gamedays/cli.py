"""gameday command line: validate, brief, seal, verify and evaluate experiment plans."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from . import brief, evaluate, rules, sealing
from .model import Experiment, PlanError, load, parse_time, write_atomic

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
VERDICT_EXIT = {"held": 0, "refuted": 3, "aborted": 4, "inconclusive": 5}
MAX_PLANS = 500


def plan_paths(items: Sequence[str]) -> list[Path]:
    """Expand files and directories (their *.yaml and *.yml files) into a sorted list."""
    out: list[Path] = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            out += sorted([*p.glob("*.yaml"), *p.glob("*.yml")])
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"no such file or directory: {item}")
    if len(out) > MAX_PLANS:
        raise ValueError(f"more than {MAX_PLANS} plans")
    return out


def load_all(items: Sequence[str]) -> tuple[list[tuple[Path, Experiment]], list[str]]:
    """Load every plan; return the good ones and one line per unreadable or schema-invalid plan."""
    good: list[tuple[Path, Experiment]] = []
    bad: list[str] = []
    for p in plan_paths(items):
        try:
            good.append((p, load(p)))
        except PlanError as exc:
            bad += [f"FAIL {p.as_posix()} schema: {m}" for m in exc.problems]
    return good, bad


def cmd_validate(args: argparse.Namespace) -> int:
    """Schema plus safety rules for every plan; non-zero exit on any finding."""
    good, bad = load_all(args.paths)
    lines = list(bad)
    for p, e in good:
        found = rules.check(e)
        lines += [f"FAIL {p.as_posix()} {e.id} {f.rule}: {f.message}" for f in found]
        if not found:
            lines.append(
                f"OK   {p.as_posix()} {e.id}: {len(e.expectations)} expectation(s), {len(e.abort_conditions)} abort condition(s), blast radius {e.blast_radius.environment}/{','.join(e.blast_radius.namespaces)}"
            )
    lines += [f"FAIL {f.rule}: {f.message}" for f in rules.check_unique_ids(e for _, e in good)]
    failures = sum(1 for line in lines if line.startswith("FAIL"))
    print("\n".join(lines))
    print(f"{len(good) + len({ln.split()[1] for ln in bad})} plan(s), {failures} finding(s)")
    return EXIT_FAIL if failures else EXIT_OK


def cmd_brief(args: argparse.Namespace) -> int:
    """Write (or with --check, compare) the Markdown brief for each plan."""
    good, bad = load_all(args.paths)
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return EXIT_FAIL
    if args.out_dir is None:
        for _, e in good:
            sys.stdout.write(brief.render(e))
        return EXIT_OK
    out_dir = Path(args.out_dir)
    stale: list[str] = []
    for _, e in good:
        target, text = out_dir / f"{e.id}.md", brief.render(e)
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if args.check:
            if current != text:
                stale.append(target.as_posix())
        elif current != text:
            out_dir.mkdir(parents=True, exist_ok=True)
            write_atomic(target, text)
            print(f"wrote {target.as_posix()}")
    if stale:
        print("\n".join(f"stale brief: {s} (run gameday brief --out-dir)" for s in stale), file=sys.stderr)
        return EXIT_FAIL
    print(f"{len(good)} brief(s) current")
    return EXIT_OK


def _now(arg: str | None) -> datetime:
    """The --now override or the current UTC time."""
    return parse_time(arg) if arg else datetime.now(UTC).replace(microsecond=0)


def cmd_seal(args: argparse.Namespace) -> int:
    """Seal plans that pass every rule into the lock file."""
    good, bad = load_all(args.paths)
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return EXIT_FAIL
    lock = Path(args.lock)
    seals, now, errors = sealing.read_lock(lock), _now(args.now), []
    for p, e in good:
        found = rules.check(e)
        if found:
            errors += [f"{p.as_posix()} {e.id} {f.rule}: {f.message}" for f in found]
            continue
        try:
            s = sealing.seal(e, seals, now, reseal=args.reseal)
            print(f"sealed {e.id} sha256={s.sha256[:16]} at {s.sealed_at:%Y-%m-%dT%H:%M:%SZ}")
        except ValueError as exc:
            errors.append(str(exc))
    sealing.write_lock(lock, seals)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Check every plan is sealed, unchanged since sealing, and was sealed before its start."""
    good, bad = load_all(args.paths)
    problems = bad + sealing.verify((e for _, e in good), sealing.read_lock(Path(args.lock)))
    for line in problems:
        print(f"FAIL {line}")
    print(f"{len(good)} plan(s) checked against {Path(args.lock).as_posix()}, {len(problems)} problem(s)")
    return EXIT_FAIL if problems else EXIT_OK


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Judge one plan's hypothesis against a recorded series; exit code encodes the verdict."""
    if args.max_breaches < 0:
        raise ValueError("--max-breaches must be >= 0")
    e = load(Path(args.plan))
    result = evaluate.evaluate(e, evaluate.load_series(Path(args.series)))
    if args.json:
        print(json.dumps({"experiment": e.id, **result.as_dict()}, indent=2))
    else:
        print(f"{e.id}: {result.verdict.upper()}")
        print("\n".join(f"  {r}" for r in result.reasons + result.breaches[: args.max_breaches]))
        if len(result.breaches) > args.max_breaches:
            print(f"  ... {len(result.breaches) - args.max_breaches} more breach(es)")
    return VERDICT_EXIT[result.verdict]


def parser() -> argparse.ArgumentParser:
    """Argument parser for all subcommands."""
    ap = argparse.ArgumentParser(prog="gameday", description="Write down, check and judge chaos experiments before and after a game day.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="check plans against the schema and safety rules")
    v.add_argument("paths", nargs="+")
    v.set_defaults(func=cmd_validate)
    b = sub.add_parser("brief", help="render the Markdown pre-flight brief")
    b.add_argument("paths", nargs="+")
    b.add_argument("--out-dir")
    b.add_argument("--check", action="store_true", help="fail if a written brief is missing or stale")
    b.set_defaults(func=cmd_brief)
    s = sub.add_parser("seal", help="record plan digests in the lock file before the start")
    s.add_argument("paths", nargs="+")
    s.add_argument("--lock", required=True)
    s.add_argument("--reseal", action="store_true", help="replace a seal after re-approval")
    s.add_argument("--now", help="override the current time (ISO 8601, Z)")
    s.set_defaults(func=cmd_seal)
    c = sub.add_parser("verify", help="check plans are sealed and unchanged")
    c.add_argument("paths", nargs="+")
    c.add_argument("--lock", required=True)
    c.set_defaults(func=cmd_verify)
    e = sub.add_parser("evaluate", help="judge a hypothesis against a recorded metric series")
    e.add_argument("plan")
    e.add_argument("--series", required=True)
    e.add_argument("--json", action="store_true")
    e.add_argument("--max-breaches", type=int, default=5)
    e.set_defaults(func=cmd_evaluate)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; usage, file and parse errors exit with code 2."""
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
