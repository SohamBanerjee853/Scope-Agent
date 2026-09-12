"""Fail-closed, import-light baseline permission hook.

S1 allows recognized T1 requests locally and abstains for T2/T3. Classification
does not establish command execution or replace the host's sandbox. No watcher,
grant store, terminal UI, or model client belongs on this path in this milestone.
"""

import argparse
import os
import sys


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid hook arguments")


def _diagnostic() -> None:
    # Never echo exception details: untrusted request text can contain secrets.
    try:
        print("scope hook: abstaining after invalid input or an internal error", file=sys.stderr)
    except BaseException:
        pass


def _valid_tier(result, tier_type, segment_type) -> bool:
    """Only a complete, internally consistent classifier result can authorize."""
    ranks = {"T1": 1, "T2": 2, "T3": 3}
    if type(result) is not tier_type or result.name not in ranks:
        return False
    if not isinstance(result.reason, str) or not result.reason:
        return False
    if result.matched_rule is not None and not isinstance(result.matched_rule, str):
        return False
    if not isinstance(result.domains, tuple) or any(
        not isinstance(domain, str) or not domain for domain in result.domains
    ):
        return False
    if not isinstance(result.segments, tuple) or not result.segments:
        return False
    for segment in result.segments:
        if type(segment) is not segment_type or segment.name not in ranks:
            return False
        if not isinstance(segment.command, str) or not isinstance(segment.reason, str):
            return False
        if segment.matched_rule is not None and not isinstance(segment.matched_rule, str):
            return False
    return ranks[result.name] == max(ranks[segment.name] for segment in result.segments)


def main(argv: list[str] | None = None) -> int:
    """Emit one decision at most. Every permission-path failure exits zero."""
    try:
        parser = _Parser(prog="scope hook", description=__doc__, allow_abbrev=False)
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--abstain", action="store_true", help="emit no permission decision")
        modes.add_argument(
            "--always-allow", action="store_true",
            help="smoke fixture only: allow T1/T2 with SCOPE_SMOKE=1; never T3",
        )
        args = parser.parse_args(argv)
        if args.abstain:
            return 0
        if args.always_allow and os.environ.get("SCOPE_SMOKE") != "1":
            _diagnostic()
            return 0

        # Import inside the failure boundary. Broken policy imports never allow.
        from .wire import decision_json, read_request

        stream = getattr(sys.stdin, "buffer", sys.stdin)
        request = read_request(stream)
        from .tiers import SegmentFinding, Tier, classify

        result = classify(
            request.command, request.cwd, request.shell,
            description=request.description,
        )
        if not _valid_tier(result, Tier, SegmentFinding):
            return 0
        if result.name == "T3":
            return 0
        if result.name == "T1" or args.always_allow:
            # Construct all JSON before touching stdout. Input cannot add fields.
            sys.stdout.write(decision_json("allow"))
            sys.stdout.flush()
        return 0
    except SystemExit as exc:
        # argparse --help is an explicit human-facing operation, not a decision.
        if exc.code != 0:
            _diagnostic()
        return 0
    except BaseException:
        # Includes input/policy/stdio failures and interruptions. Never exit 2.
        _diagnostic()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
