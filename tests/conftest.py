from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    """tests/fixtures/<name> を読む（サイトのページやフィードのスナップショット）。"""
    return (FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return fixture_bytes(name).decode("utf-8")


@pytest.fixture
def digest_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """data/ と state/ の読み書きを一時ディレクトリに向ける（リポジトリの data/ と state/ には書かない）。"""
    monkeypatch.setenv("DIGEST_ROOT", str(tmp_path))
    monkeypatch.setenv("DIGEST_DATE", "2026-10-03")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for sub in ("data/sources", "data/excluded", "data/articles", "state"):
        (tmp_path / sub).mkdir(parents=True)
    return tmp_path
