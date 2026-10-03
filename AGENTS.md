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
  report_tools.py          ルーチン用（標準ライブラリだけ。scripts.lib を import しない）
scripts/lib/             入口から使う共通部品（store / urls / web / parsers / x_oauth / actions / models）
config/                  手で編集する設定（topics / sources / jev / report）
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
