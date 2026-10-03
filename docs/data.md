# データの形

取得層が書き、ルーチンが読むファイルの形。型の定義は [scripts/lib/models.py](../scripts/lib/models.py) が正で、ここはその説明。
形を変えるときは models.py、このファイル、書く側と読む側のスクリプト、ROUTINE.md を同時に直す。
レポートに入る report-data の形は [ROUTINE.md](../ROUTINE.md) の手順5と `scripts/report_tools.py` の `validate_data` にある。

## 共通の約束

| 項目 | 約束 |
| --- | --- |
| 情報源の名前 | `bookmarks`、`github`、`qiita`、`zenn`、`devio`、`blogs`。data/sources の配列名、report-data の `source_status`、除外の `source` で同じ綴りを使う |
| `article_key` | 正規化した URL（計測用クエリとフラグメントを外す）の SHA-256 の先頭12桁。`data/articles/<article_key>.json` のファイル名で、report-data ではブックマーク以外の項目の `id` |
| 日付 | ファイル名と `date` は日本時間の `YYYY-MM-DD`。日時は ISO 8601（`+09:00`、X の `created_at` は `Z`） |
| 取得結果 | `ok`（新着あり）、`none`（取得できたが新着なし）、`error`（取得失敗） |
| Jev の結果 | `jev` が `null` なら未判定。`status: unavailable` なら Jev が応答せず、ほかの値は `null`（ルーチンが代わりに判定する） |
| テック判定 | `tech_label` は `tech`（0.7以上）、`hold`（0.4以上0.7未満、ルーチンが最終判断）、`excluded`（0.4未満） |

## data/YYYY-MM-DD.json（ブックマーク）

`BookmarksFile`。新着が0件でも `{"date": ..., "posts": []}` を置く。X API の取得に失敗した日は `error` が付く。

```json
{
  "date": "2026-10-03",
  "fetched_at": "2026-10-03T06:02:11+09:00",
  "posts": [
    {
      "id": "1840000000000000001",
      "text": "投稿本文（長文投稿は note_tweet の本文。t.co は展開済み）",
      "author": {"username": "kato_agents", "name": "加藤"},
      "created_at": "2026-10-02T21:14:00Z",
      "url": "https://x.com/kato_agents/status/1840000000000000001",
      "fetched_at": "2026-10-03T06:02:11+09:00",
      "quoted": {"id": "...", "text": "引用元の本文", "author": {"username": "someone", "name": "..."}, "url": "https://x.com/someone/status/..."},
      "links": [
        {"url": "https://example.com/post", "domain": "example.com", "card_title": "...", "card_description": "...", "article_key": "3f9a1c0e5b2d", "from": "quoted"}
      ],
      "jev": {"status": "ok", "tech_prob": 0.93, "tech_label": "tech", "topic": "llm_agents", "topic_prob": 0.81}
    }
  ]
}
```

- `quoted` は引用がなければ `null`。引用元が削除されていると `text` が空になる。
- `links` は x.com・twitter.com・画像・動画を除いた外部リンク。`from` は本人の投稿（`self`）か引用元（`quoted`）か。

## data/articles/<article_key>.json（本文）

`ArticleRecord`。記事、README、ブログ記事で共通。一度作ったら取り直さない（`error` でも）。

| キー | 内容 |
| --- | --- |
| `fetch_status` | `ok`（500文字以上）、`partial`（500文字未満。有料記事など）、`blocked`（robots.txt で禁止）、`error`（取得エラー） |
| `chars` | 抽出した本文の元の文字数。読了目安（500文字 = 1分、1件15分まで）に使う |
| `text` | 本文。最大 20,000 文字 |
| `title`、`site_name`、`published` | trafilatura が取り出したもの。README は `title` がリポジトリ名、`site_name` が GitHub |

## data/sources/YYYY-MM-DD.json（追加の情報源）

`SourcesFile`。

| キー | 内容 |
| --- | --- |
| `github` | `RepoItem` の配列（上位10件）。`stars_today` は Search API で代用した日は `null` |
| `qiita`、`zenn`、`devio` | `RankingItem` の配列（各10件）。Qiita・Zenn の `jev` はテック判定つき、DevelopersIO はトピックだけ |
| `blogs` | `BlogItem` の配列（前回以降の新着）。`summary` は RSS の概要（最大500文字。一覧ページのブログは空） |
| `status` | 情報源ごとの取得結果（`github`、`qiita`、`zenn`、`devio`、`blogs`） |
| `blog_status` | ブログごとの結果（`new` / `none` / `error` と件数） |
| `notes` | 補足（例: `github` を Search API で代用した旨） |
| `errors` | 失敗した情報源と理由 |
| `reserve` | Qiita・Zenn の次点。`classify_jev.py` が補充に使って消す（ルーチンからは見えない） |

Qiita の項目と公式ブログの項目は、フィードの概要を `summary` に持つ（Zenn・DevelopersIO・一覧ページのブログは空）。本文が取れなかった記事（サイトがボットからの取得を拒否する場合など）は、Jev の判定とルーチンの要約でこれを本文の代わりに使う。

同日再取得では、保存済みの `blogs`（Jev判定を含む）に新着をURLで重複排除して追加する。
`blog_status.count` は当日の保存済み件数である。取得失敗でも項目と件数を保持し、状態は `error` にする。
日別ファイルの保存後に既読URLを更新する。

`streak_days` は前日のファイルにも同じ URL があれば前日の値 + 1。2以上なら、ルーチンは前日の要約を再利用する。
`title_source: "page"` は一覧ページから拾った見出し（崩れやすい）で、記事のタイトルが取れたら `fetch_articles.py` が置き換えて `"article"` にする。

## data/excluded/YYYY-MM-DD.json（除外）

`ExcludedFile`。`items` は `{"source": "bookmarks" | "qiita" | "zenn", "id", "title", "url", "tech_prob"}`。
ブックマークの `id` は投稿 ID、記事は `article_key`。
同日再分類では既存履歴を読み、`source` と `id` の組で重複排除して更新する。
当日の入力で判定が `status: ok` かつ `tech` / `hold` に変わった項目は履歴から外す。
入力にない項目や `unavailable` の項目の履歴は保持する。

## state/

| ファイル | 形 | 内容 |
| --- | --- | --- |
| `seen_ids.json` | `{"ids": [...]}` | 取得済みの投稿 ID（新しい順、上限 20,000）。ブックマークの取得はこれに当たったら止まる |
| `seen_urls.json` | `{"urls": {"<company>/<blog>": [...]}}` | ブログごとの既読 URL（新しい順、上限 500） |

## config/

| ファイル | 内容 |
| --- | --- |
| `topics.json` | トピック一覧。ID と並び順は固定（レポートの色が並び順で決まる）。`description` は Jev の選択肢の説明（英語） |
| `sources.json` | 情報源の URL、CSS セレクタ、ブログの一覧、新着の規則、User-Agent |
| `jev.json` | Jev のモデル、しきい値、問いの文面（英語）、state に入れる文字数 |
| `report.json` | Claude の公開担当が更新する固定アーティファクトの URL（各自のリポジトリで設定する）。Codex は使わない |

## レポートの届け先と履歴

report-data の形は両実行者で共通。届け先を report-data に含めない。
Claude は固定アーティファクト、Codex は実行した Cloud タスクの結果チャットへ届ける。
履歴の `reports/YYYY-MM-DD.html` と `summaries/<article_key>.json` は、公開担当が
Claude なら `claude/reports`、Codex なら `codex/reports` に保存する。
要約側には実行者に対応する履歴を読み取り専用で渡す。
