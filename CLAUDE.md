@AGENTS.md

## Claude Code 向けの補足

- 利用者用のリポジトリで運用設定を依頼され、`config/enabled.json` がなければ、最初に [docs/setup.md](docs/setup.md) を読んで初回セットアップを行う（利用者は `/setup` でも始められる）。テンプレートの更新の取り込みは `/sync-fork`。

- Jev（`config/jev.json`、`scripts/classify_jev.py`）を変えるときは TypeSafe のスキル（`/typesafe:typesafe-ai`）を使い、公式ドキュメント（https://docs.typesafe.ai/llms.txt）を正とする。問いは英語で書き、`make try` に `TYPESAFE_API_KEY` を渡して実データで判定を確かめてから採用する。
- テンプレートの見た目を変えたら、`make shots` で幅3種とダークモードを撮って確かめる（`make preview` はブラウザーで開く）。ロゴを足したら `make icons`。ルーチンが使うアーティファクトはルーチンが公開し直すので、ここから公開しない。
- `.claude/settings.json` で `make check` などの確認用コマンドは許可済み。`gh variable set`・`gh workflow run` などは確認制。
- `.claude/hooks/guard.py`（PreToolUse）が、`-R` のない `gh`、`data/`・`state/` の編集、クラウドのセッションでの履歴ブランチ以外への push を止める。止められたら理由を読んで直す（回避しない）。
