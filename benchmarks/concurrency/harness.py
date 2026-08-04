"""Reusable spawned-process concurrency workload harness (TASK-007).

Validates the driver, barriers, seeds, crash injection, and lock injection
against a throwaway SQLite toy store (toy_adapter.py) before TASK-055 applies
the same harness to the real repository.

Workload (Plan 07 accepted concurrency workload):
- fresh-open storm: N processes barrier-sync and open one absent database;
- mixed WAL workload: preseeded rows, writers (40% remember / 25% reinforce /
  20% forget / 15% restore) and readers (60% search / 25% recent / 15% get)
  over a shared hot-ID pool;
- crash probe: kill one writer at an injected post-BEGIN barrier;
- busy-exhaustion probe: an external write lock held longer than the retry
  envelope must surface the typed busy-exhausted error, not a hang.

Every worker appends structured events to a JSONL log; the driver merges logs
and checks the allowed-outcome oracle (see run_harness.py).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import random
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

WRITER_MIX = [("remember", 40), ("reinforce", 25), ("forget", 20), ("restore", 15)]
READER_MIX = [("search", 60), ("recent", 25), ("get", 15)]


@dataclass
class WorkloadConfig:
    db_path: Path
    log_dir: Path
    seed: int
    workers: int = 4
    writers: int = 2
    ops_per_worker: int = 500
    hot_ids: int = 50
    preseed: int = 500
    crash_inject: bool = False
    barrier_timeout_s: float = 30.0
    join_timeout_s: float = 300.0
    busy_timeout_s: float = 5.0
    retry_attempts: int = 5
    retry_base_s: float = 0.05


def pick_op(rng: random.Random, mix: list[tuple[str, int]]) -> str:
    roll = rng.uniform(0, sum(w for _, w in mix))
    for op, weight in mix:
        roll -= weight
        if roll <= 0:
            return op
    return mix[0][0]


def hot_id(rng: random.Random, cfg: WorkloadConfig) -> str:
    return f"hot-{rng.randint(1, cfg.hot_ids):04d}"


def worker(worker_id: int, role: str, cfg: WorkloadConfig, barrier: mp.Barrier,
           adapter_module: str) -> None:
    """One spawned process. Events: op/start/ok/err/crash-point."""
    import importlib

    adapter = importlib.import_module(adapter_module)
    rng = random.Random(cfg.seed * 1000 + worker_id)
    log_path = cfg.log_dir / f"worker-{worker_id}.jsonl"

    def log(**event):
        event.update({"worker": worker_id, "role": role, "t": time.time()})
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")

    mix = WRITER_MIX if role == "writer" else READER_MIX
    store = None
    try:
        barrier.wait(timeout=cfg.barrier_timeout_s)
        store = adapter.open_store(
            cfg.db_path,
            busy_timeout_s=cfg.busy_timeout_s,
            retry_attempts=cfg.retry_attempts,
            retry_base_s=cfg.retry_base_s,
        )
        log(event="open_ok")
        for i in range(cfg.ops_per_worker):
            op = pick_op(rng, mix)
            memory_id = hot_id(rng, cfg)
            if cfg.crash_inject and role == "writer" and worker_id == 1 and i == cfg.ops_per_worker // 2:
                log(event="crash_point", op=op)
                os.kill(os.getpid(), signal.SIGKILL)  # injected post-BEGIN crash
            try:
                result = adapter.apply(store, op, memory_id, rng)
                log(event="op", op=op, memory_id=memory_id, result=result)
            except adapter.BusyExhausted as exc:
                log(event="busy_exhausted", op=op, memory_id=memory_id, detail=str(exc))
            except Exception as exc:  # noqa: BLE001 - logged for the oracle
                log(event="error", op=op, memory_id=memory_id, detail=repr(exc))
        log(event="done")
    except Exception as exc:  # noqa: BLE001
        log(event="fatal", detail=repr(exc))
    finally:
        if store is not None:
            adapter.close_store(store)


def _launch(cfg: WorkloadConfig, adapter_module: str, procs: list[mp.Process]) -> None:
    for p in procs:
        p.start()
    deadline = time.monotonic() + cfg.join_timeout_s
    for p in procs:
        p.join(timeout=max(0.1, deadline - time.monotonic()))
    for p in procs:
        if p.is_alive():  # never leave a child running past the join envelope
            p.terminate()
            p.join()


def fresh_open_storm(cfg: WorkloadConfig, adapter_module: str,
                     workers: int = 8) -> list[Path]:
    """N processes synchronize at a barrier and open one absent database."""
    barrier = mp.Barrier(workers)
    procs = [
        mp.Process(target=worker, args=(i, "writer", cfg, barrier, adapter_module))
        for i in range(workers)
    ]
    _launch(cfg, adapter_module, procs)
    return [cfg.log_dir / f"worker-{i}.jsonl" for i in range(workers)]


def mixed_workload(cfg: WorkloadConfig, adapter_module: str) -> None:
    barrier = mp.Barrier(cfg.workers)
    procs = [
        mp.Process(
            target=worker,
            args=(i, "writer" if i < cfg.writers else "reader", cfg, barrier, adapter_module),
        )
        for i in range(cfg.workers)
    ]
    _launch(cfg, adapter_module, procs)


def merge_logs(log_dir: Path) -> list[dict]:
    events = []
    for path in sorted(log_dir.glob("worker-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            events.append(json.loads(line))
    return events
