# アーキテクチャ

## 層と責任

| 層 | 実行場所 | 外部との通信 | 秘密情報 | 書くもの |
| --- | --- | --- | --- | --- |
| 取得層 | GitHub Actions（`.github/workflows/fetch.yml`、6:00 JST） | X API、各サイト、GitHub API、TypeSafe AI | X・TypeSafe・GH_PAT（Secrets） | main の `data/`、`state/` |
| 要約層 | Claude Code のルーチン（7:00 JST、Sonnet） | なし（リポジトリと Artifact だけ） | なし | アーティファクト、`claude/reports` ブランチ |
| 表示層 | claude.ai のアーティファクト | cdnjs の d3 だけ | なし | 閲覧者のブラウザの localStorage（既読） |

この分け方の理由: 外部の文章（記事本文など）には Claude への指示が紛れ込みうる。要約層にコネクタも秘密情報も持たせなければ、
仮に指示に従ってしまっても被害が出ない。記事の取得を取得層で済ませておくので、ルーチンはネットワーク設定を初期状態のまま使える。

## 取得層の流れ

`fetch.yml` が次の順に実行する。各スクリプトは前のスクリプトの出力ファイルを読んで、同じファイルに書き足す。

| 手順 | スクリプト | 読む | 書く | 失敗したとき |
| --- | --- | --- | --- | --- |
| 1〜2 | `fetch_bookmarks.py` | Secrets、`state/seen_ids.json` | `data/YYYY-MM-DD.json`、`state/seen_ids.json` | トークンの更新・書き戻しの失敗はワークフローを止める。X API の失敗はファイルに `error` を書いて続け、最後にワークフローを失敗させる |
| 3 | `fetch_sources.py` | `config/sources.json`、前日の `data/sources/`、`state/seen_urls.json` | `data/sources/YYYY-MM-DD.json`、`state/seen_urls.json` | 情報源ごとに `errors` と `status: error` を記録して続ける |
| 4 | `fetch_articles.py` | 当日の2ファイル | `data/articles/<article_key>.json` | 記事ごとに `fetch_status` を記録して続ける |
| 5 | `classify_jev.py` | 当日の2ファイル、`data/articles/`、`config/jev.json`、`config/topics.json` | 当日の2ファイルの `jev`、`data/excluded/YYYY-MM-DD.json` | 項目ごとに `jev.status: unavailable`。設定の誤りなら以降を呼ばない |
| 6 | （ワークフロー） | | main へのコミット | |

同じ日に再実行しても壊れない: ブックマークは ID で重複を除いて足し、記事は `article_key` で取得済みなら取らず、Jev は判定済み（`status: ok`）を呼び直さない。

## 要約層の流れ

`ROUTINE.md` がプロンプト本体。`scripts/report_tools.py`（標準ライブラリだけ）を補助に使う。

1. `report_tools.py inputs` で当日の入力の有無と件数を確かめる
2. `data/` を資料として読み、要約して report-data の JSON を作る
3. `report_tools.py build` でテンプレートの report-data ブロックだけを差し替え、`validate` で検証する
4. アーティファクトを公開し直し、`claude/reports` ブランチに HTML と要約のキャッシュを push する

ランキングで前日にも載った項目は、`claude/reports` の `summaries/<article_key>.json` を再利用して利用枠を節約する。

## 表示層

`template/report.html` は1ファイルで完結する（アーティファクトの制約: 外部のスクリプトは cdnjs だけ、画像は読めない）。
`<script type="application/json" id="report-data">` の中身だけが日々変わり、ほかの部分は変えない。
話題マップ（d3 の force layout）、絞り込み、既読（localStorage）、図解はすべてこの JSON から描く。

## 設計上の判断

| 判断 | 理由 |
| --- | --- |
| Jev のテック判定とトピック分類を1回の呼び出しで並列に尋ねる | 呼び出しが半分になる。除外になった項目のトピックは捨てる |
| 除外したブックマークもファイルに残す（`tech_label: excluded`） | 推移グラフの件数を新着の総数にし、誤判定を確かめられるようにする |
| Qiita・Zenn は次点を `reserve` に取っておく | 除外で10件を割ったときに、ランキングを取り直さずに補充する |
| RSS のない一覧ページは初回は既読にするだけ | 過去の記事が一度に新着扱いになるのを防ぐ |
| `report_tools.py` は scripts/lib を import しない | ルーチンのクラウド環境で依存を入れずに `python3` だけで動かす |
| 設定（セレクタ、問い、しきい値、トピック）を config/ の JSON に出す | サイトの変更や判定の調整をコードを触らずに直せるようにする |
