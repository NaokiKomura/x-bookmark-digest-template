"""ルーチン（要約層）が使う補助コマンド。標準ライブラリだけで動く（クラウド環境で依存を入れずに使える）。

scripts/lib を import しない（ルーチンは python3 scripts/report_tools.py として直接実行するため）。

python3 scripts/report_tools.py inputs  YYYY-MM-DD            当日の入力の有無と件数、要約が要る項目の一覧
python3 scripts/report_tools.py trend   YYYY-MM-DD            直近14日のブックマーク件数（report-data の trend）
python3 scripts/report_tools.py keywords [--reports DIR]      直近のレポートで使ったキーワードの一覧（表記の統一用）
python3 scripts/report_tools.py build   DATA.json OUT.html    テンプレートの report-data だけを差し替える
python3 scripts/report_tools.py validate OUT.html             差し替えたHTMLを検証する（公開の前に必ず通す）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "report.html"
BLOCK = re.compile(r'(<script type="application/json" id="report-data">)(.*?)(</script>)', re.S)
VISUAL_TYPES = {"before_after", "flow", "versus", "options", "stat"}
POST_KINDS = {"post", "quote", "article", "quote_article"}
FETCH_STATUSES = {"ok", "partial", "blocked", "error"}
SOURCE_STATES = {"ok", "none", "error"}
EXCLUDED_SOURCES = {"bookmarks", "qiita", "zenn"}
SOURCE_NAMES = {"bookmarks", "blogs", "github", "qiita", "zenn", "devio"}


def load_topics() -> list[dict[str, str]]:
    return json.loads((ROOT / "config" / "topics.json").read_text(encoding="utf-8"))["topics"]


# ---------- 入力 ----------


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def cmd_inputs(day: str) -> dict[str, Any]:
    bookmarks = read(ROOT / "data" / f"{day}.json")
    sources = read(ROOT / "data" / "sources" / f"{day}.json")
    excluded = read(ROOT / "data" / "excluded" / f"{day}.json")
    out: dict[str, Any] = {
        "date": day,
        "has_bookmarks": bookmarks is not None,
        "has_sources": sources is not None,
    }
    if bookmarks is not None:
        posts = bookmarks["posts"]
        out["bookmarks"] = {
            "total": len(posts),
            "error": bookmarks.get("error"),
            "by_label": count_by(
                posts, lambda p: (p.get("jev") or {}).get("tech_label") or "unavailable"
            ),
        }
    if sources is not None:
        out["sources"] = {
            name: len(sources.get(name, []))
            for name in ("github", "qiita", "zenn", "devio", "blogs")
        }
        out["status"] = sources.get("status", {})
        out["blog_status"] = sources.get("blog_status", [])
        out["errors"] = sources.get("errors", [])
        out["jev_unavailable"] = sum(
            1
            for name in ("github", "qiita", "zenn", "devio", "blogs")
            for item in sources.get(name, [])
            if (item.get("jev") or {}).get("status") != "ok"
        )
        out["streaks"] = [
            {"source": name, "article_key": i["article_key"], "streak_days": i["streak_days"]}
            for name in ("github", "qiita", "zenn", "devio")
            for i in sources.get(name, [])
            if i.get("streak_days", 1) >= 2
        ]
    out["excluded"] = len((excluded or {}).get("items", []))
    return out


def count_by(items: list[Any], key: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        k = key(item)
        counts[k] = counts.get(k, 0) + 1
    return counts


def cmd_trend(day: str, days: int = 14) -> list[dict[str, Any]]:
    """data/ 配下の日別ファイルの件数。ファイルがない日は 0 件。除外も含めた新着ブックマークの件数。"""
    end = date.fromisoformat(day)
    trend = []
    for i in range(days - 1, -1, -1):
        d = (end - timedelta(days=i)).isoformat()
        data = read(ROOT / "data" / f"{d}.json")
        trend.append({"date": d, "count": len(data["posts"]) if data else 0})
    return trend


def cmd_keywords(reports_dir: Path, limit: int = 7) -> dict[str, int]:
    """直近 limit 件のレポートに出たキーワードと出現回数。"""
    files = sorted(reports_dir.glob("*.html"))[-limit:]
    counts: dict[str, int] = {}
    for f in files:
        data = extract_data(f.read_text(encoding="utf-8"))
        if not data:
            continue
        for section in ("posts", "blogs", "github", "articles"):
            for item in data.get(section, []):
                for k in item.get("keywords", []):
                    counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ---------- 組み立てと検証 ----------


def extract_data(html: str) -> Any:
    match = BLOCK.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(2))
    except json.JSONDecodeError:
        return None


def embed(data: Any) -> str:
    """script 要素の中に安全に置ける JSON にする（</script> で閉じられないよう < をエスケープ）。"""
    text = json.dumps(data, ensure_ascii=False, indent=1)
    return (
        "\n"
        + text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        + "\n"
    )


def cmd_build(data_path: Path, out_path: Path) -> None:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    errors = validate_data(data)
    if errors:
        raise SystemExit("report-data の検証に失敗: " + "; ".join(errors))
    template = TEMPLATE.read_text(encoding="utf-8")
    if not BLOCK.search(template):
        raise SystemExit("template has no report-data block")
    html = BLOCK.sub(lambda m: m.group(1) + embed(data) + m.group(3), template, count=1)
    out_path.write_text(html, encoding="utf-8")


def outside_block(html: str) -> str:
    return BLOCK.sub(lambda m: m.group(1) + m.group(3), html, count=1)


def validate_data(data: Any) -> list[str]:
    """report-data の形を確かめる。問題の一覧を返す（空なら合格）。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["report-data がオブジェクトではない"]
    topic_ids = {t["id"] for t in load_topics()}

    def need(cond: bool, message: str) -> None:
        if not cond:
            errors.append(message)

    need(
        bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data.get("date", "")))),
        "date が YYYY-MM-DD でない",
    )
    need(isinstance(data.get("lede"), str), "lede がない")
    for key in (
        "themes",
        "picks",
        "trend",
        "posts",
        "github",
        "articles",
        "blogs",
        "source_status",
        "excluded",
    ):
        need(isinstance(data.get(key), list), f"{key} が配列でない")
    if errors:
        return errors

    for t in data["themes"]:
        need(t.get("id") in topic_ids, f"themes に一覧にないトピック: {t.get('id')}")

    ids: set[str] = set()

    def check_item(section: str, item: dict[str, Any]) -> None:
        iid = str(item.get("id", ""))
        need(bool(iid), f"{section}: id がない")
        need(iid not in ids, f"{section}: id が重複: {iid}")
        ids.add(iid)
        need(
            item.get("theme") in topic_ids,
            f"{section} {iid}: theme が一覧にない: {item.get('theme')}",
        )
        need(
            isinstance(item.get("title"), str) and bool(item.get("title")),
            f"{section} {iid}: title がない",
        )
        url = item.get("url")
        need(
            url is None or (isinstance(url, str) and url.startswith("https://")),
            f"{section} {iid}: url が https でない",
        )
        if "visual" in item and item["visual"] is not None:
            need(item["visual"].get("type") in VISUAL_TYPES, f"{section} {iid}: visual.type が不明")
        for k in ("keywords", "points"):
            if k in item:
                need(isinstance(item[k], list), f"{section} {iid}: {k} が配列でない")

    for p in data["posts"]:
        check_item("posts", p)
        need(p.get("kind") in POST_KINDS, f"posts {p.get('id')}: kind が不明")
        need(1 <= len(p.get("points", [])) <= 3, f"posts {p.get('id')}: points は1〜3個")
        need(2 <= len(p.get("keywords", [])) <= 3, f"posts {p.get('id')}: keywords は2〜3個")
        need(p.get("importance") in (1, 2, 3), f"posts {p.get('id')}: importance は1〜3")
        need(isinstance(p.get("read_min"), int | float), f"posts {p.get('id')}: read_min がない")
        if p.get("kind") in ("article", "quote_article"):
            art = p.get("article") or {}
            need(
                art.get("fetch_status") in FETCH_STATUSES,
                f"posts {p.get('id')}: article.fetch_status が不明",
            )
            need(
                str(art.get("url", "")).startswith("https://") or not art.get("url"),
                f"posts {p.get('id')}: article.url が https でない",
            )
        if p.get("kind") in ("quote", "quote_article"):
            need(isinstance(p.get("quoted"), dict), f"posts {p.get('id')}: quoted がない")
    for section in ("github", "articles", "blogs"):
        for item in data[section]:
            check_item(section, item)
    for b in data["blogs"]:
        need(
            b.get("fetch_status") in FETCH_STATUSES,
            f"blogs {b.get('id')}: fetch_status が不明",
        )
    for a in data["articles"]:
        need(a.get("site") in ("qiita", "zenn", "devio"), f"articles {a.get('id')}: site が不明")
    for s in data["source_status"]:
        need(s.get("status") in SOURCE_STATES, f"source_status {s.get('source')}: status が不明")
        need(s.get("source") in SOURCE_NAMES, f"source_status: source が不明: {s.get('source')}")
    for x in data["excluded"]:
        need(x.get("source") in EXCLUDED_SOURCES, f"excluded: source が不明: {x.get('source')}")
    for pid in data["picks"]:
        need(pid in ids, f"picks に存在しない項目: {pid}")
    need(len(data["picks"]) <= 3, "picks は3件まで")
    return errors


def cmd_validate(html_path: Path) -> list[str]:
    html = html_path.read_text(encoding="utf-8")
    errors = []
    if outside_block(html) != outside_block(TEMPLATE.read_text(encoding="utf-8")):
        errors.append("report-data 以外の部分がテンプレートと違う")
    data = extract_data(html)
    if data is None:
        errors.append("report-data を JSON として読めない")
    else:
        errors += validate_data(data)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ルーチン用の補助コマンド")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inputs")
    p.add_argument("date")
    p = sub.add_parser("trend")
    p.add_argument("date")
    p = sub.add_parser("keywords")
    p.add_argument("--reports", default="reports")
    p = sub.add_parser("build")
    p.add_argument("data")
    p.add_argument("out")
    p = sub.add_parser("validate")
    p.add_argument("html")
    args = parser.parse_args(argv)

    if args.cmd == "inputs":
        print(json.dumps(cmd_inputs(args.date), ensure_ascii=False, indent=1))
    elif args.cmd == "trend":
        print(json.dumps(cmd_trend(args.date), ensure_ascii=False))
    elif args.cmd == "keywords":
        print(json.dumps(cmd_keywords(Path(args.reports)), ensure_ascii=False, indent=1))
    elif args.cmd == "build":
        cmd_build(Path(args.data), Path(args.out))
        print(f"wrote {args.out}")
    elif args.cmd == "validate":
        errors = cmd_validate(Path(args.html))
        if errors:
            print("NG")
            for e in errors:
                print(" - " + e)
            return 1
        print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
