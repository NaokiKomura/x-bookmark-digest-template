"""ルーチン（要約層）が使う補助コマンド。標準ライブラリだけで動く（クラウド環境で依存を入れずに使える）。

scripts/lib を import しない（ルーチンは python3 scripts/report_tools.py として直接実行するため）。

python3 scripts/report_tools.py inputs  YYYY-MM-DD            当日の入力の有無と件数、要約が要る項目の一覧
python3 scripts/report_tools.py trend   YYYY-MM-DD            直近14日のブックマーク件数（report-data の trend）
python3 scripts/report_tools.py keywords [--reports DIR]      直近のレポートで使ったキーワードの一覧（表記の統一用）
python3 scripts/report_tools.py build   DATA.json OUT.html    テンプレートの report-data だけを差し替える
python3 scripts/report_tools.py validate OUT.html             差し替えたHTMLを検証する（公開の前に必ず通す）
python3 scripts/report_tools.py carryover YYYY-MM-DD [--reports DIR]  前日から3日前までのレポートの項目（report-data の carryover）
python3 scripts/report_tools.py archive YYYY-MM-DD [--reports DIR]    前日から14日前までのレポートの索引（report-data の archive）
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
VISUAL_TYPES = {"before_after", "flow", "versus", "options", "stat", "matrix"}
MATRIX_LEVELS = (1, 2, 3)
POST_KINDS = {"post", "quote", "article", "quote_article"}
FETCH_STATUSES = {"ok", "partial", "blocked", "error"}
SOURCE_STATES = {"ok", "none", "error"}
EXCLUDED_SOURCES = {"bookmarks", "qiita", "zenn"}
SOURCE_NAMES = {"bookmarks", "blogs", "github", "qiita", "zenn", "devio"}
MAX_READ_MIN = 15
CARRY_DAYS = 3
"""未読の項目を翌日以降のレポートに繰り越す日数（元の日から数える）。"""
CARRY_SECTIONS = ("posts", "blogs", "github", "articles")
ARCHIVE_DAYS = 14
"""「過去の日報」に索引を載せる日数（前日から数える）。"""
ARCHIVE_NOTE_CHARS = 120
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_number(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


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


# ---------- 繰り越し ----------


def item_errors(section: str, item: Any) -> list[str]:
    """report-data の1項目を、その項目だけを持つレポートとして検証する。"""
    if not isinstance(item, dict):
        return ["項目がオブジェクトでない"]
    report: dict[str, Any] = {
        "date": "2000-01-01",
        "lede": "",
        "section_summaries": {},
        **{key: [] for key in ("themes", "picks", "trend", "source_status", "excluded")},
        **{key: [] for key in CARRY_SECTIONS},
    }
    report[section] = [item]
    return validate_data(report)


def cmd_carryover(day: str, reports_dir: Path) -> list[dict[str, Any]]:
    """前日から CARRY_DAYS 日前までのレポートの項目。前日のレポートの繰り越し分は含めない（最大3日にするため）。

    レポートがない日、日付が合わない日、今の形で検証を通らない項目は飛ばす。
    どれを表示するか（既読でないもの）は、閲覧者のブラウザでテンプレートが決める。
    """
    end = date.fromisoformat(day)
    out: list[dict[str, Any]] = []
    for i in range(1, CARRY_DAYS + 1):
        d = (end - timedelta(days=i)).isoformat()
        path = reports_dir / f"{d}.html"
        if not path.is_file():
            continue
        data = extract_data(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("date") != d:
            continue
        entry: dict[str, Any] = {"date": d}
        for section in CARRY_SECTIONS:
            items = data.get(section)
            entry[section] = [
                item
                for item in (items if isinstance(items, list) else [])
                if not item_errors(section, item)
            ]
        if any(entry[section] for section in CARRY_SECTIONS):
            out.append(entry)
    return out


# ---------- 過去の日報 ----------


def to_archive_item(section: str, item: dict[str, Any]) -> dict[str, Any] | None:
    """レポートの1項目を、索引の1行（見出し・リンク・出どころ・トピック・短い要点）にする。"""
    title, item_id = item.get("title"), item.get("id")
    if not isinstance(title, str) or not title or not isinstance(item_id, str) or not item_id:
        return None
    if section == "posts":
        source, label = "bookmarks", "@" + str(item.get("handle", ""))
    elif section == "blogs":
        source, label = "blogs", str(item.get("company_label") or item.get("company") or "")
    elif section == "github":
        source, label = "github", str(item.get("language") or "")
    else:
        source, label = str(item.get("site", "")), ""
    points = item.get("points")
    note = points[0] if isinstance(points, list) and points else item.get("summary")
    url = item.get("url")
    out: dict[str, Any] = {
        "id": item_id,
        "source": source,
        "title": title,
        "url": url if isinstance(url, str) and url.startswith("https://") else "",
        "theme": item.get("theme"),
    }
    if label:
        out["label"] = label
    if section == "blogs" and isinstance(item.get("company"), str):
        out["company"] = item["company"]
    if isinstance(note, str) and note:
        out["note"] = note[:ARCHIVE_NOTE_CHARS]
    return out


def cmd_archive(day: str, reports_dir: Path) -> list[dict[str, Any]]:
    """前日から ARCHIVE_DAYS 日前までのレポートの索引。新しい日が先。

    レポートがない日、日付が合わない日、索引の形にならない項目は飛ばす。各日の繰り越し分は含めない。
    """
    end = date.fromisoformat(day)
    topic_ids = {t["id"] for t in load_topics()}
    out: list[dict[str, Any]] = []
    for i in range(1, ARCHIVE_DAYS + 1):
        d = (end - timedelta(days=i)).isoformat()
        path = reports_dir / f"{d}.html"
        if not path.is_file():
            continue
        data = extract_data(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("date") != d:
            continue
        items = []
        for section in CARRY_SECTIONS:
            for item in data.get(section) or []:
                row = to_archive_item(section, item) if isinstance(item, dict) else None
                if row and not archive_item_errors(row, topic_ids):
                    items.append(row)
        if items:
            out.append({"date": d, "items": items})
    return out


def archive_item_errors(row: Any, topic_ids: set[str]) -> list[str]:
    if not isinstance(row, dict):
        return ["項目がオブジェクトでない"]
    errors = []
    if not isinstance(row.get("id"), str) or not row["id"]:
        errors.append("id がない")
    if row.get("source") not in SOURCE_NAMES:
        errors.append(f"source が不明: {row.get('source')}")
    if not isinstance(row.get("title"), str) or not row["title"]:
        errors.append("title がない")
    url = row.get("url")
    if not isinstance(url, str) or (url and not url.startswith("https://")):
        errors.append("url は https:// で始まる文字列か空文字")
    if row.get("theme") not in topic_ids:
        errors.append(f"theme が不明: {row.get('theme')}")
    for key in ("label", "note", "company"):
        if key in row and not isinstance(row[key], str):
            errors.append(f"{key} が文字列でない")
    return errors


def validate_archive(archive: Any, day: str) -> list[str]:
    """archive は任意。当日より前の日ごとに、索引の行（to_archive_item の形）を持つ。"""
    if not isinstance(archive, list) or len(archive) > ARCHIVE_DAYS:
        return [f"archive は{ARCHIVE_DAYS}日分までの配列"]
    topic_ids = {t["id"] for t in load_topics()}
    errors: list[str] = []
    for entry in archive:
        if not isinstance(entry, dict) or not isinstance(entry.get("items"), list):
            errors.append("archive の要素は date と items を持つオブジェクト")
            continue
        d = str(entry.get("date", ""))
        if not DATE_RE.fullmatch(d) or d >= day:
            errors.append(f"archive.date は当日より前の YYYY-MM-DD: {d}")
        for row in entry["items"]:
            errors += [f"archive {d}: {e}" for e in archive_item_errors(row, topic_ids)]
    return errors


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
    sums = data.get("section_summaries", {})
    need(
        isinstance(sums, dict)
        and set(sums) <= {"blogs", "trends"}
        and all(isinstance(v, str) for v in sums.values()),
        "section_summaries は blogs・trends の文字列だけ",
    )
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

    def check_visual(where: str, v: Any) -> None:
        if not isinstance(v, dict) or v.get("type") not in VISUAL_TYPES:
            need(False, f"{where}: visual.type が不明")
            return
        kind = v["type"]
        if kind == "flow":
            steps = v.get("steps")
            need(
                isinstance(steps, list)
                and bool(steps)
                and all(
                    isinstance(s, str) or (isinstance(s, dict) and isinstance(s.get("label"), str))
                    for s in steps
                ),
                f"{where}: flow.steps は文字列か {{label, detail?}} の配列",
            )
        elif kind == "options":
            items = v.get("items")
            need(
                isinstance(items, list)
                and all(isinstance(i, dict) and i.get("name") for i in items),
                f"{where}: options.items に name がない",
            )
            for i in items if isinstance(items, list) else []:
                if isinstance(i, dict) and "value" in i:
                    need(is_number(i["value"]), f"{where}: options.items.value が数値でない")
        elif kind == "versus":
            for c in v.get("criteria") or []:
                need(
                    isinstance(c, dict)
                    and bool(c.get("name"))
                    and c.get("better") in (None, "a", "b"),
                    f"{where}: versus.criteria は name と better（a/b）",
                )
        elif kind == "stat":
            need(is_number(v.get("value")), f"{where}: stat.value が数値でない")
            if v.get("compare") is not None:
                cmp = v["compare"]
                need(
                    isinstance(cmp, dict)
                    and is_number(cmp.get("value"))
                    and bool(cmp.get("label")),
                    f"{where}: stat.compare は value と label",
                )
        elif kind == "matrix":
            items = v.get("items")
            need(
                isinstance(v.get("x"), dict) and isinstance(v.get("y"), dict),
                f"{where}: matrix に x と y の軸がない",
            )
            need(
                isinstance(items, list)
                and 1 <= len(items) <= 8
                and all(
                    isinstance(i, dict)
                    and i.get("name")
                    and i.get("x") in MATRIX_LEVELS
                    and i.get("y") in MATRIX_LEVELS
                    for i in items
                ),
                f"{where}: matrix.items は1〜8個で、x と y は 1〜3",
            )

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
            check_visual(f"{section} {iid}", item["visual"])
        for k in ("keywords", "points"):
            if k in item:
                need(isinstance(item[k], list), f"{section} {iid}: {k} が配列でない")

    for p in data["posts"]:
        check_item("posts", p)
        need(p.get("kind") in POST_KINDS, f"posts {p.get('id')}: kind が不明")
        need(1 <= len(p.get("points", [])) <= 3, f"posts {p.get('id')}: points は1〜3個")
        need(2 <= len(p.get("keywords", [])) <= 3, f"posts {p.get('id')}: keywords は2〜3個")
        need(p.get("importance") in (1, 2, 3), f"posts {p.get('id')}: importance は1〜3")
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
    for section in ("posts", "blogs"):
        for item in data[section]:
            rm = item.get("read_min")
            need(
                isinstance(rm, int | float) and 1 <= rm <= MAX_READ_MIN,
                f"{section} {item.get('id')}: read_min は1〜{MAX_READ_MIN}",
            )
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
    reasons = data.get("pick_reasons", {})
    need(
        isinstance(reasons, dict)
        and all(
            key in data["picks"] and isinstance(value, str) and bool(value.strip())
            for key, value in reasons.items()
        ),
        "pick_reasons は picks のIDをキーとする空でない文字列のオブジェクト",
    )
    errors += validate_carryover(data.get("carryover", []), str(data["date"]))
    errors += validate_archive(data.get("archive", []), str(data["date"]))
    return errors


def validate_carryover(carry: Any, day: str) -> list[str]:
    """carryover は任意。当日より前の日ごとに、その日のレポートの posts・blogs・github・articles を持つ。"""
    if not isinstance(carry, list) or len(carry) > CARRY_DAYS:
        return [f"carryover は{CARRY_DAYS}日分までの配列"]
    errors: list[str] = []
    for entry in carry:
        if not isinstance(entry, dict):
            errors.append("carryover の要素がオブジェクトでない")
            continue
        d = str(entry.get("date", ""))
        if not DATE_RE.fullmatch(d) or d >= day:
            errors.append(f"carryover.date は当日より前の YYYY-MM-DD: {d}")
        for section in CARRY_SECTIONS:
            items = entry.get(section, [])
            if not isinstance(items, list):
                errors.append(f"carryover {d}: {section} が配列でない")
                continue
            for item in items:
                errors += [f"carryover {d}: {e}" for e in item_errors(section, item)]
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
    p = sub.add_parser("carryover")
    p.add_argument("date")
    p.add_argument("--reports", default="reports")
    p = sub.add_parser("archive")
    p.add_argument("date")
    p.add_argument("--reports", default="reports")
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
    elif args.cmd == "carryover":
        print(json.dumps(cmd_carryover(args.date, Path(args.reports)), ensure_ascii=False))
    elif args.cmd == "archive":
        print(json.dumps(cmd_archive(args.date, Path(args.reports)), ensure_ascii=False))
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
