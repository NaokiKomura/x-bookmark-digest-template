from pathlib import Path

import pytest


@pytest.fixture
def digest_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """データの読み書きを一時ディレクトリに向ける（リポジトリの data/ と state/ には書かない）。"""
    monkeypatch.setenv("DIGEST_ROOT", str(tmp_path))
    monkeypatch.setenv("DIGEST_DATE", "2026-10-03")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for sub in ("data/sources", "data/excluded", "data/articles", "state"):
        (tmp_path / sub).mkdir(parents=True)
    return tmp_path
