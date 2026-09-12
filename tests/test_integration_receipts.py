"""Real A1/permission seams, without inventing the unpublished A2 workflow."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

from scope import ipc, learning, log, receipt
from scope.watch import Watcher


def test_real_task_snapshot_and_permission_record_share_one_receipt(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    (repo / "fixture.py").write_text("value = 1\n")
    task = learning.start(repo, "Understand the fixture value", session_id="joined")
    log.append("joined", "permission_request", request_id="r1", command="pytest -q", cwd=str(repo), shell="bash", tier="T1")
    log.append("joined", "permission_decision", request_id="r1", behavior="allow")
    result = receipt.write("joined")
    assert result["counts"]["requests"] == result["counts"]["auto_allowed"] == 1
    assert result["understanding"]["tasks"][0]["fields"]["task_id"] == task["task_id"]
    assert result["understanding"]["predictions"] == result["understanding"]["executions"] == []
    assert result["actions"][0]["execution"] == "unknown"


def test_shared_revoke_is_visible_in_understanding_projection(tmp_path):
    class FixtureUI:
        interactive = False
    with Watcher(FixtureUI()) as watcher:
        assert ipc.exchange({"kind": "proposal", "session_id": "joined", "cwd": str(tmp_path),
                             "card": {"summary": "fixture", "commands": ["touch fixture"], "domains": [], "budget": 1}}) == {"accepted": True}
        assert ipc.exchange({"kind": "revoke"}) == {"revoked": True}
        result = receipt.write("joined")
    assert result["counts"]["scopes_granted"] == 0
    assert result["understanding"]["revocations"][0]["fields"]["revoked"] is True
