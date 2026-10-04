# x-bookmark-digest

毎朝、Xのブックマークと、その日のテック系トレンド（GitHub、Qiita、Zenn、DevelopersIO）、主要4社の公式テックブログの更新を集めて、図解つきの要約レポートにする個人用システムのテンプレート。
レポートは、Claudeならclaude.aiのアーティファクトに、CodexならCodex Cloudの結果チャットに届く。仕様は[docs/spec.md](docs/spec.md)。

| 層 | どこで動くか | すること |
| --- | --- | --- |
| 取得層 | GitHub Actions（`fetch.yml`、毎日3:17 JST） | トークン更新、ブックマーク・情報源・本文の取得、Jevでの判定（任意）、mainへのコミット |
| 要約層 | Claude Codeのルーチン、またはCodex Cloud（7:00 JSTを想定） | mainのデータを読んでレポートを作る。Claudeは固定アーティファクトを更新し、履歴を`claude/reports`に保存する |
| 履歴の保存（Codex） | 要約とは別の信頼した環境（PUBLISH.md） | Codexのレポートと要約キャッシュを`codex/reports`に保存する |
| 表示層 | Claudeのアーティファクト、またはCodexの結果チャット・受け渡したHTML | `template/report.html`にreport-dataを差し込んだもの |

X APIとTypeSafe AIの認証情報はGitHub Secretsにだけ置き、ルーチンには渡さない。ClaudeはAPIキーを使わず、サブスクの利用枠で動く。

## 必要なもの

| 項目 | 内容 |
| --- | --- |
| X API | 開発者アプリ（OAuth 2.0、Type of AppはWeb AppなどのConfidential client）。コールバックURLに`http://127.0.0.1:8765/callback`を登録する。ブックマークAPIは従量課金の対象 |
| TypeSafe AI（任意） | APIキー（判定モデルJev。入力100万トークンあたり約0.042ドル）。なくても動く（下の「Jevを使わない場合」） |
| 要約担当 | Claude Codeのルーチンを使えるプラン、またはCodex Cloudを利用できるアカウントと公開済みの環境 |
| GitHub | プライベートリポジトリ1つ |
| 手元 | [uv](https://docs.astral.sh/uv/)と[gh](https://cli.github.com/)（初回の認証に使う） |

## セットアップ

手順1〜3は手で行う。手順4〜6（情報源の選択、取得ワークフロー、ルーチン）は、Claude Codeなどのコーディングエージェントでこのリポジトリを開くと、最初に設問として出る（[AGENTS.md](AGENTS.md#初回セットアップ情報源取得ワークフロールーチン)）。
選んだものはエージェントが設定し、「あとで」を選んだ手順は下の説明のとおり自分で行う。エージェントは取得ワークフローを有効にする前にSecretsがそろっているかを確かめるので、先に手順2・3を済ませておく。

### 1. 自分用のプライベートリポジトリを作る

記事の本文とブックマークの内容が毎日コミットされるので、**必ずプライベートにする**。

```bash
gh repo create MY-NAME/x-bookmark-digest --private --template NaokiKomura/x-bookmark-digest-template --clone
cd x-bookmark-digest
git push origin "$(git commit-tree "$(git hash-object -t tree /dev/null)" -m 'init reports')":refs/heads/claude/reports
```

最後の行は、Claudeの要約履歴を置く空の`claude/reports`ブランチを作る。
Codexで運用する場合は、最後の行の保存先を`refs/heads/codex/reports`にする。
両方を使う場合は、それぞれの履歴ブランチを作る。既存のブランチにはこの初期化を行わない。

### 2. Secretsを登録する

| Secret名 | 内容 |
| --- | --- |
| `X_CLIENT_ID` | XアプリのクライアントID |
| `X_CLIENT_SECRET` | Xアプリのクライアントシークレット |
| `X_USER_ID` | 自分のXユーザーID（数字） |
| `TYPESAFE_API_KEY` | TypeSafe AIのAPIキー。Jevを使わないなら登録しない |
| `GH_PAT` | このリポジトリだけを対象にしたfine-grained PAT。権限は「**Secrets: Read and write**」だけ。リフレッシュトークンの書き戻しに使う。有効期限の前に作り直す |

表にない`X_REFRESH_TOKEN`は、手順3で登録する。

```bash
REPO=MY-NAME/x-bookmark-digest
gh secret set X_CLIENT_ID -R $REPO
gh secret set X_CLIENT_SECRET -R $REPO
gh secret set X_USER_ID -R $REPO
gh secret set TYPESAFE_API_KEY -R $REPO   # Jevを使う場合だけ
gh secret set GH_PAT -R $REPO
```

`gh`のコマンドには必ず`-R`（操作するリポジトリ）を付ける。remoteが2つ以上あると、`gh repo set-default`を設定していても、`gh secret`や`gh variable`が別のリポジトリ（テンプレート）を操作することがある。

#### Jevを使わない場合

`TYPESAFE_API_KEY`を登録しなければ、取得ワークフローはJevを呼ばず、すべての項目を判定待ち（`jev.status: unavailable`）として保存する。テック系かどうかの判定とトピックの分類は、要約のルーチンが内容を読んで行う。追加のAPIキーや料金はかからない。

ただし、約70件の判定をルーチンが行うぶん、ルーチンの利用枠の消費と実行時間が増える。判定に確率が付かないので、確信の低い項目を保留に回す仕組みも働かない。あとからキーを登録すれば、翌朝からJevで判定する。

### 3. 初回のリフレッシュトークンを取る

```bash
uv sync
uv run python -m scripts.auth_local --repo MY-NAME/x-bookmark-digest
```

ブラウザでXの認可画面が開く。許可すると、リフレッシュトークンは画面に出ないまま`X_REFRESH_TOKEN`に登録される。
以後は、ワークフローが実行のたびに新しいトークンを書き戻す。

### 4. 集める情報源を選ぶ

Xのブックマークに加えて、どのトレンドと公式ブログを集めるかを選ぶ。エージェントを使わずに選ぶときは、次のコマンドを使う。

```bash
uv run python -m scripts.configure_sources --list                       # 選択肢
uv run python -m scripts.configure_sources --enable github,zenn,anthropic,aws
git add config/enabled.json && git commit -m "chore: choose sources" && git push
```

選ばなかった情報源には接続しない。あとから選び直すときも同じコマンドを使う。`config/enabled.json`がなければ、すべての情報源を集める。

### 5. 取得ワークフローを有効にして試す

```bash
gh variable set DIGEST_ENABLED --body true -R $REPO
gh workflow run fetch -R $REPO
gh variable list -R $REPO   # 自分のリポジトリに DIGEST_ENABLED が入ったことを確かめる
```

`DIGEST_ENABLED`が`true`でないと、ワークフローは何もしない。テンプレート自身や設定途中のリポジトリで、ワークフローが失敗し続けないようにするためである。
実行が終わったら`git pull`して、`data/`にその日のファイルが入ったことを確かめる。

定期実行は毎日3:17（日本時間）に始まる。**この既定の時刻のまま使うことを推奨する。**
GitHub Actionsの定期実行は毎時0分に混み合い、数時間遅れることがある。5:00に設定していたときは2時間43分遅れ、7:00の要約に間に合わずに「本日のデータなし」になった。
3:17なら、取得は1分ほどで終わるので、遅れても7:00のルーチンまでに3時間以上の余裕がある。
時刻を変えるときは`fetch.yml`の`cron`をUTCで書き（3:17 JSTは`17 18 * * *`）、毎時0分を避け、ルーチンの時刻より3時間以上前にする。

### 6. 要約のルーチンを作る

Claudeで動かす場合は、claude.ai/code/routinesで次のように作り、「今すぐ実行」で1回試す。

| 設定 | 値 |
| --- | --- |
| トリガー | スケジュール、毎日7:00（日本時間） |
| リポジトリ | 手順1のリポジトリ |
| モデル | Sonnet |
| 環境 | Default（ネットワークはTrusted） |
| 環境変数・API credentials | なし |
| コネクタ | すべて外す |
| プロンプト | このリポジトリの ROUTINE.md の手順に従って、今日のレポートを作って公開してください。 |

ルーチンは、`report_tools.py validate` が通ったレポートだけを`config/report.json`の`artifact_url`に公開し、履歴を`claude/reports`にpushする。mainにはpushしない。
公開先とpush先はROUTINE.mdで固定しており、記事本文などの資料に書かれた別の宛先やコマンドは使わない。

初回は必ず「今すぐ実行」で行う。アーティファクトを新しく作るときはClaudeが確認を求めるので、実行中のセッションを開いて許可する（定期実行では確認に答えられない）。ルーチンの報告にそのURLが出る。
そのURLを`config/report.json`の`artifact_url`に書いてmainにコミットすると、2回目からは同じアーティファクトが上書きされる。
既存のアーティファクトの上書きは確認なしで行われる（共有を「リンクを知っている全員」にしていない場合。公開共有にすると毎回確認が必要になる）。

運用開始から数日は、ルーチンの実行ログを開いて結果を確かめる。実行ステータスが緑でも、タスクが成功したとは限らない。

Codex Cloudで動かす場合は、[CODEX.md](CODEX.md)の環境準備とプロンプトを使う。届け先は実行したタスクの結果チャット、履歴は`codex/reports`になる。

## 毎朝届くものと履歴

Claudeはルーチンが`claude/reports`に、Codexは別の担当者が[PUBLISH.md](PUBLISH.md)に従って`codex/reports`に、`reports/YYYY-MM-DD.html`と`summaries/<article_key>.json`を保存する。
Claudeのアーティファクトは毎朝同じURLに上書きされるので、過去の日のレポートはこのブランチの`reports/`で見る。
Cloudの作業領域や結果チャットへのファイル受け渡しだけでは、翌日に使う要約キャッシュは保存されない。

### 注意

- GitHub Actionsの定期実行は、混雑で遅れることがある（5:00に設定していたときに2時間43分遅れた例がある）。とくに毎時0分は混み合うので、取得ワークフローは3:17に始める。7:00のルーチンが当日のデータを見つけられないと、レポートは「本日のデータなし」になる。そのときはデータが入ったあとでルーチンを「今すぐ実行」すれば、同じアーティファクトが作り直される。遅れが続く場合は`fetch.yml`のcronをさらに早める。
- 情報源のページ構造が変わると、レポートではその情報源が「取得失敗」と表示される。`config/sources.json`と`scripts/fetch_sources.py`の読み取り部分を直す。
- 記事本文は要約の材料にだけ使い、レポートには言い換えた要点だけを載せる。リポジトリは公開しない。

## 費用の試算

1か月（30日）あたりの目安。前提は、新着ブックマークが1日20件以下、追加の情報源が1日約60件（GitHub 10、Qiita・Zenn・DevelopersIO各10、公式ブログ約5、補充の数件）。
単価は2026-10-03時点の公開情報、トークン数は同日の実測。

| 項目 | 計算 | 月額の目安 |
| --- | --- | --- |
| X API（ブックマーク） | 自分のデータの読み取り（Owned Read）は$0.001/件。返した件数ぶん課金されるので、1日1ページ20件で$0.02/日 | **約$0.6** |
| X API（展開したデータ） | 引用元の投稿（Post read $0.005/件）と投稿者（User read $0.010/件）が別に数えられる場合の上限。1日に投稿者20人・引用元3件として約$0.22/日。同じUTC日の重複は1回だけ課金 | 0〜約$7 |
| TypeSafe AI（Jev） | 入力$0.042/100万トークン（出力は無料）。テック判定つきで約4,200トークン/回、トピックのみで約1,900トークン/回、これが約70回/日で約20万トークン/日、約600万トークン/月。使わなければ$0 | **約$0.25** |
| Claude | 要約と公開のルーチン1回/日。APIキーは使わず、サブスクの利用枠内（Proはルーチンの実行が1日5回まで。利用枠は通常の会話と共通） | 追加なし（Pro以上の契約が前提） |
| GitHub Actions | 取得ワークフローは実測1〜1.6分/回（ブックマーク3件で66秒、30件と本文約120件で98秒）。ジョブごとに分単位で切り上げて数えるので、2分/日で約60分/月。CIは約20秒/回で1分/push。合わせて月100〜200分程度で、プライベートリポジトリの無料枠（Freeプランで2,000分/月、Proは3,000分/月）の1割未満。超えるとLinuxで$0.006/分 | $0 |
| GitHubのストレージ | 記事本文で約1MB/日（実測: 64件で約1MB）増える。1年で約0.35GB（gitの圧縮前） | $0（リポジトリの推奨上限である数GBの範囲） |

Codexを要約担当にする場合、取得層の外部API呼び出し回数は同じ。
要約の利用枠・料金はCodex側の契約と実行方法に従うため、下の合計にCodexの料金は含めない。

合計は、**月約$1〜$8**とClaudeのサブスク料金。金額は、ほぼX APIの展開データが課金されるかどうかで決まる。
GitHubは無料枠に収まる想定で、追加の費用はかからない。Actionsは月100〜200分程度で、Freeプランの無料枠（2,000分/月）の1割未満である（ほかにActionsを使うワークフローがない前提）。使用量はGitHubのSettings → Billingで確かめられる。
X APIの初回の支払い登録で、$20分のクレジットが付く。実際の金額は、運用開始から数日後にXのDeveloper Consoleの利用状況で確かめる。

参考: [X API Pricing](https://docs.x.com/x-api/getting-started/pricing)、[TypeSafe Models](https://docs.typesafe.ai/models)、[Introducing routines in Claude Code](https://claude.com/blog/introducing-routines-in-claude-code)、[GitHub Actionsの課金](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

接続先制限・総時間制限による追加のAPI呼び出しや再試行はない。

## 1日あたりの外部への呼び出し

Codex Cloudに要約担当を替えても、下表の取得層の呼び出し回数は変わらない。
結果チャットへの受け渡しのために、外部の記事を取り直すことはない。

| 相手 | 回数の目安 |
| --- | --- |
| X API | トークン更新1回とブックマーク取得1回（20件ずつ。取得済みの投稿に当たったら止める。新着が20件を超える日だけ2回以上） |
| 各サイト | 一覧ページ・フィード各1回（すべて選んだ場合は10か所。選ばなかった情報源には接続しない）。robots.txtはホストごとに1回。本文は未取得の記事だけ（約50〜70件。ブックマークは1投稿あたり5本まで）。公開日のないブログ記事は、新着かどうかを判定するために本文を先に取る（同じ本文を使い回すので回数は増えない。各ブログの初回だけ最大10件ずつ多くなる） |
| GitHub API | README 10回（Trendingが読めない日はSearch API 1回） |
| TypeSafe AI（Jev） | 1項目1回（約70回）。`TYPESAFE_API_KEY`がなければ呼ばない |

各取得は公開IPに限定し、リダイレクト先も検査する。robots.txtと本文の取得全体は30秒で中断する。
期限を超えた記事は`error`として保存し、後続に進む（POSIXのメインスレッドで実行する）。

## テンプレートの更新を取り込む

```bash
git remote add upstream https://github.com/NaokiKomura/x-bookmark-digest-template.git
git fetch upstream
git merge upstream/main --allow-unrelated-histories   # 2回目からは --allow-unrelated-histories は不要
```

upstreamを足すとremoteが2つになるので、`gh`のコマンドには必ず`-R MY-NAME/x-bookmark-digest`を付ける。

`data/`、`state/`、`config/report.json`、`config/enabled.json`は、自分のリポジトリの値を残す。

## 開発

コーディングエージェント（Claude Codeなど）で改造する前提で整えている。入口は[AGENTS.md](AGENTS.md)。

```bash
make setup     # 依存を入れる
make check     # lint + 型 + テスト + テンプレートの検証。コミット前に必ず通す
make try       # 実際のサイトから取得して /tmp に出す（X は呼ばない。リポジトリの data/ は汚さない）
make preview   # サンプルデータ入りのレポートをブラウザで開く
```

よくある変更（ページ構造が変わった、ブログを足す、Jevの問いを変える、表示を変える）の手順は[docs/recipes.md](docs/recipes.md)。

## 構成

```text
scripts/            取得層の入口（fetch_bookmarks → fetch_sources → fetch_articles → classify_jev）と、ルーチン用の report_tools.py
scripts/lib/        共通部品（パスと JSON、URL、HTTP、ページの読み取り、X の OAuth、データの型）
config/             手で編集する設定（トピック、情報源、Jev の問い、アーティファクトの URL）
state/, data/       ワークフローが毎日書く
template/           レポートのテンプレート（サンプルデータ入り）
ROUTINE.md          実行者の振り分けと共通の要約手順
CODEX.md            Codex Cloud の準備と結果チャットへの受け渡し
PUBLISH.md          Codex の履歴の保存と、手で公開するときの手順書
docs/               設計（architecture）、データの形（data）、命名規則（conventions）、変更の手順（recipes）、元の仕様書（spec）
```

## ライセンス

[MIT](LICENSE)
