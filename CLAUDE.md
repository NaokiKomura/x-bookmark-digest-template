@AGENTS.md

## Claude Code 向けの補足

- `config/enabled.json` がなければ、最初に AGENTS.md の「初回セットアップ」に従い、AskUserQuestion（`multiSelect: true`）で集める情報源を尋ねる。

- Jev（`config/jev.json`、`scripts/classify_jev.py`）を変えるときは TypeSafe のスキル（`/typesafe:typesafe-ai`）を使い、公式ドキュメント（https://docs.typesafe.ai/llms.txt）を正とする。問いは英語で書き、`make try` に `TYPESAFE_API_KEY` を渡して実データで判定を確かめてから採用する。
- テンプレートの見た目を変えたら、手元の確認は `make preview`。ルーチンが使うアーティファクトはルーチンが公開し直すので、ここから公開しない。
- `.claude/settings.json` で `make check` などの確認用コマンドは許可済み。
