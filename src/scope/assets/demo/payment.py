"""Disposable in-memory retry bug. No real payments, files, or network services."""

import json


class AcknowledgementLost(TimeoutError):
    """The fake service committed a charge before its reply was lost."""


class FakePaymentService:
    def __init__(self):
        self.charges = {}
        self.attempt_keys = []
        self.lost_acknowledgements = 0

    def charge(self, order_id, amount_cents, idempotency_key):
        self.attempt_keys.append(idempotency_key)
        if idempotency_key in self.charges:
            return self.charges[idempotency_key]
        receipt = {"order_id": order_id, "amount_cents": amount_cents,
                   "charge_id": f"fake-charge-{len(self.charges) + 1}"}
        self.charges[idempotency_key] = receipt
        if self.lost_acknowledgements == 0:
            self.lost_acknowledgements += 1
            raise AcknowledgementLost("FIXTURE: charge committed; first acknowledgement lost")
        return receipt


def retry_key(order_id, attempt):
    """Choose the key sent by checkout for one attempt of the same order."""
    return f"{order_id}-attempt-{attempt}"


def checkout(service, order_id, amount_cents=100):
    """Retry the lost reply once; the key policy is the intentional bug."""
    for attempt in (1, 2):
        try:
            return service.charge(order_id, amount_cents, retry_key(order_id, attempt))
        except AcknowledgementLost:
            if attempt == 2:
                raise


def probe():
    """Observe a fresh fake service; these values come from actual local calls."""
    service = FakePaymentService()
    receipt = checkout(service, "demo-order", amount_cents=100)
    return {
        "fixture": "disposable in-memory payment retry",
        "order_id": receipt["order_id"],
        "charge_count": len(service.charges),
        "charged_cents": sum(charge["amount_cents"] for charge in service.charges.values()),
        "attempt_keys": list(service.attempt_keys),
        "lost_acknowledgements": service.lost_acknowledgements,
    }


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True))
