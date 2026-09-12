"""Retry checkout once when the fake service loses an acknowledgment."""

from fake_payment import LostAcknowledgment


def charge_order(service, order_id):
    for attempt in range(2):
        key = f"{order_id}:{attempt}"
        try:
            return service.charge(order_id, key=key)
        except LostAcknowledgment:
            if attempt == 1:
                raise
