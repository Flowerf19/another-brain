"""TASK-055 driver: accepted concurrency workload against the real repository.

Runs the four locked scenarios (Plan 07 "Accepted concurrency workload")
through ``repository_adapter`` — the production factory/migrations/repository
with locked retry constants — and asserts the full post-condition set:
timeouts, typed busy-exhausted error, allowed races, migration uniqueness,
restart read/write, ``integrity_check``/``foreign_key_check``, all-row FTS
trigger parity plus live filtering, and resource closure (every worker logs
``done`` or the single injected ``crash_point``; no process outlives the
join envelope).

    uv run python benchmarks/concurrency/run_repository.py [--quick]

``--quick`` (2 seeds, 60 ops/worker) is the CI gate; the full locked
parameters (5 seeds, 500 ops/worker, both vector modes) run for evidence.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.integration.concurrency import harness, repository_adapter  # noqa: E402
from tests.integration.concurrency.run_harness import Oracle  # noqa: E402

ADAPTER = "tests.integration.concurrency.repository_adapter"
VEC_MODES = ("extension", "numpy")

ALLOWED_RESULTS = {
    "remember": {"remembered", "duplicate"},
    "reinforce": {"reinforced", "not_found"},
    "forget": {"forgotten", "not_found"},
    "restore": {"restored", "not_found"},
    "get": {"hit", "not_found"},
    "recent": {"listed"},
    "search": {"searched"},
}


def check_allowed_outcomes(oracle: Oracle, events: list[dict], workers: int) -> None:
    dones = [e for e in events if e.get("event") == "done"]
    fatals = [e for e in events if e.get("event") == "fatal"]
    errors = [e for e in events if e.get("event") == "error"]
    busy = [e for e in events if e.get("event") == "busy_exhausted"]
    oracle.check(len(dones) == workers, f"{workers}/{workers} workers completed ({len(dones)})")
    oracle.check(not fatals, f"no fatal events ({fatals[:1]})")
    oracle.check(not errors, f"zero unhandled errors ({errors[:1]})")
    oracle.check(not busy, f"zero unhandled busy exhaustion in mixed workload ({busy[:1]})")
    bad = [
        e for e in events
        if e.get("event") == "op" and e.get("result") not in ALLOWED_RESULTS[e["op"]]
    ]
    oracle.check(not bad, f"every op outcome is an allowed serializable race ({bad[:1]})")


def check_db_health(oracle: Oracle, db_path: Path, *, expect_profile: bool) -> None:
    report = repository_adapter.integrity_report(db_path)
    oracle.check(report["integrity_check"] == "ok", "integrity_check ok")
    oracle.check(report["foreign_key_violations"] == 0, "foreign_key_check empty")
    oracle.check(report["ledger_ok"], f"migration ledger exactly v1+checksum ({report['ledger']})")
    oracle.check(
        report["fts_parity"],
        f"FTS trigger parity on all rows (memories={report['memories']},"
        f" fts={report['fts_rows']})",
    )
    oracle.check(report["live_filter_ok"], "filtered search excludes non-live rows")
    if expect_profile:
        oracle.check(report["profiles"] == 1, "exactly one embedding profile row")


def scenario_fresh_open(oracle: Oracle, work: Path, seed: int, mode: str) -> None:
    print(f"fresh-open storm (seed {seed}, {mode} mode)")
    os.environ[repository_adapter.VEC_MODE_ENV] = mode
    cfg = harness.WorkloadConfig(
        db_path=work / "storm.sqlite3", log_dir=work / "logs", seed=seed, ops_per_worker=0,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    harness.fresh_open_storm(cfg, ADAPTER, workers=8)
    elapsed = time.monotonic() - started
    events = harness.merge_logs(cfg.log_dir)
    opens = [e for e in events if e.get("event") == "open_ok"]
    fatals = [e for e in events if e.get("event") == "fatal"]
    oracle.check(len(opens) == 8, f"8/8 processes opened the database ({len(opens)})")
    oracle.check(not fatals, f"no fatal events ({fatals[:1]})")
    oracle.check(elapsed < 30, f"storm finished in {elapsed:.1f}s (<30s)")
    check_db_health(oracle, cfg.db_path, expect_profile=False)


def scenario_mixed(oracle: Oracle, work: Path, seed: int, ops: int, mode: str) -> None:
    print(f"mixed WAL workload (seed {seed}, {ops} ops/worker, {mode} mode)")
    os.environ[repository_adapter.VEC_MODE_ENV] = mode
    cfg = harness.WorkloadConfig(
        db_path=work / "mixed.sqlite3", log_dir=work / "logs", seed=seed, ops_per_worker=ops,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    repository_adapter.preseed(cfg.db_path, cfg.preseed, cfg.hot_ids)
    harness.mixed_workload(cfg, ADAPTER)
    events = harness.merge_logs(cfg.log_dir)
    check_allowed_outcomes(oracle, events, cfg.workers)
    remembered = {
        e["memory_id"] for e in events
        if e.get("event") == "op" and e.get("op") == "remember"
        and e.get("result") == "remembered"
    }
    check_db_health(oracle, cfg.db_path, expect_profile=True)
    report = repository_adapter.integrity_report(cfg.db_path)
    present = repository_adapter.sqlite3.connect(
        f"file:{cfg.db_path}?mode=ro", uri=True
    )
    try:
        missing = [
            mid for mid in remembered
            if present.execute(
                "SELECT COUNT(*) FROM memories WHERE brain_id = ? AND memory_id = ?",
                (repository_adapter.BRAIN_ID, mid),
            ).fetchone()[0] != 1
        ]
    finally:
        present.close()
    oracle.check(not missing, f"acknowledged remembers unique and present ({missing[:1]})")
    oracle.check(
        repository_adapter.restart_probe(cfg.db_path),
        "restart can read/write after the run",
    )
    del report


def scenario_crash(oracle: Oracle, work: Path, seed: int, ops: int) -> None:
    print(f"crash probe (seed {seed})")
    os.environ[repository_adapter.VEC_MODE_ENV] = "extension"
    cfg = harness.WorkloadConfig(
        db_path=work / "crash.sqlite3", log_dir=work / "logs", seed=seed,
        ops_per_worker=ops, crash_inject=True,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    repository_adapter.preseed(cfg.db_path, cfg.preseed, cfg.hot_ids)
    harness.mixed_workload(cfg, ADAPTER)
    events = harness.merge_logs(cfg.log_dir)
    crashes = [e for e in events if e.get("event") == "crash_point"]
    oracle.check(len(crashes) == 1, "injected crash fired exactly once")
    survivors = [e for e in events if e.get("event") == "done"]
    oracle.check(
        len(survivors) == cfg.workers - 1,
        f"{cfg.workers - 1} survivors completed, victim closed by SIGKILL ({len(survivors)})",
    )
    check_db_health(oracle, cfg.db_path, expect_profile=True)
    # reopen + complete another mixed workload on the same file
    cfg2 = harness.WorkloadConfig(
        db_path=cfg.db_path, log_dir=work / "logs2", seed=seed + 1,
        ops_per_worker=ops, crash_inject=False,
    )
    cfg2.log_dir.mkdir(parents=True, exist_ok=True)
    harness.mixed_workload(cfg2, ADAPTER)
    events2 = harness.merge_logs(cfg2.log_dir)
    check_allowed_outcomes(oracle, events2, cfg2.workers)
    check_db_health(oracle, cfg2.db_path, expect_profile=True)


def scenario_busy(oracle: Oracle, work: Path, seed: int) -> None:
    print("busy-exhaustion probe (locked envelope: 5 attempts x 5s busy_timeout)")
    db = work / "busy.sqlite3"
    repository_adapter.preseed(db, 10, 1)
    holder = sqlite3.connect(db, timeout=0.2)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO audit_events(event_id, brain_id, memory_id, agent_id, action,"
        " event_at_ms, timeline_day, detail_json)"
        " VALUES ('busy-holder', 'conc-brain', 'hot-0001', 'conc-agent',"
        " 'remember', 1, '2026-01-01', '{}')"
    )
    cfg = harness.WorkloadConfig(
        db_path=db, log_dir=work / "logs", seed=seed, workers=1, writers=1,
        ops_per_worker=1, join_timeout_s=90.0,
    )
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    harness.mixed_workload(cfg, ADAPTER)
    elapsed = time.monotonic() - started
    holder.rollback()
    holder.close()
    events = harness.merge_logs(cfg.log_dir)
    exhausted = [e for e in events if e.get("event") == "busy_exhausted"]
    errors = [e for e in events if e.get("event") in ("error", "fatal")]
    oracle.check(bool(exhausted), "typed BusyExhausted surfaced (no hang)")
    # The event is only emitted from `except adapter.BusyExhausted`, and the
    # production retry.py message proves it is not the toy adapter's error.
    oracle.check(
        bool(exhausted) and "write busy after 5 attempts" in exhausted[0]["detail"],
        f"error is the production typed BusyExhausted ({exhausted[:1]})",
    )
    oracle.check(not errors, f"no other worker errors ({errors[:1]})")
    oracle.check(elapsed < 60, f"probe bounded in {elapsed:.1f}s (<60s)")
    check_db_health(oracle, db, expect_profile=True)
    oracle.check(
        repository_adapter.restart_probe(db),
        "write path recovers once the external lock is released",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    seeds = [20260804, 20260805] if args.quick else [20260804 + i for i in range(5)]
    ops = 60 if args.quick else 500

    oracle = Oracle()
    work = Path(tempfile.mkdtemp(prefix="ab-repo-harness-"))
    try:
        for i, seed in enumerate(seeds):
            scenario_fresh_open(oracle, work / f"storm-{i}", seed, VEC_MODES[i % 2])
        i = 0
        for seed in seeds:
            for mode in VEC_MODES:
                scenario_mixed(oracle, work / f"mixed-{i}", seed, ops, mode)
                i += 1
        scenario_crash(oracle, work / "crash", seeds[0], ops)
        scenario_busy(oracle, work / "busy", seeds[0])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if oracle.failures:
        print(f"\nREPOSITORY WORKLOAD FAIL: {len(oracle.failures)} violation(s)")
        return 1
    print("\nREPOSITORY WORKLOAD PASS: accepted workload holds on the real repository")
    return 0


if __name__ == "__main__":
    sys.exit(main())
