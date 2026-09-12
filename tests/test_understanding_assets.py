"""Packaged fixtures run locally with explicit test provenance and no services."""

from importlib import resources, util
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
import yaml


BUGGY_KEY = '    return f"{order_id}-attempt-{attempt}"'
FIXED_KEY = "    return order_id"


def asset(name):
    return resources.files("scope").joinpath("assets", "demo", name).read_bytes()


def load_payment(tmp_path, *, fixed=False):
    source = asset("payment.py").decode("utf-8")
    assert source.count(BUGGY_KEY) == 1
    if fixed:
        source = source.replace(BUGGY_KEY, FIXED_KEY, 1)
    path = tmp_path / "payment.py"
    path.write_text(source, encoding="utf-8")
    name = "scope_payment_fixture_" + uuid4().hex
    spec = util.spec_from_file_location(name, path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fixed, count, keys", [
    (False, 2, ["demo-order-attempt-1", "demo-order-attempt-2"]),
    (True, 1, ["demo-order", "demo-order"]),
])
def test_actual_fake_charge_evidence_distinguishes_bug_and_stable_key(tmp_path, fixed, count, keys):
    payment = load_payment(tmp_path, fixed=fixed)
    report = payment.probe()
    assert report["charge_count"] == count
    assert report["charged_cents"] == 100 * count
    assert report["attempt_keys"] == keys
    assert report["lost_acknowledgements"] == 1
    assert payment.probe() == report  # Each run starts with a fresh fake service.


def test_charge_is_committed_before_acknowledgement_loss_and_retry_deduplicates(tmp_path):
    payment = load_payment(tmp_path)
    service = payment.FakePaymentService()
    with pytest.raises(payment.AcknowledgementLost):
        service.charge("fixture-order", 300, "fixture-stable-key")
    assert len(service.charges) == 1
    assert service.charges["fixture-stable-key"]["amount_cents"] == 300
    receipt = service.charge("fixture-order", 300, "fixture-stable-key")
    assert receipt == service.charges["fixture-stable-key"]
    assert len(service.charges) == 1


def test_stable_identity_preserves_distinct_orders(tmp_path):
    payment = load_payment(tmp_path, fixed=True)
    service = payment.FakePaymentService()
    first = payment.checkout(service, "fixture-order-one", 100)
    second = payment.checkout(service, "fixture-order-two", 250)
    assert first["charge_id"] != second["charge_id"]
    assert len(service.charges) == 2
    assert sum(charge["amount_cents"] for charge in service.charges.values()) == 350


@pytest.mark.parametrize("fixed, expected_code", [(False, 1), (True, 0)])
def test_packaged_regression_exposes_bug_and_passes_after_actual_repair(tmp_path, fixed, expected_code):
    load_payment(tmp_path, fixed=fixed)
    (tmp_path / "test_payment.py").write_bytes(asset("test_payment.py"))
    # A temporary pytest config keeps this disposable project independent of any
    # ancestor checkout's plugins or collection paths.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_payment.py"],
                            cwd=tmp_path, text=True, capture_output=True, timeout=20, check=False)
    assert result.returncode == expected_code, result.stdout + result.stderr
    if fixed:
        assert "3 passed" in result.stdout
    else:
        assert "1 failed, 2 passed" in result.stdout
        assert "test_retry_after_lost_acknowledgement_charges_one_order_once" in result.stdout


def test_packaged_probe_emits_observed_json_from_a_real_local_process(tmp_path):
    (tmp_path / "payment.py").write_bytes(asset("payment.py"))
    result = subprocess.run([sys.executable, "payment.py"], cwd=tmp_path, text=True,
                            capture_output=True, timeout=5, check=False)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)["charge_count"] == 2


def test_packaged_resources_are_utf8_and_skill_has_valid_frontmatter():
    for name in ("payment.py", "test_payment.py", "README.md"):
        assert asset(name).decode("utf-8")
    text = resources.files("scope").joinpath("skills", "scope-understand", "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["name"] == "scope-understand"
    assert isinstance(frontmatter["description"], str) and frontmatter["description"].strip()
