"""Check retry deduplication and distinct orders; exit 1 when either fails."""

import json

from checkout import charge_order
from fake_payment import FakePaymentService, LostAcknowledgment


def check_orders(order_ids):
    service = FakePaymentService()
    error = None
    receipts = []
    try:
        for order_id in order_ids:
            receipts.append(charge_order(service, order_id))
    except (LostAcknowledgment, ValueError) as exc:
        error = str(exc)
    charged_orders = [charge["order_id"] for charge in service.charges]
    return {
        "passed": error is None and charged_orders == order_ids
                  and len({receipt["charge_id"] for receipt in receipts}) == len(order_ids),
        "charges": len(service.charges),
        "charged_orders": charged_orders,
        "error": error,
    }


def main():
    retry = check_orders(["fixture-order"])
    distinct = check_orders(["fixture-order-a", "fixture-order-b"])
    passed = retry["passed"] and distinct["passed"]
    print(json.dumps({"provenance": "test_fixture", "passed": passed,
                      "retry_deduplication": retry, "distinct_orders": distinct}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
