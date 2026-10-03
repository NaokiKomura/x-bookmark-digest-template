"""GitHub Actions への注釈と出力。ローカルではふつうの出力になる。"""

from __future__ import annotations

import os
import sys


def in_actions() -> bool:
    return bool(os.environ.get("GITHUB_ACTIONS"))


def warning(message: str) -> None:
    print(f"::warning::{message}" if in_actions() else f"warning: {message}")


def error(message: str) -> None:
    print(f"::error::{message}" if in_actions() else f"error: {message}", file=sys.stderr)


def set_output(name: str, value: str) -> None:
    """ステップの出力（後続のステップが steps.<id>.outputs.<name> で読む）。"""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
