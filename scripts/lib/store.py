"""リポジトリ上のパス、日付、JSON の読み書き、設定の読み込み。

data/ と state/ の場所は `root()` で決まる。テストや試し実行では環境変数 `DIGEST_ROOT` で
一時ディレクトリに向ける。config/ は常にリポジトリのものを読む。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parents[2]


def root() -> Path:
    """data/ と state/ を読み書きする場所。"""
    return Path(os.environ.get("DIGEST_ROOT", REPO_ROOT))


def today_jst(now: datetime | None = None) -> date:
    """データの日付。`DIGEST_DATE`（YYYY-MM-DD）があればそれを使う（手動の再実行用）。"""
    override = os.environ.get("DIGEST_DATE")
    if override:
        return date.fromisoformat(override)
    return (now or datetime.now(JST)).astimezone(JST).date()


def previous_day(day: date) -> date:
    return day - timedelta(days=1)


def now_iso() -> str:
    return datetime.now(JST).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """一時ファイルに書いてから置き換える（途中で落ちても壊れたファイルを残さない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_config(name: str) -> Any:
    """config/<name> を読む。"""
    return read_json(REPO_ROOT / "config" / name, None)


ENABLED_PATH = REPO_ROOT / "config" / "enabled.json"
"""初回セットアップで選んだ情報源（scripts/configure_sources.py が書く）。テンプレートには入れない。"""


def load_enabled() -> set[str] | None:
    """集める情報源の ID の集合。config/enabled.json がなければ None（すべて集める）。"""
    data = read_json(ENABLED_PATH, None)
    return None if data is None else set(data.get("sources", []))


# ---------- data/ と state/ のパス（形は models.py） ----------


def bookmarks_path(day: date) -> Path:
    return root() / "data" / f"{day.isoformat()}.json"


def sources_path(day: date) -> Path:
    return root() / "data" / "sources" / f"{day.isoformat()}.json"


def excluded_path(day: date) -> Path:
    return root() / "data" / "excluded" / f"{day.isoformat()}.json"


def article_path(key: str) -> Path:
    return root() / "data" / "articles" / f"{key}.json"


def seen_ids_path() -> Path:
    return root() / "state" / "seen_ids.json"


def seen_urls_path() -> Path:
    return root() / "state" / "seen_urls.json"
