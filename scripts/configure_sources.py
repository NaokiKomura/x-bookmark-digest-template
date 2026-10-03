"""集める情報源を選ぶ（初回セットアップ用）。

コーディングエージェントが初回に利用者へ複数選択で尋ね、その答えを config/enabled.json に書く。
このファイルがないあいだは、すべての情報源を集める。X のブックマークは常に集めるので選択肢に入れない。

python -m scripts.configure_sources --list            選択肢を JSON で出す（グループごとの ID と表示名）
python -m scripts.configure_sources --enable a,b,c   選んだ ID の情報源だけを集める（空なら追加の情報源なし）
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from scripts.lib import store

TRENDS = [
    {"id": "github", "label": "GitHubトレンド"},
    {"id": "qiita", "label": "Qiita"},
    {"id": "zenn", "label": "Zenn"},
    {"id": "devio", "label": "DevelopersIO"},
]


def choices() -> list[dict[str, Any]]:
    """設問のグループと選択肢。公式ブログは企業ごとにまとめる（config/sources.json の company）。"""
    companies: dict[str, dict[str, Any]] = {}
    for blog in store.load_config("sources.json")["blogs"]:
        c = companies.setdefault(
            blog["company"], {"id": blog["company"], "label": blog["company_label"], "blogs": []}
        )
        c["blogs"].append(blog["blog"])
    blogs = [
        {"id": c["id"], "label": c["label"], "note": "、".join(c["blogs"])}
        for c in companies.values()
    ]
    return [
        {"group": "trends", "title": "トレンド", "options": TRENDS},
        {"group": "blogs", "title": "公式テックブログ", "options": blogs},
    ]


def write_enabled(ids: list[str]) -> list[str]:
    """選んだ ID を config/enabled.json に書く。知らない ID があれば何も書かずに止める。"""
    order = [o["id"] for g in choices() for o in g["options"]]
    unknown = sorted(set(ids) - set(order))
    if unknown:
        raise ValueError(f"unknown source ids: {', '.join(unknown)}")
    selected = [i for i in order if i in ids]
    store.write_json(
        store.ENABLED_PATH,
        {
            "_note": "集める情報源（scripts/configure_sources.py で作る）。ここにない情報源は取得しない",
            "sources": selected,
        },
    )
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="集める情報源を選ぶ")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="選択肢を JSON で出す")
    group.add_argument("--enable", help="集める情報源の ID（カンマ区切り）")
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps(choices(), ensure_ascii=False, indent=2))
        return 0
    ids = [i.strip() for i in args.enable.split(",") if i.strip()]
    try:
        selected = write_enabled(ids)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"enabled: {', '.join(selected) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
