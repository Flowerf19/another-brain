"""Harness validation driver + allowed-outcome oracle (TASK-007).

Runs the four accepted-workload scenarios against the toy adapter and asserts
the locked post-conditions. Run from the repo root:

    uv run python benchmarks/concurrency/run_harness.py [--quick]

--quick uses reduced seeds/ops (CI-friendly); the full locked parameters run
in TASK-055 against the real repository.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.integration.concurrency import harness, toy_adapter  # noqa: E402

ADAPTER = "tests.integration.concurrency.toy_adapter"


class Oracle:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, cond: bool, message: str) -> None:
        if not cond:
            self.failures.append(message)
            print(f"  FAIL {message}")
        else:
            print(f"  ok   {message}")


def scenario_fresh_open(oracle: Oracle, work: Path, seed: int) -> None:
    print(f"fresh-open storm (seed {seed})")
    cfg = harness.WorkloadConfig(
        db_path=work / "storm.sqlite3", log_dir=work / "logs", seed=seed,
        ops_per_worker=0,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    harness.fresh_open_storm(cfg, ADAPTER, workers=8)
    elapsed = time.monotonic() - started
    events = harness.merge_logs(cfg.log_dir)
    opens = [e for e in events if e.get("event") == "open_ok"]
    fatals = [e for e in events if e.get("event") == "fatal"]
    report = toy_adapter.integrity_report(cfg.db_path)
    oracle.check(len(opens) == 8, f"8/8 processes opened the database ({len(opens)})")
    oracle.check(not fatals, f"no fatal events ({fatals[:1]})")
    oracle.check(elapsed < 30, f"storm finished in {elapsed:.1f}s (<30s)")
    oracle.check(report["migrations"] == [1], f"exactly one schema version {report['migrations']}")
    oracle.check(report["integrity_check"] == "ok", "integrity_check ok")


def scenario_mixed(oracle: Oracle, work: Path, seed: int, ops: int) -> None:
    print(f"mixed WAL workload (seed {seed}, {ops} ops/worker)")
    cfg = harness.WorkloadConfig(
        db_path=work / "mixed.sqlite3", log_dir=work / "logs", seed=seed,
        ops_per_worker=ops,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    toy_adapter.preseed(cfg.db_path, cfg.preseed, cfg.hot_ids)
    harness.mixed_workload(cfg, ADAPTER)
    events = harness.merge_logs(cfg.log_dir)
    errors = [e for e in events if e.get("event") == "error"]
    fatals = [e for e in events if e.get("event") == "fatal"]
    dones = [e for e in events if e.get("event") == "done"]
    report = toy_adapter.integrity_report(cfg.db_path)
    oracle.check(len(dones) == cfg.workers, f"{cfg.workers}/{cfg.workers} workers completed ({len(dones)})")
    oracle.check(not fatals, f"no fatal events ({fatals[:1]})")
    oracle.check(not errors, f"zero unhandled errors ({errors[:1]})")
    oracle.check(report["integrity_check"] == "ok", "integrity_check ok")
    oracle.check(report["fts_parity"], "FTS trigger parity with memories rows")
    oracle.check(report["migrations"] == [1], "migrations not duplicated")


def scenario_crash(oracle: Oracle, work: Path, seed: int, ops: int) -> None:
    print(f"crash probe (seed {seed})")
    cfg = harness.WorkloadConfig(
        db_path=work / "crash.sqlite3", log_dir=work / "logs", seed=seed,
        ops_per_worker=ops, crash_inject=True,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    toy_adapter.preseed(cfg.db_path, cfg.preseed, cfg.hot_ids)
    harness.mixed_workload(cfg, ADAPTER)
    events = harness.merge_logs(cfg.log_dir)
    crashes = [e for e in events if e.get("event") == "crash_point"]
    oracle.check(len(crashes) == 1, "injected crash fired exactly once")
    # reopen + continue a second workload on the same file
    report = toy_adapter.integrity_report(cfg.db_path)
    oracle.check(report["integrity_check"] == "ok", "integrity_check ok after crash")
    cfg2 = harness.WorkloadConfig(
        db_path=cfg.db_path, log_dir=work / "logs2", seed=seed + 1,
        ops_per_worker=ops, crash_inject=False,
    )
    cfg2.log_dir.mkdir(parents=True, exist_ok=True)
    harness.mixed_workload(cfg2, ADAPTER)
    events2 = harness.merge_logs(cfg2.log_dir)
    errors = [e for e in events2 if e.get("event") in ("error", "fatal")]
    oracle.check(not errors, f"post-crash workload clean ({errors[:1]})")


def scenario_busy(oracle: Oracle, work: Path, seed: int) -> None:
    print("busy-exhaustion probe")
    db = work / "busy.sqlite3"
    toy_adapter.preseed(db, 10, 1)
    holder = sqlite3.connect(db, timeout=0.2)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO memories(memory_id, brain_id, summary, importance,"
                   " created_at_ms, expires_at_ms) VALUES ('holder', 'toy-brain', 'x', 3, 1, 1)")
    cfg = harness.WorkloadConfig(
        db_path=db, log_dir=work / "logs", seed=seed, workers=1, writers=1,
        ops_per_worker=3, join_timeout_s=20.0,
        busy_timeout_s=0.2, retry_attempts=2, retry_base_s=0.05,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    harness.mixed_workload(cfg, ADAPTER)
    elapsed = time.monotonic() - started
    holder.rollback()
    holder.close()
    events = harness.merge_logs(cfg.log_dir)
    exhausted = [e for e in events if e.get("event") == "busy_exhausted"]
    oracle.check(bool(exhausted), "typed busy_exhausted surfaced (no hang)")
    oracle.check(elapsed < 15, f"probe bounded in {elapsed:.1f}s")
    oracle.check(
        toy_adapter.integrity_report(db)["integrity_check"] == "ok",
        "integrity_check ok after probe",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    seeds = [20260804, 20260805] if args.quick else [20260804 + i for i in range(5)]
    ops = 60 if args.quick else 500

    oracle = Oracle()
    work = Path(tempfile.mkdtemp(prefix="ab-harness-"))
    try:
        for i, seed in enumerate(seeds):
            scenario_fresh_open(oracle, work / f"storm-{i}", seed)
        for i, seed in enumerate(seeds):
            scenario_mixed(oracle, work / f"mixed-{i}", seed, ops)
        scenario_crash(oracle, work / "crash", seeds[0], ops)
        scenario_busy(oracle, work / "busy", seeds[0])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if oracle.failures:
        print(f"\nHARNESS FAIL: {len(oracle.failures)} violation(s)")
        return 1
    print("\nHARNESS PASS: driver, barriers, seeds, crash + lock injection validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
