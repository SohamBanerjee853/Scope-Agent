# Offline fixture by default; --dry-run only prints its plan.
$ErrorActionPreference = 'Stop'
& uv run scope smoke --shell powershell @args
exit $LASTEXITCODE
