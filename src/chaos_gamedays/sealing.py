"""Seal approved plans in a lock file so any edit after approval, or a late seal, is detected."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import Experiment, parse_time

LOCK_VERSION = 1
MAX_LOCK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Seal:
    """Digest of a plan and when it was sealed."""

    sha256: str
    sealed_at: datetime


def digest(e: Experiment) -> str:
    """SHA-256 of the plan's canonical JSON form (key order and whitespace do not matter)."""
    canonical = json.dumps(e.raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_lock(path: Path) -> dict[str, Seal]:
    """Seals from a lock file; an absent file means no seals."""
    if not path.exists():
        return {}
    if path.stat().st_size > MAX_LOCK_BYTES:
        raise ValueError(f"{path}: lock file larger than {MAX_LOCK_BYTES} bytes")
    doc: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("version") != LOCK_VERSION or not isinstance(doc.get("plans"), dict):
        raise ValueError(f"{path}: not a version {LOCK_VERSION} lock file")
    out: dict[str, Seal] = {}
    for pid, entry in doc["plans"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("sealed_at"), str):
            raise ValueError(f"{path}: bad entry for {pid}")
        out[str(pid)] = Seal(entry["sha256"], parse_time(entry["sealed_at"]))
    return out


def write_lock(path: Path, seals: dict[str, Seal]) -> None:
    """Write seals sorted by id, one stable JSON document."""
    plans = {pid: {"sha256": s.sha256, "sealed_at": s.sealed_at.strftime("%Y-%m-%dT%H:%M:%SZ")} for pid, s in sorted(seals.items())}
    path.write_text(json.dumps({"version": LOCK_VERSION, "plans": plans}, indent=2) + "\n", encoding="utf-8")


def seal(e: Experiment, seals: dict[str, Seal], now: datetime, *, reseal: bool = False) -> Seal:
    """Seal one plan at time now; refuses after the start or over a different seal unless resealing."""
    if now >= e.scheduled_start:
        raise ValueError(f"{e.id}: cannot seal at or after the scheduled start {e.scheduled_start:%Y-%m-%dT%H:%MZ}")
    d = digest(e)
    old = seals.get(e.id)
    if old is not None and old.sha256 == d:
        return old
    if old is not None and not reseal:
        raise ValueError(f"{e.id}: plan changed since it was sealed; get it re-approved and pass --reseal")
    new = Seal(d, now)
    seals[e.id] = new
    return new


def verify(plans: Iterable[Experiment], seals: dict[str, Seal]) -> list[str]:
    """Problems: unsealed plans, plans edited after sealing, late seals and stale lock entries."""
    out: list[str] = []
    ids: set[str] = set()
    for e in plans:
        ids.add(e.id)
        s = seals.get(e.id)
        if s is None:
            out.append(f"{e.id}: not sealed")
        elif s.sha256 != digest(e):
            out.append(f"{e.id}: plan changed after it was sealed")
        elif s.sealed_at >= e.scheduled_start:
            out.append(f"{e.id}: sealed at or after its scheduled start")
    out += [f"{pid}: lock entry has no plan" for pid in sorted(set(seals) - ids)]
    return out
