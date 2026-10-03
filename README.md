# x-bookmark-digest

毎朝、Xのブックマークと、その日のテック系トレンド（GitHub、Qiita、Zenn、DevelopersIO）、主要5社の公式テックブログの更新を集め、
図解つきの要約レポートとして claude.ai のアーティファクトに届ける個人用システムのテンプレート。仕様は [docs/spec.md](docs/spec.md)。

| 層 | どこで動くか | すること |
| --- | --- | --- |
| 取得層 | GitHub Actions（`fetch.yml`、毎日 6:00 JST） | トークン更新、ブックマーク・情報源・本文の取得、Jev での判定、main へのコミット |
| 要約層 | Claude Code のルーチン（毎日 7:00 JST、Sonnet） | main のデータを読んで要約し、アーティファクトを更新、`claude/reports` に履歴を保存 |
| 表示層 | claude.ai のアーティファクト | `template/report.html` に report-data を差し込んだもの |

X API と TypeSafe AI の認証情報は GitHub Secrets にだけ置き、ルーチンには渡さない。Claude は API キーを使わず、サブスクの利用枠で動く。

## 必要なもの

| 項目 | 内容 |
| --- | --- |
| X API | 開発者アプリ（OAuth 2.0、Type of App は Web App など Confidential client）。コールバック URL に `http://127.0.0.1:8765/callback` を登録する。ブックマーク API は従量課金の対象 |
| TypeSafe AI | API キー（判定モデル Jev。入力100万トークンあたり約0.042ドル） |
| Claude | Pro 以上のプラン（Claude Code のルーチンを使う） |
| GitHub | プライベートリポジトリ1つ |
| 手元 | [uv](https://docs.astral.sh/uv/) と [gh](https://cli.github.com/)（初回の認証に使う） |

## セットアップ

### 1. 自分用のプライベートリポジトリを作る

**記事の本文とブックマークの内容が毎日コミットされるので、必ずプライベートにする。**

```bash
gh repo create MY-NAME/x-bookmark-digest --private --template NaokiKomura/x-bookmark-digest-template --clone
cd x-bookmark-digest
git push origin "$(git commit-tree "$(git hash-object -t tree /dev/null)" -m 'init reports')":refs/heads/claude/reports
```

最後の行は、要約の履歴を置く空の `claude/reports` ブランチを作る。

### 2. Secrets を登録する

| Secret名 | 内容 |
| --- | --- |
| `X_CLIENT_ID` | XアプリのクライアントID |
| `X_CLIENT_SECRET` | Xアプリのクライアントシークレット |
| `X_USER_ID` | 自分のXユーザーID（数字） |
| `TYPESAFE_API_KEY` | TypeSafe AI の API キー |
| `GH_PAT` | このリポジトリだけを対象にした fine-grained PAT。権限は **Secrets: Read and write** だけ。リフレッシュトークンの書き戻しに使う。有効期限の前に作り直す |

```bash
gh secret set X_CLIENT_ID
gh secret set X_CLIENT_SECRET
gh secret set X_USER_ID
gh secret set TYPESAFE_API_KEY
gh secret set GH_PAT
```

`X_REFRESH_TOKEN` は次の手順で登録する。

### 3. 初回のリフレッシュトークンを取る

```bash
uv sync
uv run python -m scripts.auth_local --repo MY-NAME/x-bookmark-digest
```

ブラウザで X の認可画面が開く。許可すると、リフレッシュトークンが画面に出ることなく `X_REFRESH_TOKEN` に登録される。
以後はワークフローが実行のたびに新しいトークンを書き戻す。

### 4. 取得ワークフローを有効にして試す

```bash
gh variable set DIGEST_ENABLED --body true
gh workflow run fetch
```

`upstream`（テンプレート）を remote に足していると、`gh` がどちらのリポジトリを操作するか決められずに止まる。
その場合は先に `gh repo set-default MY-NAME/x-bookmark-digest` を実行する（または各コマンドに `-R MY-NAME/x-bookmark-digest` を付ける）。

`DIGEST_ENABLED` が `true` でないと、ワークフローは何もしない（テンプレート自身や設定途中のリポジトリで失敗し続けないため）。
実行が終わったら `git pull` して、`data/` にその日のファイルが入ったことを確かめる。

### 5. ルーチンを作る

claude.ai/code/routines で次のように作り、「今すぐ実行」で1回試す。

| 設定 | 値 |
| --- | --- |
| トリガー | スケジュール、毎日 7:00（日本時間） |
| リポジトリ | 手順1のリポジトリ |
| モデル | Sonnet |
| 環境 | Default（ネットワークは Trusted） |
| 環境変数・API credentials | なし |
| コネクタ | すべて外す |
| プロンプト | このリポジトリの ROUTINE.md の手順に従って、今日のレポートを作って公開してください。 |

初回はアーティファクトが新しく作られ、ルーチンの報告にその URL が出る。
その URL を `config/report.json` の `artifact_url` に書いて main にコミットすると、2回目からは同じアーティファクトが上書きされる。

運用開始から数日は、ルーチンの実行ログを開いて結果を確かめる（実行ステータスが緑でも、タスクが成功したとは限らない）。

## テンプレートの更新を取り込む

```bash
git remote add upstream https://github.com/NaokiKomura/x-bookmark-digest-template.git
gh repo set-default MY-NAME/x-bookmark-digest   # remote が2つになるので、gh の操作先を自分のリポジトリに固定する
git fetch upstream
git merge upstream/main --allow-unrelated-histories   # 2回目からは --allow-unrelated-histories は不要
```

`data/`、`state/`、`config/report.json` は自分のリポジトリの値を残す。

## 構成

```text
scripts/            取得層の入口（fetch_bookmarks → fetch_sources → fetch_articles → classify_jev）と、ルーチン用の report_tools.py
scripts/lib/        共通部品（パスと JSON、URL、HTTP、ページの読み取り、X の OAuth、データの型）
config/             手で編集する設定（トピック、情報源、Jev の問い、アーティファクトの URL）
state/, data/       ワークフローが毎日書く
template/           レポートのテンプレート（サンプルデータ入り）
ROUTINE.md          ルーチンの手順書（プロンプト本体）
docs/               設計（architecture）、データの形（data）、命名規則（conventions）、変更の手順（recipes）、元の仕様書（spec）
```

`claude/reports` ブランチに `reports/YYYY-MM-DD.html` と `summaries/<article_key>.json` が溜まる。

## 開発

コーディングエージェント（Claude Code など）で改造する前提で整えている。入口は [AGENTS.md](AGENTS.md)。

```bash
make setup     # 依存を入れる
make check     # lint + 型 + テスト + テンプレートの検証。コミット前に必ず通す
make try       # 実際のサイトから取得して /tmp に出す（X は呼ばない。リポジトリの data/ は汚さない）
make preview   # サンプルデータ入りのレポートをブラウザで開く
```

よくある変更（ページ構造が変わった、ブログを足す、Jev の問いを変える、表示を変える）の手順は [docs/recipes.md](docs/recipes.md)。

## 費用の試算

1か月（30日）あたりの目安。前提は、新着ブックマークが1日20件以下、追加の情報源が1日約60件（GitHub 10、Qiita・Zenn・DevelopersIO 各10、公式ブログ約5、補充の数件）。
単価は 2026-10-03 時点の公開情報、トークン数は同日の実測。

| 項目 | 計算 | 月額の目安 |
| --- | --- | --- |
| X API（ブックマーク） | 自分のデータの読み取り（Owned Read）$0.001/件。返した件数ぶん課金されるので、1日1ページ20件 = $0.02/日 | **約$0.6** |
| X API（展開したデータ） | 引用元の投稿（Post read $0.005/件）と投稿者（User read $0.010/件）が別に数えられる場合の上限。1日に投稿者20人・引用元3件として約$0.22/日。同じ UTC 日の重複は1回だけ課金 | 0〜約$7 |
| TypeSafe AI（Jev） | 入力 $0.042/100万トークン（出力は無料）。テック判定つき約4,200トークン/回、トピックのみ約1,900トークン/回 × 約70回/日 ≒ 20万トークン/日 ≒ 600万トークン/月 | **約$0.25** |
| Claude | ルーチン1回/日。API キーは使わずサブスクの利用枠内（Pro はルーチンの実行が1日5回まで。利用枠は通常の会話と共通） | 追加なし（Pro 以上の契約が前提） |
| GitHub Actions | 取得ワークフロー約2〜3分/日 ≒ 90分/月 + CI。プライベートリポジトリの無料枠は Free プランで2,000分/月（超えると Linux $0.006/分） | $0 |
| GitHub のストレージ | 記事本文が約1MB/日（実測: 64件で約1MB）増える。1年で約0.35GB（git の圧縮前） | $0（リポジトリの推奨上限 数GB の範囲） |

合計は **月 約$1〜$8**（ほぼ X API の展開データが課金されるかどうかで決まる）と Claude のサブスク料金。
X API の初回の支払い登録で $20 分のクレジットが付く。実際の金額は、運用開始から数日後に X の Developer Console の利用状況で確かめる。

参考: [X API Pricing](https://docs.x.com/x-api/getting-started/pricing)、[TypeSafe Models](https://docs.typesafe.ai/models)、[Introducing routines in Claude Code](https://claude.com/blog/introducing-routines-in-claude-code)、[GitHub Actions の課金](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

## 1日あたりの外部への呼び出し

| 相手 | 回数の目安 |
| --- | --- |
| X API | トークン更新1回 + ブックマーク取得1回（20件ずつ。取得済みの投稿に当たったら止める。新着が20件を超える日だけ2回以上） |
| 各サイト | 一覧ページ・フィード各1回（10か所）、robots.txt はホストごとに1回、本文は未取得の記事だけ（約50〜70件） |
| GitHub API | README 10回（Trending が読めない日は Search API 1回） |
| TypeSafe AI（Jev） | 1項目1回（約70回） |

## 注意

- GitHub Actions の定期実行は混雑で遅れることがある（数時間遅れた例もある）。7:00 のルーチンが当日のデータを見つけられないと「本日のデータなし」になる。遅れが続く場合は `fetch.yml` の cron を早める。
- 情報源のページ構造が変わると、その情報源はレポートに「取得失敗」と出る。`config/sources.json` と `scripts/fetch_sources.py` の読み取り部分を直す。
- 記事本文は要約の材料としてだけ使い、レポートには言い換えた要点だけを載せる。リポジトリを公開しない。

## ライセンス

[MIT](LICENSE)
