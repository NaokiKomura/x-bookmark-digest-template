# アーキテクチャ

## 層と責任

| 層 | 実行場所 | 外部との通信 | 秘密情報 | 書くもの |
| --- | --- | --- | --- | --- |
| 取得層 | GitHub Actions（`.github/workflows/fetch.yml`、3:17 JST） | X API、各サイト、GitHub API、TypeSafe AI（キーがあるときだけ） | X・TypeSafe・GH_PAT（Secrets） | main の `data/`、`state/` |
| 要約層 | Claude Code のルーチン、または Codex Cloud（7:00 JST を想定） | 外部サイトなし。Claude は Git（`claude/reports` への push）と Artifact だけ | なし | Claude: 固定アーティファクトと `claude/reports`。Codex: 結果チャット |
| 履歴の保存（Codex） | 要約とは別の信頼した環境（PUBLISH.md） | Git のみ | `codex/reports` への書き込み | `codex/reports` |
| 表示層 | Claude のアーティファクト、または Codex の結果チャット・HTMLファイル | cdnjs の d3 の取得 | なし | アーティファクトの db（所有者だけが読み書き。既読・あとで読む）。db がない場面は閲覧者のブラウザの localStorage |

この分け方の理由: 外部の文章（記事本文など）には Claude への指示が紛れ込みうる。要約層からコネクタと秘密情報を外すことで、操作可能な範囲を狭める。
記事の取得を取得層で済ませておくので、ルーチンはネットワーク設定を初期状態のまま使える。

Claude のルーチンは、毎朝ひとりで公開まで終えるために、Artifact ツールと `claude/reports` への push の権限を持つ。
claude.ai のルーチンには、要約と公開を別の環境に分ける仕組みがないためである。この権限が悪用されたときの影響を、次の決まりで狭める。

- 公開先は main の `config/report.json` の `artifact_url`、push 先は `claude/reports` だけに ROUTINE.md で固定する。資料や生成したJSONにある別の宛先は使わない。
- `report_tools.py validate` が `OK` を返したHTMLだけを公開する。HTMLはテンプレートの report-data ブロックだけが違うことを検証するので、スクリプトや外部の読み込みは足せない。
- main には push しない。取得層のコードと設定は、ルーチンからは書き換わらない。
- コネクタと秘密情報を渡さない。
- クラウドのセッションでは、フック（`.claude/hooks/guard.py`）が履歴ブランチ（`claude/reports`・`codex/reports`）以外への push を止める。push 先を明示しない push、強制 push、削除も止める。
  GitHub のブランチ保護が使える（公開リポジトリか有料プランの）場合は、main への push をそちらでも止めると二重になる。

プロンプトによる決まりとフックは権限そのものを制限しないので、資料に紛れた指示で誤った内容が公開されるおそれは残る。
運用開始から数日はルーチンの実行ログを確かめ、気になるときは PUBLISH.md の手順で手で公開する運用に切り替える。

## 取得層の流れ

`fetch.yml` が次の順に実行する。各スクリプトは前のスクリプトの出力ファイルを読んで、同じファイルに書き足す。

| 手順 | スクリプト | 読む | 書く | 失敗したとき |
| --- | --- | --- | --- | --- |
| 1〜2 | `fetch_bookmarks.py` | Secrets、`state/seen_ids.json` | `data/YYYY-MM-DD.json`、`state/seen_ids.json` | トークンの更新・書き戻しの失敗はワークフローを止める。X API の失敗はファイルに `error` を書いて続け、最後にワークフローを失敗させる |
| 3 | `fetch_sources.py` | `config/sources.json`、前日の `data/sources/`、`state/seen_urls.json` | `data/sources/YYYY-MM-DD.json`、`state/seen_urls.json` | 情報源ごとに `errors` と `status: error` を記録して続ける |
| 4 | `fetch_articles.py` | 当日の2ファイル | `data/articles/<article_key>.json` | 記事ごとに `fetch_status` を記録して続ける |
| 5 | `classify_jev.py` | 当日の2ファイル、`data/articles/`、`config/jev.json`、`config/topics.json` | 当日の2ファイルの `jev`、`data/excluded/YYYY-MM-DD.json` | 項目ごとに `jev.status: unavailable`。設定の誤りなら以降を呼ばない |
| 6 | （ワークフロー） | | main へのコミット | |

同じ日に再実行しても壊れない: ブックマークは ID で重複を除いて足し、記事は `article_key` で取得済みなら取らず、ブログは当日の保存済み項目と新着をURLで重複排除して足し、除外履歴はsourceとidで保持し、Jev は判定済み（`status: ok`）を呼び直さない。

## 要約層の流れ

`ROUTINE.md` がプロンプト本体。`scripts/report_tools.py`（標準ライブラリだけ）を補助に使う。

1. `report_tools.py inputs` で当日の入力の有無と件数を確かめる
2. `data/` を資料として読み、要約して report-data の JSON を作る
3. `report_tools.py build` でテンプレートの report-data ブロックだけを差し替え、`validate` で検証する
4. Claude は `config/report.json` の `artifact_url` に公開し、`reports/YYYY-MM-DD.html` と新しい要約を `claude/reports` に push する

実行者は信頼したタスク設定で選ぶ。Codex は [CODEX.md](../CODEX.md) を入口とし、
検証済みレポートを実行したタスクの結果チャットに渡す。ファイル受け渡し機能がなければ本文に要約を載せる。
Claude の `artifact_url` は使わない。Codex の履歴は別の担当者が [PUBLISH.md](../PUBLISH.md) に従って `codex/reports` に保存する。

ランキングで前日にも載った項目は、Claude は `claude/reports`、Codex は `codex/reports` の `summaries/<article_key>.json` を再利用して利用枠を節約する。

## 表示層

`template/report.html` は1ファイルで完結する。画像は企業と情報源（GitHub・Qiita・Zenn・DevelopersIO）のロゴだけで、元画像を `template/icons/` に置き、テンプレートには data URI で埋め込んでいる（外部から読み込まない）。話題マップには cdnjs の d3 を使い、取得できない場合は話題名と件数のボタンを表示する。フォントは外部から読み込まず、システムフォントを使う。
`<script type="application/json" id="report-data">` の中身だけが日々変わり、ほかの部分は変えない。
話題マップ（d3 の treemap）、絞り込み、既読・あとで読む、図解はすべてこの JSON から描く。
既読・あとで読むは、claude.ai のアーティファクトを所有者が開いたときだけ、アーティファクトの db（`read/<掲載日>`、`later/<項目ID>`）に保存して端末間で同期する。
db は claude.ai が提供する保存先で、外部への接続ではない。ルーチンが公開するときに、読み書きを所有者だけに絞る rules を宣言する。
db が使えない場面（手元のファイル、Codex の HTML、所有者以外の閲覧）は localStorage にだけ保存する。

## 設計上の判断

| 判断 | 理由 |
| --- | --- |
| Jev のテック判定とトピック分類を1回の呼び出しで並列に尋ねる | 呼び出しが半分になる。除外になった項目のトピックは捨てる |
| 除外したブックマークもファイルに残す（`tech_label: excluded`） | 推移グラフの件数を新着の総数にし、誤判定を確かめられるようにする |
| Qiita・Zenn は次点を `reserve` に取っておく | 除外で10件を割ったときに、ランキングを取り直さずに補充する |
| RSS のない一覧ページは初回は既読にするだけ | 過去の記事が一度に新着扱いになるのを防ぐ |
| `report_tools.py` は scripts/lib を import しない | ルーチンのクラウド環境で依存を入れずに `python3` だけで動かす |
| 設定（セレクタ、問い、しきい値、トピック）を config/ の JSON に出す | サイトの変更や判定の調整をコードを触らずに直せるようにする |
