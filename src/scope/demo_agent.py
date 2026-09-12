"""Narrow local payment-fixture probe, invoked only by an explicit caller."""

import argparse
from importlib.resources import files
import json
from pathlib import Path
import sys
import uuid

from . import runner
from .demo import MARKER, _linked
from .install import _read

BUGGY_KEY = '    return f"{order_id}-attempt-{attempt}"'
FIXED_KEY = "    return order_id"


def probe(path):
    root = Path(path).expanduser().absolute()
    if not root.is_dir() or _linked(root):
        raise ValueError("probe requires the prepared fixture directory")
    data = _read(root / MARKER, 4096)
    if data is None:
        raise ValueError("prepare the disposable demo before probing")
    marker = json.loads(data)
    session = marker.get("session_id") if isinstance(marker, dict) else None
    if (not isinstance(marker, dict) or type(marker.get("schema_version")) is not int
            or marker["schema_version"] != 1 or marker.get("fixture") != "scope-payment-retry"
            or not isinstance(session, str) or not session.startswith("demo-")):
        raise ValueError("invalid disposable fixture marker")
    uuid.UUID(session.removeprefix("demo-"))
    bundled = files("scope").joinpath("assets", "demo", "payment.py").read_bytes()
    fixed = bundled.replace(BUGGY_KEY.encode(), FIXED_KEY.encode(), 1)
    source = _read(root / "payment.py", 64 * 1024)
    if source not in (bundled, fixed):
        raise ValueError("adapter accepts only the bundled retry fixture or its stable-key repair")
    # Isolated mode prevents local json.py or PYTHONPATH injection. Feed the
    # validated bytes to Python instead of reopening an editable script path.
    result = runner.run([sys.executable, "-I", "-c", source.decode("utf-8")], cwd=root, timeout=10)
    if not result.succeeded:
        raise RuntimeError("fixture probe failed or returned incomplete evidence")
    observed = json.loads(result.stdout)
    if not isinstance(observed, dict) or type(observed.get("charge_count")) is not int:
        raise ValueError("fixture returned invalid observed JSON")
    return observed


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scope demo-adapter", description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("probe",))
    parser.add_argument("-C", "--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        print(json.dumps(probe(args.cwd), ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        from .watch_ui import display_text
        print("scope demo-adapter: " + display_text(exc), file=sys.stderr)
        return 1
