"""The gameday CLI end to end on the shipped plans, briefs, lock file and demo series."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from chaos_gamedays.cli import main
from conftest import BRIEFS, EXPERIMENTS, GD001, LOCK, SERIES, UNSAFE


def test_validate_shipped_plans(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(EXPERIMENTS)]) == 0
    out = capsys.readouterr().out
    assert out.count("OK   ") == 3
    assert "3 plan(s), 0 finding(s)" in out


def test_validate_rejects_unsafe_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(UNSAFE)]) == 1
    out = capsys.readouterr().out
    assert "1 plan(s), 14 finding(s)" in out
    assert "blast-pods: fault affects 3 of 3 replicas: none would survive" in out


def test_validate_reports_schema_errors_and_duplicates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "broken.yaml").write_text("apiVersion: gameday/v1\nid: GD-1\n", encoding="utf-8")
    shutil.copy(GD001, tmp_path / "a.yaml")
    shutil.copy(GD001, tmp_path / "b.yaml")
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "schema: <root>: 'hypothesis' is a required property" in out
    assert "unique-id: duplicate experiment id GD-001" in out
    assert "3 plan(s)" in out


def test_briefs_are_current() -> None:
    assert main(["brief", str(EXPERIMENTS), "--out-dir", str(BRIEFS), "--check"]) == 0


def test_brief_check_detects_stale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["brief", str(GD001), "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "GD-001.md").read_text(encoding="utf-8").startswith("# GD-001: One Valkey replica dies")
    (tmp_path / "GD-001.md").write_text("edited by hand\n", encoding="utf-8")
    assert main(["brief", str(GD001), "--out-dir", str(tmp_path), "--check"]) == 1
    assert "stale brief" in capsys.readouterr().err


def test_brief_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["brief", str(GD001)]) == 0
    out = capsys.readouterr().out
    assert "> If one Valkey replica dies, checkout p99 stays under 300 ms" in out
    assert "- `checkout_error_rate_pct > 5` for 30s" in out
    assert "| Affected | 1 of 3 pods |" in out


def test_verify_shipped_lock() -> None:
    assert main(["verify", str(EXPERIMENTS), "--lock", str(LOCK)]) == 0


def test_seal_then_edit_then_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan, lock = tmp_path / "gd-001.yaml", tmp_path / "plans.lock.json"
    shutil.copy(GD001, plan)
    assert main(["seal", str(plan), "--lock", str(lock), "--now", "2026-10-30T12:00:00Z"]) == 0
    assert main(["verify", str(plan), "--lock", str(lock)]) == 0
    plan.write_text(plan.read_text(encoding="utf-8").replace("affected: 1", "affected: 2"), encoding="utf-8")
    assert main(["verify", str(plan), "--lock", str(lock)]) == 1
    assert "plan changed after it was sealed" in capsys.readouterr().out


def test_seal_refuses_unsafe_and_late(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lock = tmp_path / "plans.lock.json"
    assert main(["seal", str(UNSAFE), "--lock", str(lock), "--now", "2026-10-30T12:00:00Z"]) == 1
    assert main(["seal", str(GD001), "--lock", str(lock), "--now", "2026-11-05T14:00:00Z"]) == 1
    err = capsys.readouterr().err
    assert "written-before-start" in err
    assert "cannot seal at or after the scheduled start" in err
    assert json.loads(lock.read_text(encoding="utf-8"))["plans"] == {}


@pytest.mark.parametrize(("name", "code"), [("held", 0), ("refuted", 3), ("aborted", 4)])
def test_evaluate_exit_codes(name: str, code: int, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["evaluate", str(GD001), "--series", str(SERIES / f"gd-001-{name}.json"), "--json"]) == code
    assert json.loads(capsys.readouterr().out)["verdict"] == name


def test_usage_errors_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(tmp_path / "missing.yaml")]) == 2
    bad = tmp_path / "series.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["evaluate", str(GD001), "--series", str(bad)]) == 2
    assert capsys.readouterr().err.count("error:") == 2


def test_seal_twice_is_idempotent(tmp_path: Path) -> None:
    lock = tmp_path / "plans.lock.json"
    assert main(["seal", str(GD001), "--lock", str(lock), "--now", "2026-10-30T12:00:00Z"]) == 0
    first = lock.read_text(encoding="utf-8")
    assert main(["seal", str(GD001), "--lock", str(lock), "--now", "2026-10-31T12:00:00Z"]) == 0
    assert lock.read_text(encoding="utf-8") == first
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plans.lock.json"]


def test_naive_now_and_negative_max_breaches_are_usage_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["seal", str(GD001), "--lock", str(tmp_path / "l.json"), "--now", "2026-10-30T12:00:00"]) == 2
    held = str(SERIES / "gd-001-held.json")
    assert main(["evaluate", str(GD001), "--series", held, "--max-breaches", "-1"]) == 2
    err = capsys.readouterr().err
    assert "no time zone" in err
    assert "--max-breaches must be >= 0" in err
