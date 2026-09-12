"""Regression cases from independent lexical review; commands are inert data."""

import pytest

from scope.tiers import classify


WORKSPACES = {"posix": "/workspace/project", "powershell": r"C:\workspace\project"}


@pytest.mark.parametrize("shell", WORKSPACES)
@pytest.mark.parametrize(
    "command",
    [
        "git -C .. diff --output=outside.txt",
        "git -C .. log --output outside.txt",
        "git --work-tree=.. log --output=outside.txt",
        "curl --config payload.txt https://example.com",
        "curl -K payload.txt https://example.com",
        "curl -Kpayload.txt https://example.com",
        "curl --json @payload.json https://example.com",
        "curl --url-query @payload.txt https://example.com",
        "wget --config payload.txt https://example.com",
        "timeout 5 git push",
        "timeout --preserve-status 5 git push",
        "rg --pre sh needle .",
        "rg --hostname-bin custom-tool --hyperlink-format default needle .",
        'python3.12 -c "print(1)"',
        "node -p1+1",
        "python -m pip install package",
        "python -m twine upload dist/package.whl",
        "git --exec-path=custom-bin status",
    ],
)
def test_review_hard_asks_in_both_dialects(shell, command):
    assert classify(command, WORKSPACES[shell], shell).name == "T3"


@pytest.mark.parametrize(
    "command",
    [
        "command cd ..; touch outside.txt",
        "builtin cd ..; touch outside.txt",
        "command -p cd ..; touch outside.txt",
        "env --chdir=.. touch outside.txt",
        "env -C.. touch outside.txt",
        "time --output=/tmp/out pytest -q",
        "time -o/tmp/out pytest -q",
        "uniq notes.txt /tmp/out",
        "find . -fprint0 /tmp/out",
        "find . -fls /tmp/out",
        "find . -fprintf /tmp/out %p",
        "curl -o/tmp/out https://example.com",
        "curl --output-dir /tmp -O https://example.com/data",
        "wget -O/tmp/out https://example.com",
        "wget -P/tmp https://example.com",
        "cat foo\r#literal; git push",
    ],
)
def test_review_posix_wrappers_and_external_writes(command):
    assert classify(command, WORKSPACES["posix"], "posix").name == "T3"


@pytest.mark.parametrize(
    "command",
    [
        r"Microsoft.PowerShell.Management\Remove-Item -Recurse .",
        "Get-ChildItem Env:",
        "Invoke-WebRequest https://example.com -M Post -B data",
        r"Set-Content -Path:C:\Temp\out.txt -Value:test",
        r"Out-File -FilePath:C:\Temp\out.txt -InputObject:test",
        "clc data.txt",
        "cli data",
        "ipmo anything",
        "sal x Remove-Item",
        "nal x Remove-Item",
        r"rni C:\Temp\out.txt changed.txt",
        "Get-Content note#literal; Remove-Item note.txt",
        "git status HEAD#literal; git push",
        "pytest notes#literal; git push",
        'Get-Content "notes"#literal; git push',
        "Get-Content notes,.env",
        "Get-Content .env,notes",
        r"Set-Content notes,..\outside.txt hello",
        "Get-Content “abc #” ; git push",
        "Get-Content ‘abc #’ ; git push",
    ],
)
def test_review_powershell_aliases_options_and_providers(command):
    assert classify(command, WORKSPACES["powershell"], "powershell").name == "T3"


@pytest.mark.parametrize(
    "shell,command",
    [
        ("posix", "rg --files /tmp"),
        ("powershell", r"rg --files C:\Temp"),
        ("powershell", "Get-ChildItem Cert:"),
        ("powershell", "Get-ChildItem Variable:"),
        ("powershell", r"Get-Content Registry::HKEY_LOCAL_MACHINE\Software"),
    ],
)
def test_review_external_or_nonfilesystem_reads_never_auto_allow(shell, command):
    assert classify(command, WORKSPACES[shell], shell).name != "T1"


def test_shell_redirection_uses_shell_cwd_before_git_override():
    # The shell opens the redirect locally before Git changes its own directory.
    result = classify("git -C .. status > local.txt", WORKSPACES["posix"], "posix")
    assert result.name == "T2"


def test_git_output_uses_git_cwd_after_global_override():
    # Conversely, Git's own output option is interpreted in Git's changed cwd.
    result = classify(
        "cd ..; git -C project diff --output=local.txt", WORKSPACES["posix"], "posix"
    )
    assert result.name == "T2"
