"""試し取得の専用ディレクトリを用意する。既存の任意ディレクトリは削除しない。

入力: 試験出力のパス（リポジトリ外）
出力: 専用の空ディレクトリと所有を示すマーカー
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = ".x-bookmark-digest-try"


def prepare(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("試験出力にシンボリックリンクは使えない")
    target = path.resolve()
    if target == Path(target.anchor) or target == Path.home() or target.is_relative_to(ROOT):
        raise ValueError("試験出力は専用のリポジトリ外ディレクトリにする")
    if ROOT.is_relative_to(target):
        raise ValueError("リポジトリを含むディレクトリは削除できない")
    marker = target / MARKER
    if target.exists():
        if not target.is_dir() or marker.is_symlink() or not marker.is_file():
            raise ValueError("既存ディレクトリに専用マーカーがない。別の空のパスを指定する")
        if marker.read_text(encoding="utf-8") != "x-bookmark-digest-try\n":
            raise ValueError("専用マーカーが不正")
        shutil.rmtree(target)
    (target / "state").mkdir(parents=True)
    (target / MARKER).write_text("x-bookmark-digest-try\n", encoding="utf-8")


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("試験出力のパスを1つ指定する")
        prepare(Path(sys.argv[1]))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
