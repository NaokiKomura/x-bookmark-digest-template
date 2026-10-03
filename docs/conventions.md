# 命名規則とコードの書き方

新しいコードは周りのコードに合わせる。ここに書いたことと既存のコードが違っていたら、ここを正として直す。

## ファイルとモジュール

| 種類 | 名前の付け方 | 例 |
| --- | --- | --- |
| 入口のスクリプト（`scripts/`） | 動詞_対象。処理フローの1手順につき1ファイル | `fetch_sources.py`、`classify_jev.py` |
| 共通部品（`scripts/lib/`） | 中身を表す名詞 | `parsers.py`、`urls.py`、`web.py` |
| テスト | `tests/test_<モジュール名>.py`。対象のモジュールと1対1 | `test_parsers.py` |
| フィクスチャ | `tests/fixtures/<分類>/<情報源>_<ページ>.<拡張子>` | `sources/github_trending.html` |
| 設定 | `config/<用途>.json` | `jev.json` |

入口のスクリプトの docstring は「手順N: 何をするか」で始め、`入力:` と `出力:` の行を書く。
`scripts/lib/` のモジュールは入口のスクリプトを import しない（依存は入口 → lib の一方向）。
`scripts/report_tools.py` は例外で、scripts/lib も含めて標準ライブラリ以外を import しない。

## 名前

| 対象 | 規則 | 例 |
| --- | --- | --- |
| 関数 | 動詞から始める | `fetch_article`、`select_new_blog_items` |
| 変換する関数 | `to_<結果>` | `to_post`、`to_ranking_item`、`to_tech_jev` |
| パスを返す関数 | `<名前>_path` | `bookmarks_path`、`seen_urls_path` |
| Jev に渡す入力を作る関数 | `<対象>_state` | `bookmark_state`、`repo_state` |
| 読み取り（`parsers.py`） | 読み取る相手の名前だけ | `parsers.github_trending`、`parsers.feed` |
| 定数 | 大文字のスネークケース | `MAX_ARTICLE_CHARS` |
| 型 | パスカルケース。JSON ファイル全体は `<名前>File`、1件は `<名前>Item` | `SourcesFile`、`RankingItem` |
| 例外 | `<何の>Error` | `FetchError`、`ParseError`、`TokenError` |
| 日付の変数 | `date` オブジェクトは `day`、文字列は `date`（JSON のキーと同じ） | `day = today_jst()` |
| JSON のキー、設定のキー | 小文字のスネークケース | `article_key`、`streak_days` |

同じものには同じ名前を使う。とくに次の語はほかの言い方をしない。

| 語 | 意味 |
| --- | --- |
| `article_key` | URL のハッシュ（`key`、`hash`、`url_id` とは書かない。`ArticleRecord` の中だけは `key`） |
| `bookmarks` / `github` / `qiita` / `zenn` / `devio` / `blogs` | 情報源の名前（単数形の `bookmark` は使わない） |
| `item` | 情報源の1件 |
| `post` | X の投稿（ブックマークした投稿） |
| `record` | `data/articles/` の1件 |
| `jev` | Jev の判定結果 |
| `topic` | `config/topics.json` のトピック（`theme` は report-data のキーとしてだけ使う） |

## 言語

| 対象 | 言語 |
| --- | --- |
| 識別子（変数、関数、JSON のキー） | 英語 |
| コメント、docstring、ドキュメント | 日本語（である調） |
| data/ に残してレポートに出る文（`errors[].message` の説明部分、`notes`、`error`） | 日本語 |
| コンソールへのログ（`actions.warning` など） | 英語でも日本語でもよい。秘密情報を含めない |
| Jev の問い（`config/jev.json`、`topics.json` の `description`） | 英語 |

## エラーの扱い

- 外部への取得は `scripts/lib/web.py` を通し、失敗は `FetchError` にそろえる。読み取りの失敗は `ParseError`。
- 情報源1つ・記事1件・Jev 1回の失敗は、その単位に閉じ込めて記録し、処理を続ける（`except Exception` は単位の境界でだけ使い、`# noqa: BLE001` に理由を書く）。
- 止めるべき失敗は `TokenError`（X のトークン）だけ。ほかは記録してレポートに「取得失敗」と出す。

## 型

- `scripts/` は mypy で型を付ける（`make typecheck`）。data/ に書く dict は `scripts/lib/models.py` の TypedDict で注釈する。
- `# type: ignore` を使わない。型が合わないときは、型を直すか、`cast` に理由のコメントを付ける。

## テスト

- 外部 API・サイトは呼ばない。HTTP は `httpx.MockTransport`、Jev は `Judge` と同じ形の偽の関数。
- data/ と state/ を読み書きするテストは `digest_root` フィクスチャを使う（一時ディレクトリに向く）。
- サイトのページは `tests/fixtures/` にスナップショットを置く。必要な構造だけを残して小さくし、先頭のコメントに取得元と日付を書く。
- 新しい機能やバグ修正には、同じ変更でテストを足す。
