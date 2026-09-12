"""Human decision watcher. No request handler executes commands or probes."""

import argparse
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from . import log
from .grants import Store, validate_card
from .hook import _valid_tier
from .ipc import Server
from .tiers import SegmentFinding, Tier, classify
from .watch_ui import TerminalUI
from .wire import parse_request
import json


def _text(value, *, optional=False, limit=16384):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise ValueError("invalid text field")
    value.encode("utf-8")
    return value


class _ReviewContext:
    def __init__(self, owner, transport, generation):
        self.owner = owner
        self.transport = transport
        self.generation = generation
        self.deadline = min(transport.deadline, time.monotonic() + owner.human_timeout)
        self.session_id = None
        self.presentation = None

    def active(self):
        return (time.monotonic() < self.deadline and not self.owner.closed.is_set()
                and self.owner.store.generation == self.generation and self.transport.active()
                and self.owner._native_active(self.session_id))


class Watcher:
    def __init__(self, ui=None, *, store=None, human_timeout=95, max_clients=16):
        if not 0 < human_timeout <= 95:
            raise ValueError("human_timeout must be in (0, 95]")
        self.ui = ui if ui is not None else TerminalUI()
        self.store = store if store is not None else Store()
        self.human_timeout = human_timeout
        self.ui_lock = threading.Lock()
        self.closed = threading.Event()
        self._close_lock = threading.RLock()
        self._close_complete = False
        self._sessions = set()
        self.launch = None
        options = {}
        if "SCOPE_LAUNCH_ID" in os.environ or "SCOPE_HOST" in os.environ:
            from . import host_session

            self.launch = host_session.current_launch()
            if self.launch is None:
                raise ValueError("native launch identity unavailable")
            options["launch_id"] = self.launch["launch_id"]
        self.server = Server(self.handle, admission=self._admit,
                             request_timeout=human_timeout, max_clients=max_clients,
                             control_kinds=frozenset({"revoke", "shutdown"}), **options)

    def _native_active(self, session):
        if self.launch is None or session is None:
            return True
        from . import host_session

        try:
            return (host_session.current_launch() == self.launch
                    and host_session.require_session(session) == session)
        except Exception:
            return False

    def _bind(self, context, session, *, cwd=None, understanding=False):
        if self.launch is None:
            return
        from . import host_session, repository

        if not self.launch["understanding" if understanding else "permissions"]:
            raise ValueError("workflow disabled in this launch")
        if session is None:
            raise ValueError("launched request requires a native session")
        if understanding:
            if repository.find_root(cwd) != repository.find_root(self.launch["cwd"]):
                raise ValueError("question belongs to another project")
            host_session.require_session(session)
        else:
            host_session.require_session(session, cwd=cwd)
        context.session_id = session
        previous = context.transport.reply_valid
        context.transport.reply_valid = lambda: previous() and self._native_active(session)

    def _admit(self, message, context):
        with self.store.lock:
            return self.store.generation

    def _guard(self, context, generation):
        context.reply_lock = self.store.lock
        context.reply_valid = lambda: (self.store.generation == generation and not self.closed.is_set())

    def start(self):
        self.server.start()
        return self

    def close(self):
        # The main loop may observe closed while a daemon reply thread is still
        # removing the endpoint. Wait for that cleanup before the process exits.
        with self._close_lock:
            if self._close_complete:
                return
            self.closed.set()
            with self.store.lock:
                self.store.revoke()
            self.server.close()
            self._close_complete = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def _record(self, session_id, event, **fields):
        # The watcher uses SCOPE_HOME through the shared foundation logger.
        # There is no process-global environment mutation for per-client homes.
        log.append(session_id, event, **fields)

    def _remember(self, session_id):
        if session_id not in self._sessions and len(self._sessions) >= 1024:
            raise ValueError("watcher session capacity reached")
        self._sessions.add(session_id)

    def _revoke(self):
        with self.store.lock:
            generation = self.store.revoke()
            for session_id in sorted(self._sessions):
                self._record(session_id, "scopes_revoked", generation=generation, source="watcher",
                             session=session_id, revoked=True)
            return generation

    def _ask(self, operation, args, context):
        if not self.ui.interactive:
            return None
        while context.active():
            if not self.ui_lock.acquire(timeout=0.025):
                continue
            try:
                if not context.active() or not self.ui.interactive:
                    return None
                return getattr(self.ui, operation)(*args, context)
            finally:
                self.ui_lock.release()
        return None

    def handle(self, message, context):
        """Process authenticated logical bodies. Auth fields never enter logs."""
        generation = context.state
        self._guard(context, generation)
        try:
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            kind = message.get("kind")
            shapes = {
                "ping": ({"kind"}, set()),
                "proposal": ({"kind", "session_id", "cwd", "card"}, {"agent_id"}),
                "request": ({"kind", "request"}, {"request_id"}),
                "question": ({"kind", "repo", "context", "prompt"}, {"session_id", "presentation"}),
                "notice": ({"kind", "message"}, set()),
                "revoke": ({"kind"}, set()),
                "stop": ({"kind"}, {"session_id"}),
                "shutdown": ({"kind"}, set()),
            }
            if kind not in shapes:
                raise ValueError("unsupported message")
            required, optional = shapes[kind]
            if not required <= message.keys() or not message.keys() <= required | optional:
                raise ValueError("unexpected message fields")
            if kind in {"revoke", "shutdown"}:
                new_generation = self._revoke()
                self._guard(context, new_generation)
                if kind == "shutdown":
                    context.after_reply = self.close
                    return {"stopped": True}
                return {"revoked": True}
            review_context = _ReviewContext(self, context, generation)
            if not review_context.active():
                return {"error": "cancelled"}
            if kind == "ping":
                return {"ready": True}
            if kind == "proposal":
                session = _text(message["session_id"], limit=512)
                agent = _text(message.get("agent_id"), optional=True, limit=512)
                cwd = _text(message["cwd"], limit=4096)
                self._bind(review_context, session, cwd=cwd)
                card = validate_card(message["card"])
                with self.store.lock:
                    if not review_context.active():
                        return {"accepted": False}
                    self.store.propose(session, agent, cwd, card)
                    self._remember(session)
                    self._record(session, "proposal_created", agent_id=agent, cwd=cwd, card=card.to_dict())
                return {"accepted": True}
            if kind == "request":
                return self._request(message, review_context)
            if kind == "question":
                repo = _text(message["repo"], limit=4096)
                session = _text(message.get("session_id"), optional=True, limit=512)
                self._bind(review_context, session, cwd=repo, understanding=True)
                context_text = message["context"]
                if not isinstance(context_text, str) or len(context_text) > 16384:
                    raise ValueError("invalid question context")
                prompt = _text(message["prompt"])
                if "presentation" in message:
                    from .review_presentation import validate

                    review_context.presentation = validate(message["presentation"], repo)
                answer = self._ask("question", (repo, context_text, prompt), review_context)
                with self.store.lock:
                    if not review_context.active() or not isinstance(answer, str) or len(answer) > 16384:
                        return {"error": "question unavailable or cancelled"}
                    return {"answer": answer}
            if kind == "notice":
                notice = _text(message["message"])
                # Notices share serialization with prompts; they never read input.
                while review_context.active():
                    if self.ui_lock.acquire(timeout=0.025):
                        try:
                            if review_context.active():
                                self.ui.notice(notice)
                                return {"received": True}
                        finally:
                            self.ui_lock.release()
                return {"received": False}
            if kind == "stop":
                session = _text(message.get("session_id"), optional=True, limit=512)
                if self.launch is not None:
                    from . import host_session

                    host_session.require_session(session)
                if session:
                    with self.store.lock:
                        self._record(session, "watcher_stop_notice")
                return {"received": True}
        except Exception:
            # Invalid data, UI failures and log failures provide no permission.
            return {"error": "invalid request or review unavailable"}

    def _request(self, message, context):
        request = parse_request(json.dumps(message["request"], allow_nan=False))
        self._bind(context, request.session_id, cwd=request.cwd)
        request_id = _text(message.get("request_id", str(uuid.uuid4())), limit=512)
        tier = classify(request.command, request.cwd, request.shell, request.description)
        if not _valid_tier(tier, Tier, SegmentFinding):
            return {"behavior": None}
        with self.store.lock:
            if not context.active():
                return {"behavior": None}
            self._remember(request.session_id)
            self._record(request.session_id, "permission_review", request_id=request_id,
                         command=request.command, cwd=request.cwd, shell=request.shell,
                         agent_id=request.agent_id, tier=tier.name, reason=tier.reason)
            if tier.name == "T3":
                return self._decision(request, request_id, None, "hard_ask")
            if tier.name == "T1":
                return self._decision(request, request_id, "allow", "auto_allow")
            if not self.ui.interactive:
                return self._decision(request, request_id, None, "no_terminal")
            grant = self.store.consume(request)
            if grant is not None:
                return self._decision(request, request_id, "allow", "scope_allow", grant)
            card = self.store.proposal_for(request)

        result = self._ask("review", (request, card), context)
        with self.store.lock:
            if not context.active() or not isinstance(result, dict) or not result.keys() <= {"action", "card"}:
                return self._decision(request, request_id, None, "cancelled_or_unavailable")
            action = result.get("action")
            if action == "revoke":
                self._revoke()
                return {"behavior": None}
            # Repeat classification at the final human decision boundary too.
            tier = classify(request.command, request.cwd, request.shell, request.description)
            if not _valid_tier(tier, Tier, SegmentFinding) or tier.name == "T3":
                return self._decision(request, request_id, None, "hard_ask")
            if action == "once":
                return self._decision(request, request_id, "allow", "allowed_once")
            if action == "deny":
                return self._decision(request, request_id, "deny", "denied")
            if action == "grant":
                selected = validate_card(result["card"]) if "card" in result else card
                if selected is not None:
                    # Publish the fresh grant only inside transport's final send
                    # lock. Failed delivery rolls it back before another consumer
                    # can observe it. No approval state is staged while waiting.
                    minted = []

                    def commit():
                        if not context.active() or not self.ui.interactive:
                            return False
                        created = self.store.approve(request, selected)
                        minted.append(created.grant_id)
                        self._record(request.session_id, "grant_created", request_id=request_id,
                                     grant_id=created.grant_id, card=selected.to_dict(), agent_id=request.agent_id,
                                     cwd=request.cwd, shell=request.shell)
                        grant = self.store.consume(request)
                        if grant is None:
                            return False
                        self._decision(request, request_id, "allow", "scope_allow", grant)
                        return True

                    def finalize(delivered):
                        if not delivered:
                            for grant_id in minted:
                                self.store.discard_grant(grant_id)
                        try:
                            self._record(request.session_id, "permission_review_delivery", request_id=request_id,
                                         delivered=delivered)
                        except Exception:
                            pass

                    context.transport.before_reply = commit
                    context.transport.reply_finalize = finalize
                    return {"behavior": "allow"}
            return self._decision(request, request_id, None, "abstain")

    def _decision(self, request, request_id, behavior, action, grant=None):
        fields = {"request_id": request_id, "behavior": behavior, "action": action}
        if grant is not None:
            fields.update(grant_id=grant.grant_id, remaining=grant.remaining)
        self._record(request.session_id, "permission_review_decision", **fields)
        return {"behavior": behavior}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scope watch", description=__doc__)
    parser.add_argument("--owner-home", type=Path, help="private run whose owner lifetime controls this watcher")
    parser.add_argument("--plain", action="store_true", help="use the line-oriented review interface with tagged replies")
    args = parser.parse_args(argv)
    ui = TerminalUI()
    watcher = None
    owner_home = None
    use_tui = False
    try:
        if args.owner_home is not None:
            from . import host_session, launch

            config = host_session.read_launch(args.owner_home)
            owner_home = Path(config["home"])
            os.environ.update(SCOPE_HOME=str(owner_home), SCOPE_LAUNCH_ID=config["launch_id"],
                              SCOPE_HOST=config["host"], SCOPE_POPUP="0")
            os.environ.pop("CODEX_THREAD_ID", None)
        use_tui = not args.plain and ui.interactive and os.environ.get("TERM") != "dumb"
        if use_tui:
            from .review_tui import ReviewUI

            ui = ReviewUI()
        watcher = Watcher(ui)
        watcher.start()
        transport = "local authenticated TCP and run mailbox" if watcher.launch else "local authenticated TCP"
        if use_tui:
            host = watcher.launch["host"].title() if watcher.launch else "your coding agent"
            ui.notice(f"Review pane ready for {host}.")
        else:
            ui.notice(f"Scope review is listening on {transport}. Ctrl-C revokes scopes and stops this watcher.")
        if not ui.interactive:
            ui.notice("No interactive terminal: human decisions will abstain and questions will return unavailable.")
        started = time.monotonic()

        def owner_tick():
            if owner_home is not None and not watcher.closed.is_set():
                if launch.owner_alive(owner_home):
                    return
                if launch.owner_started(owner_home) or time.monotonic() - started >= 30:
                    ui.notice("The launch owner has ended or did not become available; revoking this run's scopes.")
                    watcher.close()
                    (owner_home / "environment.json").unlink(missing_ok=True)
                    host_session.close_session(inferred=True, home=owner_home)
                    state = host_session.status(home=owner_home)
                    if state.get("session_id"):
                        from . import receipt

                        receipt.write(state["session_id"], home=owner_home, host=state["host"])
        if use_tui:
            ui.run(watcher, owner_tick=owner_tick, on_revoke=watcher._revoke)
        else:
            while not watcher.closed.wait(0.1):
                owner_tick()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        error_ui = TerminalUI(output_stream=sys.stderr) if use_tui else ui
        error_ui.notice("Scope watcher could not start or continue. Check for another watcher and private SCOPE_HOME permissions.")
        return 1
    finally:
        if watcher is not None:
            watcher.close()
