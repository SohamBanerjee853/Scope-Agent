"""Disposable in-memory payment service; no network or real money."""


class LostAcknowledgment(TimeoutError):
    """The service recorded a charge, but the caller did not get its receipt."""


class FakePaymentService:
    def __init__(self):
        self.charges = []
        self.request_keys = []
        self.events = []
        self._receipts = {}
        self._lost_ack_for = set()

    def charge(self, order_id, *, key):
        self.request_keys.append(key)
        if key in self._receipts:
            receipt = self._receipts[key]
            if receipt["order_id"] != order_id:
                raise ValueError("idempotency key reused for a different order")
            self.events.append("deduplicated")
            return dict(receipt)

        receipt = {"charge_id": len(self.charges) + 1, "order_id": order_id}
        self._receipts[key] = receipt
        self.charges.append(dict(receipt))
        self.events.append("charged")
        if order_id not in self._lost_ack_for:
            self._lost_ack_for.add(order_id)
            self.events.append("acknowledgment_lost")
            raise LostAcknowledgment("charge succeeded; acknowledgment was lost")
        self.events.append("acknowledged")
        return dict(receipt)
