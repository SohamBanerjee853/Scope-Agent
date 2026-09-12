# Offline retry debugging fixture

These are reusable test assets for future A2/A3 work. They do not implement a
Scope demo, human prediction/consent, classification or a host launcher.

Copy this directory into a disposable location before editing. The service keeps
its ledger in memory: it records the first charge for each order, then loses the
acknowledgment. Checkout retries once, using a different key on each attempt, so
one order produces two charges. No payment service or network is contacted.

Run the standalone scripts from the copied directory using the same Python 3.11+
interpreter as Scope:

```text
python -B probe.py
python -B regression.py
```

`probe.py` emits JSON with the literal top-level field `charges` and the sequence
of service events. `regression.py` emits JSON and exits 1 for a failing regression,
or 0 when retry deduplication and distinct-order charging both pass. Both label
their output `test_fixture`. `-B` prevents Python bytecode cache files.

The bounded repair in the copied `checkout.py` is:

```diff
-        key = f"{order_id}:{attempt}"
+        key = order_id
```

The probe then reports one charge. The regression also rejects using a constant
key for every order, which would break separate purchases. Every script run gets
a fresh service; no ledger or settings are written to disk. Automated results
establish fixture behavior only, not human understanding or debugging time saved.
