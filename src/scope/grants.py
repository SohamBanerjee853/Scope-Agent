"""Validated, bounded human grants; proposals never confer permission.

All state is process-local. Matching deliberately handles fewer command shapes
than the classifier: a custom executable, quoted argument, wrapper, compound
command or expansion still needs an individual decision. This is lexical policy,
not inspection of executable contents or a replacement for the host sandbox.
"""

from dataclasses import dataclass, replace
import math
import ntpath
import posixpath
import re
from threading import RLock
import time
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from .tiers import SegmentFinding, Tier, classify
from .hook import _valid_tier
from .wire import PermissionRequest


LIFETIME_SECONDS = 900
MAX_BINDINGS = 1024
_PATTERN = re.compile(r"[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*(?: \*)?\Z")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_ARGUMENT = re.compile(r"[A-Za-z0-9_./:=+%@,\\-]+\Z")
_SHELLS = {"bash": "bash", "sh": "bash", "zsh": "bash", "posix": "bash",
           "powershell": "powershell", "pwsh": "powershell"}
_SIMPLE = frozenset({
    "pwd", "ls", "cat", "head", "tail", "wc", "grep", "rg", "pytest",
    "get-content", "get-childitem", "get-location",
    "touch", "mkdir", "cp", "mv", "tee", "set-content", "add-content",
    "out-file", "new-item", "copy-item", "move-item", "rename-item",
})
_NETWORK = frozenset({"curl", "wget", "invoke-webrequest", "invoke-restmethod"})
_PREFIXES = (
    ("git", "status"), ("git", "diff"), ("git", "log"), ("git", "show"),
    ("git", "add"), ("git", "commit"), ("git", "mv"),
    ("uv", "run", "pytest"), ("scope", "demo-adapter"),
)


@dataclass(frozen=True)
class Card:
    summary: str
    commands: tuple[str, ...]
    domains: tuple[str, ...]
    budget: int

    def to_dict(self) -> dict:
        """Return fresh JSON containers, never the store's mutable internals."""
        return {"summary": self.summary, "commands": list(self.commands),
                "domains": list(self.domains), "budget": self.budget}


@dataclass(frozen=True)
class GrantKey:
    session_id: str
    agent_id: str | None
    cwd: str
    shell: str


@dataclass(frozen=True)
class Grant:
    grant_id: str
    remaining: int
    card: Card
    key: GrantKey
    expires_at: float


def _text(value: object, label: str, limit: int) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"invalid {label}")
    value.encode("utf-8", errors="strict")
    return value


def _hostname(value: object) -> bool:
    return (isinstance(value, str) and len(value) <= 253 and bool(value)
            and all(_LABEL.fullmatch(label) for label in value.split(".")))


def _family(words: tuple[str, ...], shell: str) -> str | None:
    if not words:
        return None
    # POSIX executable spelling stays exact. Never basename a custom path or
    # strip .exe: reviewing one executable cannot authorize another one.
    name = words[0].lower() if shell == "powershell" else words[0]
    if name in _SIMPLE | _NETWORK:
        return name
    normalized = (name, *words[1:])
    for prefix in _PREFIXES:
        if normalized[:len(prefix)] == prefix:
            return " ".join(prefix)
    return None


def validate_card(raw: dict) -> Card:
    """Validate the exact public card schema in both supported shell dialects."""
    if not isinstance(raw, dict) or set(raw) != {"summary", "commands", "domains", "budget"}:
        raise ValueError("card must contain exactly summary, commands, domains, budget")
    summary = _text(raw["summary"], "summary", 300)
    commands, domains, budget = raw["commands"], raw["domains"], raw["budget"]
    if not isinstance(commands, list) or not 1 <= len(commands) <= 20:
        raise ValueError("card requires 1-20 command patterns")
    if not isinstance(domains, list) or len(domains) > 20 or not all(map(_hostname, domains)):
        raise ValueError("domains must be at most 20 exact lowercase hostnames")
    if type(budget) is not int or not 1 <= budget <= 100:
        raise ValueError("budget must be an integer from 1 to 100")
    for pattern in commands:
        if not isinstance(pattern, str) or len(pattern) > 300 or not _PATTERN.fullmatch(pattern):
            raise ValueError("patterns require literal words and an optional final ' *'")
        words = tuple(pattern.split(" "))
        base = words[:-1] if words[-1] == "*" else words
        if not any(_family(base, shell) for shell in ("bash", "powershell")):
            raise ValueError("command family is too broad or requires one-off review")
        # A wildcard stands for a harmless lexical target while testing the
        # pattern. 'mkdir *' must not be mistaken for a targetless mkdir request.
        sample = " ".join((*base, "scope-card-target")) if words[-1] == "*" else pattern
        for shell in ("bash", "powershell"):
            tier = classify(sample, "/scope-card-workspace", shell)
            if tier.name not in {"T1", "T2"} or tier.matched_rule == "classification-error":
                raise ValueError("hard-ask or unavailable classification cannot be granted")
        if base[0].lower() in _NETWORK and not domains:
            raise ValueError("network command patterns require exact domains")
    return Card(summary, tuple(commands), tuple(domains), budget)


def _card(value: Card | dict) -> Card:
    return validate_card(value.to_dict() if isinstance(value, Card) else value)


def normalize_cwd(value: str) -> str:
    """Normalize absolute POSIX and Windows paths without filesystem access."""
    value = _text(value, "cwd", 32768)
    if any(char in value for char in "*?[]~"):
        raise ValueError("cwd must be a literal absolute path")
    windows = bool(re.match(r"^[a-zA-Z]:", value) or value.startswith("\\"))
    if windows:
        drive, tail = ntpath.splitdrive(value)
        unc_parts = drive.replace("/", "\\")[2:].split("\\") if drive.startswith("\\\\") else None
        valid_unc_root = bool(unc_parts and len(unc_parts) == 2 and all(unc_parts) and not tail)
        if (not drive or (not tail.startswith(("/", "\\")) and not valid_unc_root)
                or ":" in tail or unc_parts is not None and (len(unc_parts) != 2 or not all(unc_parts))):
            raise ValueError("cwd must be an absolute Windows path")
        normalized = ntpath.normcase(ntpath.normpath(value))
        normalized_drive, normalized_tail = ntpath.splitdrive(normalized)
        if unc_parts is not None and normalized_tail in {"", "\\"}:
            return normalized_drive
        return normalized
    if not posixpath.isabs(value) or "\\" in value:
        raise ValueError("cwd must be an absolute path")
    return posixpath.normpath(value)


def _proposal_key(session_id: str, agent_id: str | None, cwd: str) -> tuple[str, str | None, str]:
    session = _text(session_id, "session", 4096)
    agent = None if agent_id is None else _text(agent_id, "agent", 4096)
    return session, agent, normalize_cwd(cwd)


def _key(request: PermissionRequest) -> GrantKey:
    base = _proposal_key(request.session_id, request.agent_id, request.cwd)
    if not isinstance(request.shell, str):
        raise ValueError("unsupported shell")
    shell = _SHELLS.get(request.shell.lower().removesuffix(".exe"))
    if shell is None:
        raise ValueError("unsupported shell")
    return GrantKey(*base, shell)


def _words(command: str, shell: str) -> tuple[str, ...] | None:
    if not isinstance(command, str) or not command.strip() or len(command) > 32768:
        return None
    # No quoting/escaping is interpreted here. Both dialects reject expansions,
    # redirects, comments, grouping, globbing, control characters and compounds.
    if any(ord(char) < 32 and char != "\t" for char in command):
        return None
    # str.split() treats Unicode whitespace as shell separators, although the
    # actual shell can treat it as part of a custom executable's name.
    words = tuple(re.split(r"[ \t]+", command.strip(" \t")))
    if not all(_ARGUMENT.fullmatch(word) for word in words):
        return None
    if shell != "powershell" and any("\\" in word for word in words):
        return None
    return words


def _network_domains(words: tuple[str, ...], family: str) -> set[str] | None:
    """Find every target, refusing redirects and unrecognized endpoint syntax."""
    switches = {"--silent", "--show-error", "--fail", "--head", "--verbose",
                "--compressed", "--ipv4", "--ipv6", "-s", "-S", "-sS", "-I", "-f",
                "--spider", "--quiet", "-q", "-usebasicparsing"}
    values = {"--max-time", "--connect-timeout", "--output", "-o", "-outfile",
              "--url", "-uri", "-X", "--request", "--method", "-method"}
    domains: set[str] = set()

    def target(value: str) -> bool:
        try:
            url = urlsplit(value if "://" in value else "https://" + value)
            host = url.hostname
            if (url.scheme.lower() not in {"https", "http"} or not host
                    or url.username is not None or url.password is not None
                    or not _hostname(host.lower()) or url.fragment or url.query):
                return False
            # Reject malformed ports as well as malformed authority delimiters.
            _ = url.port
            domains.add(host.lower())
            return True
        except ValueError:
            return False

    index = 1
    while index < len(words):
        word = words[index]
        option, equals, value = word.partition("=")
        if family.startswith("invoke-"):
            option = option.lower()
        if option in switches and not equals:
            pass
        elif option in values:
            if not equals:
                index += 1
                if index == len(words):
                    return None
                value = words[index]
            if option in {"--url", "-uri"}:
                if not target(value):
                    return None
            elif option in {"-X", "--request", "--method", "-method"}:
                if value.upper() not in {"GET", "HEAD"}:
                    return None
            elif option in {"--max-time", "--connect-timeout"}:
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
                    return None
            elif value.startswith("-"):
                return None
        elif word.startswith("-") or not target(word):
            return None
        index += 1
    return domains or None


def _matches(card: Card, request: PermissionRequest, key: GrantKey, tier: Tier) -> bool:
    words = _words(request.command, key.shell)
    family = _family(words, key.shell) if words else None
    if not family or not set(tier.domains).issubset(card.domains):
        return False
    if family in _NETWORK:
        domains = _network_domains(words, family)
        if not domains or not domains.issubset(card.domains):
            return False
    normalized = (words[0].lower(), *words[1:]) if key.shell == "powershell" else words
    for pattern in card.commands:
        expected = tuple(pattern.split(" "))
        if key.shell == "powershell":
            expected = (expected[0].lower(), *expected[1:])
        wildcard = expected[-1] == "*"
        prefix = expected[:-1] if wildcard else expected
        if normalized[:len(prefix)] == prefix and (wildcard or len(normalized) == len(prefix)):
            return True
    return False


def _classification(request: PermissionRequest) -> Tier:
    # This must precede all grant lookup and budget handling.
    tier = classify(request.command, request.cwd, request.shell, request.description)
    if not _valid_tier(tier, Tier, SegmentFinding) or tier.name not in {"T1", "T2"} or tier.matched_rule in {
        "classification-error", "invalid-command", "invalid-context", "unsupported-shell",
    }:
        raise ValueError("request cannot use a grant")
    return tier


class Store:
    """In-memory authority owned exclusively by the human watcher.

    ``approve`` is for the human decision handler only; never call it on receipt
    of a proposal. Watchers may hold ``lock`` across generation checks and reply
    publication so revoke cannot race a stale answer into a new grant.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.lock = RLock()
        self.generation = 0
        self._clock = clock
        self._grants: dict[GrantKey, Grant] = {}
        self._proposals: dict[tuple[str, str | None, str], Card] = {}

    def propose(self, session_id: str, agent_id: str | None, cwd: str, card: Card | dict) -> None:
        validated = _card(card)
        key = _proposal_key(session_id, agent_id, cwd)
        with self.lock:
            if key not in self._proposals and len(self._proposals) >= MAX_BINDINGS:
                raise ValueError("too many pending proposals")
            self._proposals[key] = validated

    def proposal_for(self, request: PermissionRequest) -> Card | None:
        try:
            key = _proposal_key(request.session_id, request.agent_id, request.cwd)
            with self.lock:
                return self._proposals.get(key)
        except (AttributeError, TypeError, ValueError):
            return None

    def approve(self, request: PermissionRequest, card: Card | dict) -> Grant:
        """Create a fresh grant after explicit human approval of this request."""
        tier = _classification(request)
        key = _key(request)
        validated = _card(card)
        if not _matches(validated, request, key, tier):
            raise ValueError("card does not cover the current command shape or domains")
        with self.lock:
            now = self._now()
            self._prune(now)
            if key not in self._grants and len(self._grants) >= MAX_BINDINGS:
                raise ValueError("too many active grants")
            grant = Grant(str(uuid4()), validated.budget, validated, key, now + LIFETIME_SECONDS)
            self._grants[key] = grant
            return grant

    def consume(self, request: PermissionRequest) -> Grant | None:
        """Reclassify first, then atomically spend exactly one matching budget."""
        try:
            tier = _classification(request)
            key = _key(request)
            with self.lock:
                now = self._now()
                self._prune(now)
                grant = self._grants.get(key)
                if grant is None or not _matches(grant.card, request, key, tier):
                    return None
                result = replace(grant, remaining=grant.remaining - 1)
                if result.remaining:
                    self._grants[key] = result
                else:
                    del self._grants[key]
                return result
        except Exception:
            return None

    def _now(self) -> float:
        value = self._clock()
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("invalid monotonic clock")
        return value

    def _prune(self, now: float) -> None:
        for key, grant in tuple(self._grants.items()):
            if now >= grant.expires_at or grant.remaining <= 0:
                del self._grants[key]

    def revoke(self) -> int:
        with self.lock:
            self.generation += 1
            self._grants.clear()
            self._proposals.clear()
            return self.generation

    def discard_grant(self, grant_id: str) -> None:
        """Roll back an undelivered new approval without touching newer grants."""
        with self.lock:
            for key, grant in tuple(self._grants.items()):
                if grant.grant_id == grant_id:
                    del self._grants[key]

    def clear_session(self, session_id: str) -> None:
        with self.lock:
            self.generation += 1
            self._grants = {key: value for key, value in self._grants.items()
                            if key.session_id != session_id}
            self._proposals = {key: value for key, value in self._proposals.items()
                               if key[0] != session_id}

    def sessions(self) -> tuple[str, ...]:
        with self.lock:
            return tuple(sorted({key.session_id for key in self._grants}
                                | {key[0] for key in self._proposals}))
