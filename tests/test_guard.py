import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard.py"
spec = importlib.util.spec_from_file_location("guard", GUARD)
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

ROOT = Path("/repo")


def bash(command: str, remote: bool = False, env: dict[str, str] | None = None) -> str | None:
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    return guard.check_event(event, ROOT, remote, env)


@pytest.mark.parametrize(
    "command",
    [
        "gh workflow run fetch",
        "gh secret list",
        "cd x && gh variable set DIGEST_ENABLED --body true",
        "GH_PAGER= gh run watch",
    ],
)
def test_gh_without_repo_is_blocked(command):
    assert "-R" in bash(command)


@pytest.mark.parametrize(
    "command",
    [
        "gh workflow run fetch -R me/x-bookmark-digest",
        "gh secret list --repo me/x-bookmark-digest",
        "gh run watch -Rme/x-bookmark-digest",
        "gh api repos/me/x/rulesets",
        "gh auth status",
        "echo 'gh workflow run fetch'",
    ],
)
def test_gh_with_repo_or_not_repo_scoped_passes(command):
    assert bash(command) is None


@pytest.mark.parametrize(
    "path", ["/repo/data/2026-10-04.json", "/repo/state/seen_ids.json", "data/sources/x.json"]
)
def test_editing_data_and_state_is_blocked(path):
    event = {"tool_name": "Edit", "tool_input": {"file_path": path}}
    assert "手で編集しない" in guard.check_event(event, ROOT, False)


@pytest.mark.parametrize(
    "path", ["/repo/template/report.html", "/repo/docs/data.md", "/tmp/data/x"]
)
def test_editing_other_files_passes(path):
    event = {"tool_name": "Write", "tool_input": {"file_path": path}}
    assert guard.check_event(event, ROOT, False) is None


@pytest.mark.parametrize(
    "command",
    [
        "git -C /tmp/reports-branch push origin HEAD:refs/heads/claude/reports",
        "git push origin HEAD:codex/reports",
        # ROUTINE.md・PUBLISH.md のように同じ呼び出しの中で決めた変数
        'REPORT_BRANCH=claude/reports\ngit -C /tmp/b push origin "HEAD:refs/heads/$REPORT_BRANCH"',
        'case "$A" in claude) REPORT_BRANCH=claude/reports ;; esac; git push origin '
        '"HEAD:refs/heads/${REPORT_BRANCH}"',
        "git commit -m push",
    ],
)
def test_remote_push_to_report_branch_passes(command):
    assert bash(command, remote=True) is None


def test_remote_push_uses_environment_variables():
    command = 'git push origin "HEAD:refs/heads/$REPORT_BRANCH"'
    assert bash(command, remote=True, env={"REPORT_BRANCH": "claude/reports"}) is None
    assert bash(command, remote=True) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push origin main",
        "git push origin HEAD:refs/heads/main",
        "git push origin HEAD:claude/reports HEAD:main",
        "git push --force origin HEAD:claude/reports",
        "git push origin +HEAD:claude/reports",
        "git push origin :claude/reports",
        "git -C repo push --all origin",
    ],
)
def test_remote_push_elsewhere_is_blocked(command):
    assert "ROUTINE.md" in bash(command, remote=True)


def test_local_push_is_not_checked():
    assert bash("git push origin main") is None


def test_unreadable_command_passes():
    assert bash("echo 'unterminated") is None


def test_script_exit_codes(tmp_path):
    def run(event: dict, remote: str = "") -> subprocess.CompletedProcess[str]:
        env = {"CLAUDE_PROJECT_DIR": str(tmp_path), "CLAUDE_CODE_REMOTE": remote, "PATH": ""}
        return subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    blocked = run({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}, "true")
    assert blocked.returncode == 2 and "ROUTINE.md" in blocked.stderr
    allowed = run({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}})
    assert allowed.returncode == 0
    assert run({"tool_name": "Bash"}).returncode == 0
