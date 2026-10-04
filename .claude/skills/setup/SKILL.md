---
name: setup
description: 初回セットアップ。集める情報源（トレンド・公式ブログ）の選択、取得ワークフロー（GitHub Actions）の有効化、要約ルーチンの作成を、利用者に尋ねながら進める。選び直すときにも使う。
disable-model-invocation: true
---

[docs/setup.md](../../../docs/setup.md) を読み、その手順どおりに進める。

- 設問は AskUserQuestion の1回の呼び出しにまとめる（docs/setup.md の「Claude Code では」の段落）。
- 引数で「情報源だけ」「ワークフローだけ」などと言われたら、その部分だけを尋ねて進める。
- push、`gh variable set`、`gh workflow run`、ルーチンの作成は、利用者が選んだものだけを行う。`gh` には必ず `-R <自分のリポジトリ>` を付ける。
- 最後に、選ばれた内容と、「あとで」にした項目の残りの手順（README の該当箇所）を短くまとめて伝える。
