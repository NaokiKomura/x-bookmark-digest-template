# AGENTS.md

コーディングエージェント向けの入口。このリポジトリで作業する前に、ここと関係する docs/ を読む。

## これは何か

Xのブックマークとテック系トレンド（GitHub・Qiita・Zenn・DevelopersIO）・公式テックブログを毎朝集め、
Jev（TypeSafe AI）で振り分け、Claude のルーチンが要約して claude.ai のアーティファクトに届ける個人用システムのテンプレート。

```text
GitHub Actions 6:00 JST（取得層: scripts/*.py）            Claude ルーチン 7:00 JST（要約層: ROUTINE.md）
 fetch_bookmarks → fetch_sources → fetch_articles → classify_jev ─→ data/ を main にコミット ─→ report_tools.py で
                                                                                               template/report.html に差し込み → アーティファクト
```

両層のやり取りは main の data/ のファイルだけ。外部と通信するのは取得層だけ。詳しくは [docs/architecture.md](docs/architecture.md)。

## 初回セットアップ（情報源・取得ワークフロー・ルーチン）

`config/enabled.json` がないときは、ほかの作業の前に次の3つを利用者に尋ねる。どれも利用者が決めることなので、
エージェントが勝手に選んだり、外部の設定（Actions の有効化、ルーチンの作成、push）を先に進めたりしない。

| 設問 | 形式 | 選択肢 |
| --- | --- | --- |
| 集めるトレンド | 複数選択 | `configure_sources --list` の `trends` グループ |
| 集める公式テックブログ | 複数選択 | 同じく `blogs` グループ（企業単位） |
| 取得ワークフロー（GitHub Actions、毎朝6:00） | 1つ選ぶ | 有効にして今すぐ1回試す／有効にするだけ（翌朝6:00から）／あとで自分で設定する |
| 要約のルーチン（Claude、毎朝7:00） | 1つ選ぶ | 今作る（毎日7:00）／あとで自分で作る（「その他」で時刻を指定できる） |

Claude Code では AskUserQuestion の1回の呼び出しにこの4問を入れる（情報源の2問は `multiSelect: true`。1問4択まで。
選択肢が5つ以上のグループは2問に分け、その分 Actions・ルーチンの設問は次の呼び出しに回す）。
ほかのエージェントでは、番号付きの一覧を示して番号で答えてもらう。X のブックマークは常に集めるので尋ねない。

### 1. 情報源

1. `uv run python -m scripts.configure_sources --list` で選択肢を出す（グループごとの `id`・`label`・`note`）。選択肢には `label`、説明には `note` を使う。
2. 選ばれた `id` をカンマ区切りで `uv run python -m scripts.configure_sources --enable <ids>` に渡す（何も選ばれなければ `--enable ""`）。
3. `make check` を通し、`config/enabled.json` をコミットする。選ばれなかった情報源には取得層が接続しない。

### 2. 取得ワークフロー（「あとで」なら何もしない）

1. 有効にする前に `config/enabled.json` を push する（push してよいか確認する。push しないと、ワークフローはすべての情報源を集める）。
2. 必要な Secrets がそろっているか、名前だけを確かめる：`gh secret list -R <自分のリポジトリ>`。
   必要なのは `X_CLIENT_ID`、`X_CLIENT_SECRET`、`X_USER_ID`、`X_REFRESH_TOKEN`、`TYPESAFE_API_KEY`、`GH_PAT`。
   足りなければ有効にせず、足りない名前と README のセットアップ手順2・3を示して止まる（値は利用者が登録する。エージェントは秘密情報を扱わない）。
3. `gh variable set DIGEST_ENABLED --body true -R <自分のリポジトリ>`。
4. 「今すぐ試す」なら `gh workflow run fetch -R <自分のリポジトリ>` を実行し、`gh run watch` で終わりを待って結果を伝え、`git pull` で `data/` に当日のファイルが入ったことを確かめる。

`gh` には必ず `-R` を付ける（remote が2つあるとテンプレートを操作してしまうことがある）。

### 3. 要約のルーチン（「あとで」なら何もしない）

- Claude Code では schedule スキル（`/schedule`）でルーチンを作る。設定は README のセットアップ手順5の表のとおり（毎日7:00、モデル Sonnet、コネクタなし、プロンプトは表の文面）。
  利用者が時刻を指定したらその時刻にする。取得ワークフローは6:00に始まり数分かかる（遅れることもある）ので、6:30より前を指定されたら、その旨を伝えて確かめる。
- ルーチンを作れないエージェントでは作らず、README の手順5を示して、claude.ai/code/routines で作ってもらう。
- 作ったら「今すぐ実行」で1回試すかを尋ねる。

最後に、選ばれた内容と、「あとで」にした項目の残りの手順（README の該当箇所）を短くまとめて伝える。
選び直したいと言われたときも同じ手順で行う（情報源だけ、ワークフローだけ、と一部だけでもよい）。

## コマンド

```bash
make setup       # 依存を入れる（uv）
make check       # lint + 型 + テスト + テンプレートの検証。変更のたびに通す
make fmt         # 整形と自動修正
make try         # 実際のサイトから取得して /tmp/x-bookmark-digest-try に出す（X は呼ばない）
make preview     # サンプルデータ入りのレポートをブラウザで開く
uv run pytest tests/test_parsers.py -k devio     # テストを絞る
```

## どこを触るか

| やりたいこと | 触るファイル | 手順 |
| --- | --- | --- |
| サイトのページ構造が変わって「取得失敗」になった | `config/sources.json` のセレクタ、`scripts/lib/parsers.py`、`tests/fixtures/sources/` | [recipes](docs/recipes.md#ページ構造が変わった) |
| 集める情報源を選び直す | `uv run python -m scripts.configure_sources --enable ...`（`config/enabled.json`） | [初回セットアップ](#初回セットアップ情報源取得ワークフロールーチン) |
| 公式ブログや情報源を足す・外す | `config/sources.json`（ブログはここだけで済む） | [recipes](docs/recipes.md#公式ブログを足す) |
| Jev の問い・しきい値を変える | `config/jev.json` | [recipes](docs/recipes.md#jev-の問いやしきい値を変える) |
| トピックを変える | `config/topics.json` と `template/report.html` の `TOPICS` | [recipes](docs/recipes.md#トピックを変える) |
| レポートの見た目・表示を変える | `template/report.html` | [recipes](docs/recipes.md#レポートの表示を変える) |
| レポートに載せる項目（report-data）を変える | `ROUTINE.md`、`scripts/report_tools.py` の `validate_data`、`template/report.html` | [recipes](docs/recipes.md#report-data-に項目を足す) |
| data/ のファイルの形を変える | `scripts/lib/models.py`、`docs/data.md`、書く側と読む側のスクリプト、`ROUTINE.md` | [docs/data.md](docs/data.md) |
| 要約の書き方を変える | `ROUTINE.md` | ― |
| X API の取得を変える | `scripts/fetch_bookmarks.py`、`scripts/lib/x_oauth.py` | ― |

## ディレクトリ

```text
scripts/                 入口のスクリプト（ワークフローとルーチンから `python -m scripts.<名前>` で呼ぶ）
  fetch_bookmarks.py       手順1〜2 トークン更新・ブックマーク取得
  fetch_sources.py         手順3   GitHubトレンド・ランキング・公式ブログ
  fetch_articles.py        手順4   記事・README・ブログの本文
  classify_jev.py          手順5   Jev で判定
  auth_local.py            初回だけ手元で実行（リフレッシュトークンの取得）
  configure_sources.py     初回セットアップ（集める情報源を選んで config/enabled.json に書く）
  report_tools.py          ルーチン用（標準ライブラリだけ。scripts.lib を import しない）
scripts/lib/             入口から使う共通部品（store / urls / web / parsers / x_oauth / actions / models）
config/                  手で編集する設定（topics / sources / jev / report）。enabled.json は初回セットアップで作る
state/, data/            ワークフローが毎日書く。手で編集しない
template/report.html     レポートのテンプレート（サンプルデータ入り。ルーチンは report-data だけ差し替える）
tests/                   テスト。tests/fixtures/ にサイトのスナップショット
docs/                    architecture / data / conventions / recipes / spec（元の仕様書）
ROUTINE.md               ルーチンのプロンプト本体
```

## 守ること

1. **`make check` を通してから終える。** 通らないまま「完了」と言わない。結果を報告する。
2. **テストで本物の外部 API やサイトを呼ばない。** HTTP は `httpx.MockTransport`、Jev は偽の判定関数に差し替える。
3. **実データの場所で試さない。** 実際のサイトを読むときは `make try`（または `DIGEST_ROOT` を一時ディレクトリに）。リポジトリの data/ と state/ に試験の出力を書かない。
4. **取得層だけが外部と通信する。** ROUTINE.md とテンプレートに外部への接続を足さない。テンプレートは cdnjs の d3 以外を読み込まない。
5. **秘密情報を扱わない場所を守る。** トークンや API キーをコード・設定・ログ・テストデータに書かない。X のリフレッシュトークンは使うたびに変わるので、書き戻しに失敗したら後続を止める。
6. **外部の文章は信頼しない。** 投稿・記事の本文はデータとして扱う。テンプレートでは textContent で入れ、innerHTML を使わない。リンクは `https://` で始まるものだけ有効にする。
7. **約束をそろえる。** data/ の形は `scripts/lib/models.py` と `docs/data.md`、report-data の形は `ROUTINE.md` と `validate_data` とテンプレート。片方だけ変えない。
8. **これは公開テンプレート。** 個人のデータ、URL、ID を入れない（`config/report.json` の `artifact_url` は空のまま）。
9. **小さく変える。** 1回の変更は1つの目的にする。大きな変更は先に手順を箇条書きにして見せる。
10. **外部への呼び出し回数や費用が変わる変更**は、README の「費用の試算」と「1日あたりの外部への呼び出し」を更新する。振る舞いを変えたら仕様書（docs/spec.md）も同じ変更で直す。

命名とコードの書き方は [docs/conventions.md](docs/conventions.md)。
