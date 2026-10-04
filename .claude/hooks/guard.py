"""Claude Code の PreToolUse フック: AGENTS.md の「守ること」のうち、機械的に判定できるものを止める。

入力: 標準入力のフックのイベント（JSON。tool_name と tool_input）
出力: 止めるときは理由を標準エラーに書いて終了コード 2。通すときは終了コード 0

- `gh` のリポジトリ単位のコマンドに `-R` がない（remote が2つあるとテンプレート側を操作してしまう）
- `data/`・`state/` をエディタのツールで書き換える（ワークフローだけが書く）
- クラウドのセッション（ルーチン）で、履歴ブランチ以外へ push する（ROUTINE.md 守ること11）

安全網であり、権限の仕組みではない。読み取れないコマンドは止めずに通す。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

# -R（--repo）でリポジトリを選ぶ gh のサブコマンド
REPO_SCOPED_GH = {
    "pr",
    "issue",
    "workflow",
    "run",
    "secret",
    "variable",
    "release",
    "label",
    "cache",
}
# クラウドのセッションで push してよいブランチ
REPORT_BRANCHES = {"claude/reports", "codex/reports"}
SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
VARIABLE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def split_commands(command: str, env: dict[str, str]) -> list[list[str]] | None:
    """シェルのコマンド列を単純なコマンドごとの語の列に分ける。読み取れなければ None。

    先頭の代入（FOO=bar cmd、FOO=bar だけの行）は語から外し、後ろの語の $FOO・${FOO} に展開する。
    """
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    variables = dict(env)
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS or set(token) <= set(";&|()"):
            commands.append([])
            continue
        word = VARIABLE.sub(lambda m: variables.get(m.group(1), m.group(0)), token)
        if not commands[-1] and ASSIGNMENT.match(word):
            name, value = word.split("=", 1)
            variables[name] = value
            continue
        commands[-1].append(word)
    return [c for c in commands if c]


def git_subcommand(words: list[str]) -> tuple[str, list[str]] | None:
    """git のサブコマンドとその引数。git でなければ None。"""
    if not words or Path(words[0]).name != "git":
        return None
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    if i >= len(words):
        return None
    return words[i], words[i + 1 :]


def check_gh(words: list[str]) -> str | None:
    if not words or Path(words[0]).name != "gh" or len(words) < 2:
        return None
    if words[1] not in REPO_SCOPED_GH:
        return None
    if any(w in ("-R", "--repo") or w.startswith(("-R", "--repo=")) for w in words[2:]):
        return None
    return (
        f"`gh {words[1]}` には `-R <owner>/<repo>` を付ける"
        "（remote が2つあると、テンプレートのリポジトリを操作してしまうことがある。AGENTS.md）"
    )


def push_targets(words: list[str]) -> list[str] | None:
    """git push の送り先のブランチ名の列。push でなければ None。"""
    sub = git_subcommand(words)
    if sub is None or sub[0] != "push":
        return None
    args = sub[1]
    positional = [a for a in args if not a.startswith("-")]
    flags = [a for a in args if a.startswith("-")]
    if any(f in ("--all", "--mirror", "--tags", "--force", "-f", "--delete", "-d") for f in flags):
        return []
    targets = []
    for refspec in positional[1:]:
        if refspec.startswith(("+", ":")):
            return []
        dst = refspec.split(":", 1)[-1]
        targets.append(dst.removeprefix("refs/heads/"))
    return targets


def check_remote_push(words: list[str], remote: bool) -> str | None:
    if not remote:
        return None
    targets = push_targets(words)
    if targets is None:
        return None
    if targets and all(t in REPORT_BRANCHES for t in targets):
        return None
    return (
        "クラウドのセッションでは、push 先を `HEAD:refs/heads/claude/reports`（Codex は codex/reports）"
        "のように明示した履歴ブランチだけにする。main や送り先を省いた push はしない（ROUTINE.md 守ること11）"
    )


def check_edit(tool_input: dict[str, Any], project_dir: Path) -> str | None:
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = project_dir / path
    try:
        relative = path.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return None
    if relative.parts and relative.parts[0] in ("data", "state"):
        return (
            f"`{relative}` は取得ワークフローが書くファイル。手で編集しない"
            "（試すときは `make try` か一時ディレクトリの DIGEST_ROOT を使う。AGENTS.md 守ること3）"
        )
    return None


def check_event(
    event: dict[str, Any], project_dir: Path, remote: bool, env: dict[str, str] | None = None
) -> str | None:
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    if tool in EDIT_TOOLS:
        return check_edit(tool_input, project_dir)
    if tool != "Bash" or not isinstance(tool_input.get("command"), str):
        return None
    commands = split_commands(tool_input["command"], env or {})
    for words in commands or []:
        reason = check_gh(words) or check_remote_push(words, remote)
        if reason:
            return reason
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
    remote = os.environ.get("CLAUDE_CODE_REMOTE") == "true"
    reason = check_event(event, project_dir, remote, dict(os.environ))
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
