"""Console entry point. Full command surface lands in TASK-040; for the
package-shell phase only `--help`/`--version` work and every runtime command
exits with a typed not-yet-available error."""
from __future__ import annotations

import argparse
import sys

PROG = "another-brain"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Shared long-term memory for MCP agents. Bare invocation starts the "
            "MCP stdio server; subcommands manage the model, data, and migration."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROG} 0.11.0")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="start the server (stdio default, --http opt-in)")
    sub.add_parser("model", help="model management: pull | status")
    sub.add_parser("doctor", help="verify install, model, and database health")
    sub.add_parser("recent", help="print recent memories")
    sub.add_parser("admin", help="admin operations: restore | hard-delete")
    sub.add_parser("import-jsonl", help="import a another-brain-jsonl v1 artifact")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    print(
        f"{PROG}: command {args.command!r} is not yet available in this build"
        " (package shell phase, see Plan 07 GOAL-009)",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
