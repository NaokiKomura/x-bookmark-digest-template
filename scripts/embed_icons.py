"""開発用: template/icons/ のロゴを template/report.html に data URI で埋め込み直す。

入力: template/icons/<id>.png・.jpg（80px の正方形。<id> は企業か情報源の ID）
出力: template/report.html の `.av.org[data-co="<id>"]` の行（ロゴを足す・差し替えたら `make icons`）

レポートは HTML 1ファイルで公開するので、画像は外から読み込まずにテンプレートへ入れる。
標準ライブラリだけを使う。
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template" / "report.html"
ICONS = ROOT / "template" / "icons"
MIME = {".png": "image/png", ".jpg": "image/jpeg"}
# この行の直後にロゴの行を並べる
ANCHOR = ".av.org[data-co]{"
ICON_RULE = re.compile(
    r'\n\.av\.org\[data-co="[a-z0-9_-]+"\]\{background-image:url\("data:[^"]*"\)\}'
)


def to_css_rules(icons_dir: Path) -> str:
    rules = []
    for icon in sorted(icons_dir.iterdir()):
        if icon.suffix not in MIME:
            raise SystemExit(f"{icon.name}: png か jpg にする")
        data = base64.b64encode(icon.read_bytes()).decode()
        rules.append(
            f'.av.org[data-co="{icon.stem}"]'
            f'{{background-image:url("data:{MIME[icon.suffix]};base64,{data}")}}'
        )
    return "".join("\n" + r for r in rules)


def embed(template: str, rules: str) -> str:
    start = template.find(ANCHOR)
    if start < 0:
        raise SystemExit(f"テンプレートに {ANCHOR} の行がない")
    end = template.index("\n", start)
    rest = ICON_RULE.sub("", template[end:])
    return template[:end] + rules + rest


def main() -> int:
    template = TEMPLATE.read_text(encoding="utf-8")
    updated = embed(template, to_css_rules(ICONS))
    if updated == template:
        print("変更なし")
        return 0
    TEMPLATE.write_text(updated, encoding="utf-8")
    print(f"{TEMPLATE.relative_to(ROOT)} のロゴを埋め込み直した")
    return 0


if __name__ == "__main__":
    sys.exit(main())
