"""開発用: テンプレートの見た目をヘッドレス Chrome で撮る（`make shots`）。

入力: template/report.html（サンプルデータ入り）
出力: /tmp/x-bookmark-digest-shots/<名前>.png（画面・幅・ダークモードの組み合わせ）

幅の広い画面（3列）、中くらいの画面（2列）、スマホ幅、ダークモードを撮る。
ヘッドレス Chrome は幅を一定より狭くできないので、スマホ幅は iframe に入れて撮る。
Chrome の場所は環境変数 CHROME で指定できる。標準ライブラリだけを使う。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template" / "report.html"
OUT_DIR = Path("/tmp/x-bookmark-digest-shots")
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MOBILE_WIDTH = 390


@dataclass(frozen=True)
class Shot:
    name: str
    view: str  # report.html の #view=...（空なら概要）
    width: int
    height: int
    dark: bool = False


SHOTS = [
    Shot("overview-wide", "", 1440, 1300),
    Shot("bookmarks-wide", "bm", 1440, 1300),
    Shot("blogs-wide", "blog", 1440, 1300),
    Shot("trends-medium", "tr", 1100, 1300),
    Shot("bookmarks-dark", "bm", 1440, 1100, dark=True),
    Shot("overview-mobile", "", MOBILE_WIDTH, 1500),
    Shot("bookmarks-mobile", "bm", MOBILE_WIDTH, 1500),
]


def find_chrome() -> str:
    for candidate in (os.environ.get("CHROME"), MAC_CHROME):
        if candidate and Path(candidate).exists():
            return candidate
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("Chrome が見つからない。環境変数 CHROME に実行ファイルのパスを入れる")


def page_url(shot: Shot) -> str:
    return TEMPLATE.as_uri() + (f"#view={shot.view}" if shot.view else "")


def target_url(shot: Shot, out_dir: Path) -> str:
    """撮る URL。スマホ幅は iframe に入れたページを書き出して、その URL を返す。"""
    if shot.width > MOBILE_WIDTH:
        return page_url(shot)
    wrapper = out_dir / f"{shot.name}.frame.html"
    wrapper.write_text(
        '<body style="margin:0"><iframe src="' + page_url(shot) + '" style="border:0;'
        f'width:{shot.width}px;height:{shot.height}px;display:block"></iframe></body>',
        encoding="utf-8",
    )
    return wrapper.as_uri()


def chrome_args(chrome: str, shot: Shot, url: str, out: Path) -> list[str]:
    args = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--allow-file-access-from-files",
        "--virtual-time-budget=5000",
        f"--window-size={max(shot.width, 800)},{shot.height}",
        f"--screenshot={out}",
    ]
    # OS の外観に引きずられないよう、ライトもダークも明示する（0 がダーク、1 がライト）
    if shot.dark:
        args += ["--force-dark-mode", "--blink-settings=preferredColorScheme=0"]
    else:
        args += ["--blink-settings=preferredColorScheme=1"]
    return [*args, url]


def main() -> int:
    chrome = find_chrome()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for shot in SHOTS:
        out = OUT_DIR / f"{shot.name}.png"
        args = chrome_args(chrome, shot, target_url(shot, OUT_DIR), out)
        subprocess.run(args, check=True, capture_output=True, timeout=60)
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
