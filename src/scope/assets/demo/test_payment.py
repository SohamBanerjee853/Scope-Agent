"""Desired fixture behavior. The retry regression initially exposes the bug."""

import pytest

from payment import AcknowledgementLost, FakePaymentService, checkout


def test_retry_after_lost_acknowledgement_charges_one_order_once():
    service = FakePaymentService()
    receipt = checkout(service, "order-1", amount_cents=100)
    assert receipt["order_id"] == "order-1"
    assert service.lost_acknowledgements == 1
    assert len(service.charges) == 1
    assert service.attempt_keys == ["order-1", "order-1"]


def test_fake_service_deduplicates_an_identical_idempotency_key():
    service = FakePaymentService()
    with pytest.raises(AcknowledgementLost):
        service.charge("order-1", 100, "stable-fixture-key")
    receipt = service.charge("order-1", 100, "stable-fixture-key")
    assert receipt["charge_id"] == "fake-charge-1"
    assert len(service.charges) == 1


def test_distinct_orders_have_distinct_charges():
    service = FakePaymentService()
    first = checkout(service, "order-1", amount_cents=100)
    second = checkout(service, "order-2", amount_cents=250)
    assert first["order_id"] != second["order_id"]
    assert first["charge_id"] != second["charge_id"]
    assert {charge["order_id"] for charge in service.charges.values()} == {"order-1", "order-2"}
