#!/bin/sh
# Offline fixture by default; --dry-run only prints its plan.
set -eu
exec uv run scope smoke --shell posix "$@"
