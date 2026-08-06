#!/bin/sh
# macOS entry point — the gate logic is OS-identical bash; keep one source.
exec "$(dirname "$0")/../linux/check-wheel-install.sh" "$@"
