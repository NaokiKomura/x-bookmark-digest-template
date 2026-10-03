# x-bookmark-digest

毎朝、Xのブックマークと、その日のテック系トレンド（GitHub、Qiita、Zenn、DevelopersIO）、主要4社の公式テックブログの更新を集め、
図解つきの要約レポートとして、Claude なら claude.ai のアーティファクト、Codex なら Codex Cloud の結果チャットに届ける個人用システムのテンプレート。仕様は [docs/spec.md](docs/spec.md)。

| 層 | どこで動くか | すること |
| --- | --- | --- |
| 取得層 | GitHub Actions（`fetch.yml`、毎日 5:00 JST） | トークン更新、ブックマーク・情報源・本文の取得、Jev での判定、main へのコミット |
| 要約層 | Claude Code のルーチン、または Codex Cloud（7:00 JST を想定） | main のデータを読み、report-data と要約キャッシュを生成 |
| 公開担当 | 要約とは別の信頼した環境（PUBLISH.md） | 検証して実行者ごとの履歴を保存。Claude は固定アーティファクトも更新 |
| 表示層 | Claude のアーティファクト、または Codex の結果チャット・受け渡した HTML | `template/report.html` に report-data を差し込んだもの |

X API と TypeSafe AI の認証情報は GitHub Secrets にだけ置き、ルーチンには渡さない。Claude は API キーを使わず、サブスクの利用枠で動く。

## 必要なもの

| 項目 | 内容 |
| --- | --- |
| X API | 開発者アプリ（OAuth 2.0、Type of App は Web App など Confidential client）。コールバック URL に `http://127.0.0.1:8765/callback` を登録する。ブックマーク API は従量課金の対象 |
| TypeSafe AI | API キー（判定モデル Jev。入力100万トークンあたり約0.042ドル） |
| 要約担当 | Claude Code のルーチンを使えるプラン、または Codex Cloud を利用できるアカウントと公開済み環境 |
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

最後の行は、Claude の要約履歴を置く空の `claude/reports` ブランチを作る。
Codex で運用する場合は、最後の行の保存先を `refs/heads/codex/reports` にする。
両方を使う場合は、それぞれの履歴ブランチを作る。既存のブランチにはこの初期化を行わない。

### 1.5. 集める情報源を選ぶ

X のブックマークに加えて、どのトレンドと公式ブログを集めるかを選ぶ。Claude Code などのコーディングエージェントでこのリポジトリを開くと、最初に設問が出る（[AGENTS.md](AGENTS.md#初回セットアップ情報源取得ワークフロールーチン)）。集める情報源のほか、取得ワークフロー（手順4）を有効にするか、要約のルーチン（手順5）を作るかも尋ねられ、選んだものはエージェントが設定する。「あとで」を選んだ手順は下の説明のとおり自分で行う。手で情報源を選ぶときは次のとおり。

```bash
uv run python -m scripts.configure_sources --list                       # 選択肢
uv run python -m scripts.configure_sources --enable github,zenn,anthropic,aws
git add config/enabled.json && git commit -m "chore: choose sources" && git push
```

選ばなかった情報源には接続しない。あとから選び直すときも同じコマンドを使う。`config/enabled.json` がなければ、すべての情報源を集める。

### 2. Secrets を登録する

| Secret名 | 内容 |
| --- | --- |
| `X_CLIENT_ID` | XアプリのクライアントID |
| `X_CLIENT_SECRET` | Xアプリのクライアントシークレット |
| `X_USER_ID` | 自分のXユーザーID（数字） |
| `TYPESAFE_API_KEY` | TypeSafe AI の API キー |
| `GH_PAT` | このリポジトリだけを対象にした fine-grained PAT。権限は **Secrets: Read and write** だけ。リフレッシュトークンの書き戻しに使う。有効期限の前に作り直す |

```bash
REPO=MY-NAME/x-bookmark-digest
gh secret set X_CLIENT_ID -R $REPO
gh secret set X_CLIENT_SECRET -R $REPO
gh secret set X_USER_ID -R $REPO
gh secret set TYPESAFE_API_KEY -R $REPO
gh secret set GH_PAT -R $REPO
```

`gh` のコマンドには必ず `-R`（操作するリポジトリ）を付ける。remote が2つ以上あると、`gh repo set-default` を設定していても `gh secret`・`gh variable` が別のリポジトリ（テンプレート）を操作することがある。

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
gh variable set DIGEST_ENABLED --body true -R $REPO
gh workflow run fetch -R $REPO
gh variable list -R $REPO   # 自分のリポジトリに DIGEST_ENABLED が入ったことを確かめる
```

`DIGEST_ENABLED` が `true` でないと、ワークフローは何もしない（テンプレート自身や設定途中のリポジトリで失敗し続けないため）。
実行が終わったら `git pull` して、`data/` にその日のファイルが入ったことを確かめる。

### 5. ルーチンを作る

Codex Cloud で動かす場合は [CODEX.md](CODEX.md) の環境準備とプロンプトを使う。
届け先は実行したタスクの結果チャット、履歴は `codex/reports`。以下は Claude 用の設定である。

claude.ai/code/routines で次のように作り、「今すぐ実行」で1回試す。

| 設定 | 値 |
| --- | --- |
| トリガー | スケジュール、毎日 7:00（日本時間） |
| リポジトリ | 手順1のリポジトリ |
| モデル | Sonnet |
| 環境 | Default（ネットワークは Trusted） |
| 環境変数・API credentials | なし |
| コネクタ | すべて外す |
| プロンプト | このリポジトリの ROUTINE.md の手順に従って、今日の report-data と要約キャッシュをローカルに作ってください。公開は別の担当者が行います。 |

要約側にはmainと過去の要約を読み取り専用で渡し、出力ディレクトリだけを書き込み可能にする。
Gitの書き込み資格情報・Artifactツールを与えない。実行環境側でこの制限を設定できない場合は、
書き込み権限を持つルーチンでの自動公開を有効にせず、担当者が手動で [PUBLISH.md](PUBLISH.md) に従って公開する。
プロンプトや「credentialsなし」という設定だけでは、組み込みのGit・Artifact権限まで制限した保証にはならない。

初回は「今すぐ実行」でJSONの生成を確認する。公開担当者は別の信頼した環境でスキーマを検証し、
信頼したテンプレートからHTMLを組み立てる。初回だけアーティファクトを作成し、そのURLを
`config/report.json` の `artifact_url` に設定する。通常運用では同じ公開先と `claude/reports` にだけ保存する。
公開担当には外部の記事本文や生成されたコマンドを渡さない。

運用開始から数日は、ルーチンの実行ログを開いて結果を確かめる（実行ステータスが緑でも、タスクが成功したとは限らない）。

## テンプレートの更新を取り込む

```bash
git remote add upstream https://github.com/NaokiKomura/x-bookmark-digest-template.git
git fetch upstream
git merge upstream/main --allow-unrelated-histories   # 2回目からは --allow-unrelated-histories は不要
```

upstream を足したあとは remote が2つになるので、`gh` のコマンドには必ず `-R MY-NAME/x-bookmark-digest` を付ける。

`data/`、`state/`、`config/report.json`、`config/enabled.json` は自分のリポジトリの値を残す。

## 構成

```text
scripts/            取得層の入口（fetch_bookmarks → fetch_sources → fetch_articles → classify_jev）と、ルーチン用の report_tools.py
scripts/lib/        共通部品（パスと JSON、URL、HTTP、ページの読み取り、X の OAuth、データの型）
config/             手で編集する設定（トピック、情報源、Jev の問い、アーティファクトの URL）
state/, data/       ワークフローが毎日書く
template/           レポートのテンプレート（サンプルデータ入り）
ROUTINE.md          実行者の振り分けと共通の要約手順
CODEX.md            Codex Cloud の準備と結果チャットへの受け渡し
PUBLISH.md          別環境での検証・公開の手順書
docs/               設計（architecture）、データの形（data）、命名規則（conventions）、変更の手順（recipes）、元の仕様書（spec）
```

公開担当が Claude は `claude/reports`、Codex は `codex/reports` に
`reports/YYYY-MM-DD.html` と `summaries/<article_key>.json` を保存する。
Cloud の作業領域や結果チャットへのファイル受け渡しだけでは、翌日の要約キャッシュは保存されない。

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
| Claude | 要約ルーチン1回/日 + 担当者による検証・公開。API キーは使わずサブスクの利用枠内（Pro はルーチンの実行が1日5回まで。利用枠は通常の会話と共通） | 追加なし（Pro 以上の契約が前提） |
| GitHub Actions | 取得ワークフローは実測1〜1.6分/回（ブックマーク3件で66秒、30件と本文約120件で98秒）。ジョブごとに分単位で切り上げて数えるので2分/日 ≒ 60分/月。CI は約20秒/回で1分/push。合わせて月100〜200分程度で、プライベートリポジトリの無料枠（Free プランで2,000分/月、Pro は3,000分/月）の1割未満。超えると Linux $0.006/分 | $0 |
| GitHub のストレージ | 記事本文が約1MB/日（実測: 64件で約1MB）増える。1年で約0.35GB（git の圧縮前） | $0（リポジトリの推奨上限 数GB の範囲） |

Codex を要約担当にする場合、取得層の外部API呼び出し回数は同じ。
要約の利用枠・料金は Codex 側の契約と実行方法に従うため、下の合計に Codex の料金は含めない。

合計は **月 約$1〜$8**（ほぼ X API の展開データが課金されるかどうかで決まる）と Claude のサブスク料金。
GitHub は無料枠に収まる想定で、追加の費用はかからない（Actions は月100〜200分程度で、Free プランの無料枠2,000分/月の1割未満。ほかに Actions を使うワークフローがない前提。使用量は GitHub の Settings → Billing で確かめられる）。
X API の初回の支払い登録で $20 分のクレジットが付く。実際の金額は、運用開始から数日後に X の Developer Console の利用状況で確かめる。

参考: [X API Pricing](https://docs.x.com/x-api/getting-started/pricing)、[TypeSafe Models](https://docs.typesafe.ai/models)、[Introducing routines in Claude Code](https://claude.com/blog/introducing-routines-in-claude-code)、[GitHub Actions の課金](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

接続先制限・総時間制限による追加のAPI呼び出しや再試行はない。要約と公開の分離は手動公開を前提とし、追加のモデル呼び出しは行わない。

## 1日あたりの外部への呼び出し

Codex Cloud に要約担当を替えても、下表の取得層の呼び出し回数は変わらない。
結果チャットへの受け渡しのために外部記事を取り直すことはない。

| 相手 | 回数の目安 |
| --- | --- |
| X API | トークン更新1回 + ブックマーク取得1回（20件ずつ。取得済みの投稿に当たったら止める。新着が20件を超える日だけ2回以上） |
| 各サイト | 一覧ページ・フィード各1回（すべて選んだ場合は10か所。選ばなかった情報源には接続しない）、robots.txt はホストごとに1回、本文は未取得の記事だけ（約50〜70件。ブックマークは1投稿あたり5本まで）。公開日のないブログ記事は新着の判定のために本文を先に取る（同じ本文を使い回すので回数は増えない。各ブログの初回だけ最大10件ずつ多くなる） |
| GitHub API | README 10回（Trending が読めない日は Search API 1回） |
| TypeSafe AI（Jev） | 1項目1回（約70回） |

各取得は公開IPに限定し、リダイレクト先も検査する。robots.txtと本文の取得全体は30秒で中断する。
期限を超えた記事は `error` として保存し、後続に進む（POSIXのメインスレッドで実行する）。

## 注意

- GitHub Actions の定期実行は混雑で遅れることがある（数時間遅れた例もある）。7:00 のルーチンが当日のデータを見つけられないと「本日のデータなし」になる。遅れが続く場合は `fetch.yml` の cron を早める。
- 情報源のページ構造が変わると、その情報源はレポートに「取得失敗」と出る。`config/sources.json` と `scripts/fetch_sources.py` の読み取り部分を直す。
- 記事本文は要約の材料としてだけ使い、レポートには言い換えた要点だけを載せる。リポジトリを公開しない。

## ライセンス

[MIT](LICENSE)
