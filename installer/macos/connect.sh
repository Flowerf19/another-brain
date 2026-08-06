#!/bin/sh
# macOS entry point — POSIX sh connector logic is OS-identical; keep one source.
exec "$(dirname "$0")/../linux/connect.sh" "$@"
