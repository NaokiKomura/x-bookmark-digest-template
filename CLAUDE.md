# CLAUDE.md

Xのブックマークとテック系トレンドを毎朝集め（GitHub Actions）、Jevで振り分け、Claudeのルーチンが要約してアーティファクトに届ける個人用システム。
全体像と初期設定は README.md、ルーチンの手順は ROUTINE.md、元の仕様書は docs/spec.md。
このリポジトリは公開テンプレート（x-bookmark-digest-template）。個人のデータや URL を入れない（それらは各自のプライベートリポジトリにだけ置く）。

## 守ること

- `make check` が通らない状態でコミットしない。テストで本物の外部 API を呼ばない。
- 試しに実際のサイトを読むときは `DIGEST_ROOT` を一時ディレクトリに向ける。リポジトリの data/ と state/ に試験の出力を書かない。
- 取得層（scripts/fetch_*.py、classify_jev.py）だけが外部と通信する。ROUTINE.md とテンプレートに外部への接続を足さない。
- `scripts/report_tools.py` は標準ライブラリだけで書く（ルーチンのクラウド環境で依存を入れずに動かすため）。
- テンプレートの JavaScript は、データ中の文字列を textContent で入れる（innerHTML を使わない）。リンクは `https://` で始まるものだけ有効にする。
- report-data の形を変えたら、`report_tools.py validate_data`、ROUTINE.md の表、テンプレートの3つを同時に直す。
- `config/topics.json` の ID と並び順を変えない（色が並び順で決まり、日をまたいでそろえるため）。変えるときはテンプレートの `TOPICS` も直す。
- ページを読み取る情報源が壊れたら、`config/sources.json` と `fetch_sources.py` の読み取り部分だけを直す。
- X のリフレッシュトークンは使うたびに変わる。書き戻しに失敗したら後続を止める（`fetch_bookmarks.py`）。トークンをログやファイルに出さない（ローカルの .secrets/ を除く）。
- Jev の問いやしきい値を変えるときは TypeSafe のスキル（`/typesafe:typesafe-ai`）と公式ドキュメントを使い、実データで確かめてから採用する。問いは英語で書く。
- 外部 API の呼び出し回数を増やす変更では、README の「1日あたりの外部への呼び出し」を更新する。
- 仕様書と実装を変えたときは README の「仕様書との違い」を更新する。
