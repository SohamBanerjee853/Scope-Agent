"""Print a real observation of the offline fixture, without changing any files."""

import json

from checkout import charge_order
from fake_payment import FakePaymentService


def main():
    service = FakePaymentService()
    receipt = charge_order(service, "fixture-order")
    print(json.dumps({
        "provenance": "test_fixture",
        "charges": len(service.charges),
        "request_keys": service.request_keys,
        "events": service.events,
        "receipt": receipt,
    }))


if __name__ == "__main__":
    main()
