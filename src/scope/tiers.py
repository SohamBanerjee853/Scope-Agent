"""Conservative, bounded lexical policy. This is not a shell or a sandbox.

No command, filesystem path, shell profile, alias definition, symlink, Git config,
test body or executable is inspected or executed. Recognized tests can execute
project code; T1 describes the command shape, not its effects. Unknown shapes
remain T2. Dangerous syntax and known dangerous commands take precedence.

The two bundled rule files use JSON syntax, a YAML subset, so the decision path
can load them with the standard library without importing a YAML/UI dependency.
"""

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import json
import ntpath
import posixpath
import re
from urllib.parse import urlsplit

MAX_COMMAND = 32768
_RANK = {"T1": 1, "T2": 2, "T3": 3}


@dataclass(frozen=True)
class SegmentFinding:
    command: str
    name: str
    reason: str
    matched_rule: str | None = None


@dataclass(frozen=True)
class Tier:
    name: str
    reason: str
    matched_rule: str | None = None
    domains: tuple[str, ...] = ()
    segments: tuple[SegmentFinding, ...] = ()


@dataclass(frozen=True)
class _Word:
    value: str
    operator: bool = False


@lru_cache(maxsize=1)
def _rules() -> tuple[dict, dict]:
    result = []
    for filename in ("allow.yaml", "hard-ask.yaml"):
        raw = files("scope").joinpath("rules", filename).read_bytes()
        if len(raw) > 65536:
            raise ValueError("oversize bundled policy")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("unsupported bundled policy")
        result.append(data)
    return result[0], result[1]


def _lex(command: str, ps: bool) -> tuple[list[list[_Word]], list[tuple[str, str]]]:
    """Tokenize literal words and operators without evaluating any input."""
    segments: list[list[_Word]] = [[]]
    flags: list[tuple[str, str]] = []
    word: list[str] = []
    started = False
    quote = ""
    i = 0

    def flush() -> None:
        nonlocal started
        if started:
            segments[-1].append(_Word("".join(word)))
            word.clear()
            started = False

    while i < len(command):
        c = command[i]
        nxt = command[i + 1:i + 2]
        if c == "\x00" or (ord(c) < 32 and c not in "\t\r\n") or (c == "\r" and (not ps or nxt != "\n")):
            flags.append(("T3", "control-character"))
        if quote == "'":
            if c == "'":
                if ps and nxt == "'":
                    word.append("'")
                    i += 2
                    continue
                quote = ""
            else:
                word.append(c)
            i += 1
            continue
        if c == ("`" if ps else "\\"):
            if ps:
                flags.append(("T3", "powershell-escape"))
            if not nxt:
                flags.append(("T2", "unfinished-escape"))
            elif nxt == "\n":
                i += 2
                continue
            elif quote == '"' and not ps and nxt not in '$`"\\':
                word.append(c)
                started = True
                i += 1
                continue
            else:
                word.append(nxt)
                started = True
            i += 2
            continue
        if c == "$" or (not ps and c == "`"):
            flags.append(("T3", "expansion-or-evaluation"))
        if quote == '"':
            if c == '"':
                quote = ""
            else:
                word.append(c)
            i += 1
            continue
        if c in "'\"":
            started = True
            quote = c
        elif c in " \t\r":
            flush()
        elif c == "#" and not started:
            flush()
            end = command.find("\n", i)
            i = len(command) if end < 0 else end
            continue
        elif c in ";\n|&":
            flush()
            if ps and c == "&":
                flags.append(("T3", "powershell-invocation"))
            elif c == "&" and nxt != "&":
                flags.append(("T2", "background-command"))
            if segments[-1]:
                segments.append([])
            if nxt == c and c in "|&":
                i += 1
        elif c in "<>":
            flush()
            if ps and c == "<" and nxt == "#":
                flags.append(("T3", "powershell-block-comment"))
            op = c
            if nxt == c or (c == ">" and nxt == "|") or (c == "<" and nxt == ">"):
                op += nxt
                i += 1
            if op == "<<":
                flags.append(("T3", "here-document"))
            segments[-1].append(_Word(op, True))
        elif c in "(){}":
            flags.append(("T3", "grouping-or-script-block"))
            word.append(c)
            started = True
        else:
            if ps and c in "@,“”‘’":
                flags.append(("T3", "powershell-dynamic-or-alternate-quoting"))
            word.append(c)
            started = True
        i += 1
    flush()
    if quote:
        flags.append(("T2", "unterminated-quote"))
    return [segment for segment in segments if segment], flags


def _windows(path: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]:", path) or path.startswith("\\"))


def _absolute(path: str, cwd: str | None) -> str | None:
    if not cwd or not path or any(c in path for c in "~*?[]\x00"):
        return None
    windows = _windows(cwd)
    if _windows(path) and not windows:
        return None
    module = ntpath if windows else posixpath
    if windows:
        drive, tail = ntpath.splitdrive(path)
        if ":" in tail:
            return None  # Providers and alternate data streams are not plain paths.
        if drive and not tail.startswith(("/", "\\")):
            return None  # A drive-relative path depends on hidden shell state.
        if not drive and path.startswith(("/", "\\")):
            return None
    elif "\\" in path:
        return None
    if not module.isabs(cwd):
        return None
    return module.normcase(module.normpath(module.join(cwd, path)))


def _inside(path: str, cwd: str | None, root: str) -> bool:
    absolute = _absolute(path, cwd)
    base = _absolute(root, root)
    if absolute is None or base is None:
        return False
    module = ntpath if _windows(root) else posixpath
    try:
        return module.commonpath((base, absolute)) == base
    except ValueError:
        return False


def _basename(command: str) -> tuple[str, bool]:
    name = command.replace("\\", "/").rsplit("/", 1)[-1]
    # Windows executable suffixes must not disguise a dangerous command on any OS.
    name = re.sub(r"\.(exe|cmd|bat|com)$", "", name, flags=re.I)
    return name.lower(), command != name


def _secret(value: str, hard: dict) -> bool:
    text = value.lower().replace("\\", "/")
    parts = re.split(r"[/=:]", text)
    return (any(part in hard["secret_parts"] or part == ".env"
                or part.startswith(".env.") or part.endswith(tuple(hard["secret_suffixes"]))
                for part in parts)
            or "env:" in text or "variable:" in text or "/proc/" in text and text.endswith("/environ")
            or ".config/gcloud" in text
            or any(secret in text for secret in ("authorization:", "aws_secret_access_key", "private_key")))


def _domains(values: list[str], *, network: bool = False) -> tuple[str, ...]:
    found = set()
    for value in values:
        for candidate in re.findall(r"(?:https?|ssh|ftp)://[^\s'\"<>]+", value, flags=re.I):
            try:
                host = urlsplit(candidate).hostname
                if host:
                    found.add(host.lower().rstrip("."))
            except ValueError:
                pass
        if network and re.fullmatch(r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}(?::[0-9]+)?(?:/[^\s]*)?", value):
            found.add(value.split("/", 1)[0].split(":", 1)[0].lower())
    return tuple(sorted(found))


_ALIASES = {
    "rm": "remove-item", "ri": "remove-item", "del": "remove-item", "erase": "remove-item",
    "rd": "remove-item", "rmdir": "remove-item", "gc": "get-content", "cat": "get-content",
    "type": "get-content", "ls": "get-childitem", "dir": "get-childitem", "gci": "get-childitem",
    "pwd": "get-location", "gl": "get-location", "cd": "set-location", "sl": "set-location",
    "chdir": "set-location", "sc": "set-content", "ac": "add-content", "ni": "new-item",
    "cp": "copy-item", "copy": "copy-item", "cpi": "copy-item", "mv": "move-item",
    "move": "move-item", "mi": "move-item", "iwr": "invoke-webrequest", "irm": "invoke-restmethod",
    "saps": "start-process", "start": "start-process", "spps": "stop-process",
    "clc": "clear-content", "cli": "clear-item", "ipmo": "import-module",
    "sal": "set-alias", "nal": "new-alias", "rni": "rename-item",
}
_WRITES = {"touch", "mkdir", "md", "cp", "mv", "copy", "move", "tee", "install",
           "set-content", "add-content", "out-file", "new-item", "copy-item", "move-item",
           "rename-item", "set-item", "set-itemproperty", "tee-object"}
_OUTPUT_FLAGS = {"--output", "--output-file", "--log-file", "--junitxml", "--html", "-filepath",
                 "-destination", "--target-directory", "-outfile", "-fprint", "-fprintf", "-fprint0", "-fls"}
_ENV_DANGEROUS = re.compile(r"^(?:PATH|PYTHONPATH|PYTHONHOME|PYTEST_ADDOPTS|LD_.*|DYLD_.*|BASH_ENV|ENV|SHELLOPTS|GIT_.*|NODE_OPTIONS|RUBYOPT|PERL5OPT)=", re.I)


def _segment(words: list[_Word], cwd: str | None, root: str, ps: bool,
             allow: dict, hard: dict) -> SegmentFinding:
    text = " ".join(word.value for word in words)
    findings: list[tuple[str, str]] = []

    def note(tier: str, rule: str) -> None:
        findings.append((tier, rule))

    def finish() -> SegmentFinding:
        tier, rule = max(findings or [("T2", "unknown-command")], key=lambda f: _RANK[f[0]])
        return SegmentFinding(text, tier, rule.replace("-", " "), rule)

    values = [word.value for word in words if not word.operator]
    if any(_secret(value, hard) for value in values):
        note("T3", "secret-access")
    argv = []
    i = 0
    while i < len(words):
        word = words[i]
        if word.operator:
            note("T2", "redirection")
            target = words[i + 1].value if i + 1 < len(words) else ""
            if (word.value.startswith(">") or word.value == "<>") and not _inside(target, cwd, root):
                note("T3", "outside-workspace-write")
            i += 2
        else:
            argv.append(word.value)
            i += 1
    if not argv:
        return finish()

    # Unwrap known prefixes only. They always lose automatic routine status.
    while argv:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]):
            note("T3" if _ENV_DANGEROUS.match(argv[0]) else "T2", "environment-prefix")
            argv = argv[1:]
            continue
        wrapper, _ = _basename(argv[0])
        if wrapper not in {"env", "command", "builtin", "time", "nice", "nohup"}:
            break
        note("T2", "command-wrapper")
        argv = argv[1:]
        while argv and argv[0].startswith("-"):
            option = argv.pop(0)
            if option not in {"--", "-u", "--unset", "-C", "--chdir", "-n", "--adjustment", "-o", "--output", "-p"}:
                note("T3", "unrecognized-wrapper-option")
            if wrapper == "env" and (option.startswith("-S") or option.startswith("--split-string")):
                note("T3", "environment-evaluation")
            if option in {"-u", "--unset", "-C", "--chdir", "-n", "--adjustment", "-o", "--output"}:
                if argv:
                    target = argv.pop(0)
                    if option in {"-C", "--chdir"}:
                        cwd = _absolute(target, cwd)
                    if option in {"-o", "--output"} and not _inside(target, cwd, root):
                        note("T3", "outside-workspace-write")
    if not argv:
        return finish()
    original_name = argv[0]
    name, custom = _basename(original_name)
    if any(c in original_name for c in "*?[]"):
        note("T3", "dynamic-executable")
    if custom:
        note("T2", "custom-executable")
    if not ps and name != original_name and not custom:
        note("T2", "noncanonical-executable")
    if ps:
        name = _ALIASES.get(name, name)
    args = argv[1:]
    lower = [arg.lower() if ps else arg for arg in args]

    for category in ("destructive", "privileged", "evaluation", "network_write"):
        if name in hard[category]:
            note("T3", category.replace("_", "-"))
    if name in {"timeout", "gtimeout", "watch", "xargs", "parallel", "if", "then", "else", "elif", "fi",
                "for", "while", "until", "do", "done", "case", "esac", "function", "select", "coproc", "!",
                "printenv", "set", "export", "declare", "typeset", "alias", "unalias"}:
        note("T3", "shell-control-or-environment")
    python = bool(re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+){0,2})?", name))
    if python or name in {"node", "nodejs", "ruby", "perl", "lua", "php"}:
        if any(arg in {"-c", "-e", "-r", "--eval", "--print", "-p"} or
               arg.startswith(("--eval=", "--require=", "--import=")) or
               (arg.startswith(("-c", "-e", "-p")) and len(arg) > 2) for arg in lower):
            note("T3", "interpreter-evaluation")
        if python and args[:2] == ["-m", "pytest"]:
            name, args, lower = "pytest", args[2:], lower[2:]
        elif python and any(arg == "-m" or arg.startswith("-m") for arg in args):
            note("T3", "unrecognized-module-execution")

    # Known flags that invoke other code or change how commands are interpreted.
    if any(arg in {"--pre", "--hostname-bin", "-exec", "-execdir", "-ok", "-okdir",
                   "--ext-diff", "--textconv", "--config-env", "--basetemp", "--override-ini", "--%"}
           or arg.startswith(("--pre=", "--hostname-bin=", "--config-env=", "--basetemp=", "--override-ini="))
           for arg in lower):
        note("T3", "execution-option")
    if name == "find" and "-delete" in lower:
        note("T3", "destructive")
    if name == "rg" and any(arg in {"--hidden", "--no-ignore", "--no-ignore-vcs", "-u", "-uu", "-uuu"} for arg in args):
        note("T3", "unrestricted-hidden-file-search")
    if name == "uniq":
        operands = [arg for arg in args if not arg.startswith("-")]
        if len(operands) > 1:
            note("T2" if _inside(operands[-1], cwd, root) else "T3", "output-path")
    if name == "pytest" and any(arg in {"-p", "-o"} or arg.startswith("-p") and len(arg) > 2 for arg in args):
        note("T3", "test-configuration-evaluation")

    for index, arg in enumerate(lower):
        key, equals, value = arg.partition("=")
        is_output = key in _OUTPUT_FLAGS or key in {"-o", "-t"} and name in {"curl", "wget", "sort", "cp", "mv"}
        if is_output and name != "git":
            target = args[index].split("=", 1)[1] if equals else (args[index + 1] if index + 1 < len(args) else "")
            note("T2" if _inside(target, cwd, root) else "T3", "output-path")
        if name == "sort" and arg.startswith("-o") and len(arg) > 2:
            note("T2" if _inside(args[index][2:], cwd, root) else "T3", "output-path")

    if name == "git":
        subargs = list(args)
        while subargs and subargs[0].startswith("-"):
            option = subargs.pop(0)
            note("T2", "git-global-option")
            if option == "-c" or option.startswith(("-c", "--config-env", "--exec-path")):
                note("T3", "git-configuration-evaluation")
            if option in {"-C", "--git-dir", "--work-tree"} and subargs:
                cwd = _absolute(subargs.pop(0), cwd)
            elif option.startswith(("--git-dir=", "--work-tree=")):
                cwd = _absolute(option.split("=", 1)[1], cwd)
            elif option.startswith("-C") and len(option) > 2:
                cwd = _absolute(option[2:], cwd)
            elif option in {"-c", "--config-env"} and subargs:
                subargs.pop(0)
        subcommand = subargs[0].lower() if subargs else ""
        # Git's own output file is relative to its -C/--work-tree context, while
        # shell redirections above stay relative to the shell's context.
        for index, arg in enumerate(subargs):
            key, equals, value = arg.partition("=")
            if key in _OUTPUT_FLAGS:
                target = value if equals else (subargs[index + 1] if index + 1 < len(subargs) else "")
                note("T2" if _inside(target, cwd, root) else "T3", "git-output-path")
        identity_config = (subcommand == "config" and len(subargs) == 3
                           and subargs[1] in {"user.name", "user.email"}
                           and not subargs[2].startswith("-"))
        if subcommand in hard["git_dangerous"] and not identity_config:
            note("T3", "git-" + subcommand)
        if subcommand in {"checkout", "branch", "tag"} and any(flag in subargs for flag in ("-f", "--force", "-D", "-d", "--delete")):
            note("T3", "git-destructive-option")
        if subcommand in allow["git_reads"]:
            approved = allow["git_reads"][subcommand]
            if all(arg in approved or arg == "--" or not arg.startswith("-") and _inside(arg, cwd, root)
                   for arg in subargs[1:]) and _inside(".", cwd, root):
                note("T1", "routine-git-" + subcommand)
            else:
                note("T2", "git-read-options-or-path")
        else:
            note("T2", "git-local-or-unknown-operation")
        if subcommand not in allow["git_reads"] and not _inside(".", cwd, root):
            note("T3", "outside-workspace-write")
    elif name in hard["network"]:
        note("T2", "network-read-or-unknown")
        if any(re.search(r"://[^/]*@", arg) for arg in args):
            note("T3", "network-credentials")
        # Curl/wget configs and abbreviated PowerShell parameters can conceal
        # writes. Unknown network switches therefore require a hard ask.
        read_switches = {"--silent", "--show-error", "--fail", "--location", "--head", "--verbose",
                         "--compressed", "--ipv4", "--ipv6", "-s", "-S", "-sS", "-I", "-L", "-f",
                         "-fsSL", "-sSL", "--spider", "--quiet", "-q", "-usebasicparsing"}
        read_values = {"--max-time", "--connect-timeout", "--url", "-uri", "--output", "-o", "-outfile"}
        for arg in args:
            option = arg.split("=", 1)[0]
            option = option.lower() if ps and name.startswith("invoke-") else option
            if arg.startswith("-") and option not in read_switches | read_values | {"-X", "--request", "--method", "-method"}:
                note("T3", "unrecognized-network-option")
        # Header/auth/request/body/upload options can transmit private data or write.
        network_flags = {"-x", "--request", "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
                         "-f", "--form", "-t", "--upload-file", "--post-data", "--post-file", "--method",
                         "-method", "-body", "-infile", "-credential", "-headers", "-h", "--header", "-u", "--user"}
        for index, arg in enumerate(args):
            opt = arg.lower().split("=", 1)[0]
            if opt in network_flags or any(arg.startswith(prefix) and len(arg) > len(prefix)
                                          for prefix in ("-X", "-d", "-F", "-T", "-H", "-u")):
                # Only explicitly named GET/HEAD methods are read-shaped.
                value = arg.split("=", 1)[1] if "=" in arg else (args[index + 1] if index + 1 < len(args) else "")
                if opt not in {"-x", "--request", "--method", "-method"} or value.upper() not in {"GET", "HEAD"}:
                    note("T3", "network-write-or-sensitive-payload")
    elif name in {"npm", "pnpm", "yarn", "pip", "pip3", "twine", "cargo", "docker", "gh", "uv"}:
        if any(arg in {"publish", "push", "upload", "release", "deploy", "install", "add", "exec", "dlx"} for arg in lower):
            note("T3", "publishing-network-or-execution")
        if name == "uv" and args[:2] == ["run", "pytest"]:
            nested = _segment([_Word(a) for a in args[1:]], cwd, root, ps, allow, hard)
            note(nested.name, nested.matched_rule or "nested-test")
        else:
            note("T2", "package-or-service-command")
    elif name in _WRITES:
        targets = [arg for arg in args if not arg.startswith("-")]
        if ps:
            # Named PS parameters also accept -Path:value without a space.
            targets.extend(arg.split(":", 1)[1] for arg in args if arg.startswith("-") and ":" in arg)
        if not targets or any(not _inside(target, cwd, root) for target in targets):
            note("T3", "outside-or-unknown-write-target")
        else:
            note("T2", "local-write")
    elif name in {"cd", "set-location", "pushd", "popd"}:
        note("T2", "working-directory-change")
    elif name == "pytest":
        if all(arg in allow["tests"] or not arg.startswith("-") and _inside(arg.split("::", 1)[0], cwd, root)
               for arg in args) and _inside(".", cwd, root):
            note("T1", "routine-pytest")
        else:
            note("T2", "test-options-or-path")
    elif name in allow["reads"]:
        approved = allow["reads"][name]
        positional = []
        okay = True
        after_options = False
        for arg in args:
            comparable = arg.lower() if ps else arg
            if arg == "--" and not after_options:
                after_options = True
            elif not after_options and arg.startswith("-"):
                okay = okay and comparable in approved
            else:
                positional.append(arg)
        # The first rg/grep operand is a literal search pattern, not a path.
        paths = positional[1:] if name in {"rg", "grep"} and "--files" not in args else positional
        if okay and all(_inside(path, cwd, root) for path in paths) and _inside(".", cwd, root):
            note("T1", "routine-read")
        else:
            note("T2", "read-options-or-path")
    else:
        note("T2", "unknown-command")
    return finish()


def classify(command: str, cwd: str, shell: str, description: str | None = None) -> Tier:
    """Classify without executing or inspecting input paths; description is data.

    domains and segment findings are immutable. Shell dialects are evaluated on
    every OS. Unknown/malformed/oversize inputs cannot produce T1.
    """
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND:
        return Tier("T2", "invalid or oversized command", "invalid-command")
    if not isinstance(cwd, str) or not isinstance(shell, str):
        return Tier("T2", "invalid context", "invalid-context")
    dialect = shell.lower().removesuffix(".exe")
    if dialect not in {"posix", "bash", "sh", "zsh", "powershell", "pwsh"}:
        # Still inspect dangerous forms with both dialects, but never allow.
        candidates = [classify(command, cwd, known, description) for known in ("posix", "powershell")]
        strictest = max(candidates, key=lambda item: _RANK[item.name])
        if strictest.name == "T3":
            return strictest
        return Tier("T2", "unsupported shell", "unsupported-shell", strictest.domains, strictest.segments)
    ps = dialect in {"powershell", "pwsh"}
    try:
        allow, hard = _rules()
        segments, flags = _lex(command, ps)
        findings = []
        current: str | None = cwd
        domains = set()
        for words in segments:
            findings.append(_segment(words, current, cwd, ps, allow, hard))
            values = [word.value for word in words if not word.operator]
            network = any(_ALIASES.get(_basename(value)[0], _basename(value)[0])
                          in hard["network"] + hard["network_write"] for value in values[:3])
            domains.update(_domains(values, network=network))
            if values:
                name, _ = _basename(values[0])
                name = _ALIASES.get(name, name) if ps else name
                if name in {"cd", "set-location", "pushd", "popd"}:
                    current = _absolute(values[1], current) if len(values) == 2 and name != "popd" else None
                elif name in {"command", "builtin", "time", "nice", "nohup", "env"} and any(
                    _ALIASES.get(_basename(value)[0], _basename(value)[0]) in {"cd", "set-location", "pushd", "popd"}
                    for value in values[1:]
                ):
                    # A wrapped directory change is not proven to have run, but
                    # later relative writes must account for that possibility.
                    current = None
        findings.extend(SegmentFinding(command, name, rule.replace("-", " "), rule) for name, rule in flags)
        if not findings:
            return Tier("T2", "no executable command", "empty-command")
        strictest = max(findings, key=lambda item: _RANK[item.name])
        return Tier(strictest.name, strictest.reason, strictest.matched_rule,
                    tuple(sorted(domains)), tuple(findings))
    except Exception:
        # Missing/corrupt policy and lexical errors never authorize.
        return Tier("T2", "classification unavailable", "classification-error")
