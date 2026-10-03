# x-bookmark-digest

毎朝、Xのブックマークと、その日のテック系トレンド（GitHub、Qiita、Zenn、DevelopersIO）、主要5社の公式テックブログの更新を集め、
図解つきの要約レポートとして claude.ai のアーティファクトに届ける個人用システムのテンプレート。設計は [docs/spec.md](docs/spec.md)。

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
git fetch upstream
git merge upstream/main --allow-unrelated-histories   # 2回目からは --allow-unrelated-histories は不要
```

`data/`、`state/`、`config/report.json` は自分のリポジトリの値を残す。

## 構成

```text
.github/workflows/fetch.yml   取得と判定のワークフロー（6:00）
scripts/fetch_bookmarks.py    トークン更新・ブックマーク取得・差分抽出
scripts/fetch_sources.py      GitHubトレンド・ランキング・公式ブログの取得
scripts/fetch_articles.py     リンク先記事・README・ブログ本文の取得と抽出
scripts/classify_jev.py       Jevによるテック判定とトピック分類
scripts/common.py             上の4つで共通の部品
scripts/auth_local.py         初回のリフレッシュトークンを取ってSecretsに登録する
scripts/report_tools.py       ルーチン用の補助（入力の確認、推移、キーワード、組み立て、検証。標準ライブラリだけ）
config/topics.json            トピック一覧（固定）
config/sources.json           追加の情報源の取得URLと読み取り方
config/report.json            ルーチンが上書きするアーティファクトのURL
state/seen_ids.json           取得済みの投稿ID
state/seen_urls.json          取得済みのブログ記事URL（ブログごと）
data/YYYY-MM-DD.json          日別の新着ブックマーク（{"date", "posts": [...]}）
data/sources/YYYY-MM-DD.json  日別の追加の情報源
data/excluded/YYYY-MM-DD.json テック判定で除外した項目
data/articles/<key>.json      記事・README・ブログの本文（URLのハッシュ単位）
template/report.html          レポートのテンプレート（サンプルデータ入り）
ROUTINE.md                    ルーチンの手順書（プロンプト本体）
docs/spec.md                  元の仕様書
```

`claude/reports` ブランチに `reports/YYYY-MM-DD.html` と `summaries/<key>.json` が溜まる。

## 仕様書との違い

| 項目 | 実装 | 理由 |
| --- | --- | --- |
| Qiita | 人気記事の Atom フィード（`/popular-items/feed`） | トレンドページはログイン画面に転送される |
| Zenn | トレンド順の非公式 JSON（`/api/articles?order=daily`） | トレンドの RSS がない |
| DevelopersIO | `/trending/`（週間トレンド） | 人気ランキングのページがこれ |
| X Engineering Blog | 一覧ページの差分 | RSS がない。2023年以降の更新はほぼない |
| 公式ブログの新着 | RSS は公開日が3日以内の記事だけ。RSS のない一覧ページは初回は既読にするだけ | 初回に過去の記事が一度に新着扱いになるのを防ぐ |
| Jev の問い | 英語で書く | Jev の精度が最も高い言語 |
| 判定の呼び出し | テック判定とトピック分類を1回の呼び出しで並列に尋ねる | 呼び出し回数が半分になる。除外になった項目のトピックは使わない |
| ブックマークの除外 | `data/YYYY-MM-DD.json` には `tech_label: excluded` として残し、`data/excluded/` にも記録する | 推移グラフの件数を新着の総数にするため |

## 開発

```bash
uv sync
make check        # ruff + pytest。コミット前に必ず通す
DIGEST_ROOT=/tmp/digest-try uv run python -m scripts.fetch_sources   # 実データの場所を汚さずに試す
```

テストは外部 API を呼ばない（`httpx.MockTransport` と偽の判定関数）。
`DIGEST_ROOT` を一時ディレクトリに向けると、data/ と state/ の読み書きがそちらに行く（config/ はリポジトリのものを読む）。
`DIGEST_DATE=YYYY-MM-DD` でデータの日付を指定できる（手動の再実行用。ワークフローの入力 `date` と同じ）。

## 1日あたりの外部への呼び出し

| 相手 | 回数の目安 |
| --- | --- |
| X API | トークン更新1回 + ブックマーク取得1〜5回（50件ずつ。取得済みの投稿に当たったら止める） |
| 各サイト | 一覧ページ・フィード各1回（10か所）、robots.txt はホストごとに1回、本文は未取得の記事だけ（約50〜70件） |
| GitHub API | README 10回（Trending が読めない日は Search API 1回） |
| TypeSafe AI（Jev） | 1項目1回（約70回） |

## 注意

- GitHub Actions の定期実行は混雑で遅れることがある（数時間遅れた例もある）。7:00 のルーチンが当日のデータを見つけられないと「本日のデータなし」になる。遅れが続く場合は `fetch.yml` の cron を早める。
- 情報源のページ構造が変わると、その情報源はレポートに「取得失敗」と出る。`config/sources.json` と `scripts/fetch_sources.py` の読み取り部分を直す。
- 記事本文は要約の材料としてだけ使い、レポートには言い換えた要点だけを載せる。リポジトリを公開しない。

## ライセンス

[MIT](LICENSE)
