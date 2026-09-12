"""Reusable offline assets executed through the A1 runner; no human callbacks."""

import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from scope import runner


ASSETS = Path(__file__).parent / "fixtures" / "arjun_debugging"
BUGGY_KEY = 'key = f"{order_id}:{attempt}"'


def file_contents(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    homes = []
    for name in ("SCOPE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        home = tmp_path / name
        home.mkdir()
        (home / "unrelated-settings").write_text("preserve me", encoding="utf-8")
        monkeypatch.setenv(name, str(home))
        homes.append(home)
    for name in ("CODEX_THREAD_ID", "SCOPE_LAUNCH_ID"):
        monkeypatch.delenv(name, raising=False)
    expected_homes = [file_contents(home) for home in homes]
    yield
    assert [file_contents(home) for home in homes] == expected_homes


@pytest.fixture
def disposable_fixture(tmp_path):
    expected_assets = file_contents(ASSETS)
    root = tmp_path / "developer's payment project"
    shutil.copytree(ASSETS, root)
    yield root
    assert file_contents(ASSETS) == expected_assets


def run_script(root, script):
    before = file_contents(root)
    expected_environment = dict(os.environ)
    argv = [sys.executable, "-B", script]
    result = runner.run(argv, cwd=root, timeout=10)
    assert result.argv == argv
    assert result.cwd == str(root.resolve())
    assert result.error is None and not result.timed_out
    assert not result.stdout_truncated and not result.stderr_truncated
    assert not result.cleanup_incomplete and result.stderr == ""
    assert file_contents(root) == before
    assert dict(os.environ) == expected_environment
    output = json.loads(result.stdout)
    assert output["provenance"] == "test_fixture"
    return result, output


def replace_key(root, replacement):
    source = root / "checkout.py"
    original = source.read_text(encoding="utf-8")
    assert original.count(BUGGY_KEY) == 1
    source.write_text(original.replace(BUGGY_KEY, replacement), encoding="utf-8")


def test_buggy_retry_charges_before_losing_acknowledgment(disposable_fixture):
    result, output = run_script(disposable_fixture, "probe.py")
    assert result.succeeded
    assert output["charges"] == 2
    assert output["request_keys"] == ["fixture-order:0", "fixture-order:1"]
    assert output["events"] == ["charged", "acknowledgment_lost", "charged", "acknowledged"]
    assert output["receipt"] == {"charge_id": 2, "order_id": "fixture-order"}
    # A second process starts with a fresh ledger, without an in-place reset.
    repeated, second = run_script(disposable_fixture, "probe.py")
    assert repeated.succeeded and second == output
    regression, checks = run_script(disposable_fixture, "regression.py")
    assert regression.exit_code == 1 and not regression.succeeded
    assert checks["passed"] is False
    assert checks["retry_deduplication"]["charges"] == 2
    assert checks["retry_deduplication"]["passed"] is False
    assert checks["distinct_orders"]["charges"] == 4
    assert checks["distinct_orders"]["passed"] is False


def test_stable_order_key_fixes_retry_and_preserves_distinct_orders(disposable_fixture):
    replace_key(disposable_fixture, "key = order_id")
    result, output = run_script(disposable_fixture, "probe.py")
    assert result.succeeded
    assert output["charges"] == 1
    assert output["request_keys"] == ["fixture-order", "fixture-order"]
    assert output["events"] == ["charged", "acknowledgment_lost", "deduplicated"]
    assert output["receipt"] == {"charge_id": 1, "order_id": "fixture-order"}
    regression, checks = run_script(disposable_fixture, "regression.py")
    assert regression.succeeded and checks["passed"] is True
    assert checks["retry_deduplication"] == {
        "passed": True, "charges": 1, "charged_orders": ["fixture-order"], "error": None,
    }
    assert checks["distinct_orders"] == {
        "passed": True, "charges": 2,
        "charged_orders": ["fixture-order-a", "fixture-order-b"], "error": None,
    }


def test_regression_rejects_a_constant_key_shared_by_all_orders(disposable_fixture):
    replace_key(disposable_fixture, 'key = "all-orders"')
    result, output = run_script(disposable_fixture, "probe.py")
    assert result.succeeded and output["charges"] == 1
    regression, checks = run_script(disposable_fixture, "regression.py")
    assert regression.exit_code == 1 and not regression.succeeded
    assert checks["passed"] is False
    assert checks["retry_deduplication"]["passed"] is True
    assert checks["distinct_orders"] == {
        "passed": False, "charges": 1, "charged_orders": ["fixture-order-a"],
        "error": "idempotency key reused for a different order",
    }
