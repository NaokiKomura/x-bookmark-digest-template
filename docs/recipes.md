# よくある変更の手順

どの手順も最後に `make check` を通す。

## ページ構造が変わった

症状: レポートの「情報源の状況」に「取得失敗」が出る。`data/sources/YYYY-MM-DD.json` の `errors` に `ParseError` がある。

1. 今のページを取ってくる: `curl -sL -A "x-bookmark-digest/0.1" <URL> -o /tmp/page.html`（curl で 403 になるサイトは `uv run python -c "from scripts.lib.web import make_client; print(make_client().get('<URL>').text)" > /tmp/page.html`）
2. `tests/fixtures/sources/` の該当ファイルを、新しいページの必要な部分だけを残したものに差し替える（先頭のコメントの日付も直す）
3. `uv run pytest tests/test_parsers.py` が落ちることを確かめる
4. まず `config/sources.json` のセレクタやパターンだけで直せないか試す。だめなら `scripts/lib/parsers.py` を直す
5. `make try` で実際のサイトから取れることを確かめる

## 集める情報源を選び直す

```bash
uv run python -m scripts.configure_sources --list
uv run python -m scripts.configure_sources --enable github,zenn,anthropic   # 選んだ ID だけになる
make check
```

`config/enabled.json` をコミットする。公式ブログは企業単位で選ぶ（同じ `company` のブログはまとめて入切される）。

## 公式ブログを足す

`config/sources.json` の `blogs` に1件足すだけでよい（コードの変更は不要）。

- RSS・Atom があれば `"kind": "feed"` と `url`。
- なければ `"kind": "page"`、一覧ページの `url`、記事の href に一致する `link_pattern`（正規表現）、相対 URL を解決する `base_url`。
  `link_pattern` はカテゴリや一覧のページに一致しないようにする（記事の URL に年や slug が入る形を狙う）。
- `company` が同じブログは、レポートで1つの見出しにまとまる。
- 新しい `company` のアイコンは、既定では青い四角に頭文字が入る。ロゴを使うときは、80px の画像を `template/icons/<company>.png`（または `.jpg`）に置き、同じ画像を data URI にした `.av.org[data-co="<company>"]` の行を `template/report.html` に足す（`make check` で食い違いを検出する）。
- `make try` で、公開日が3日以内の記事だけが新着になることと、`state/seen_urls.json` に記事の URL が入ることを確かめる（一覧やフィードに日付がない記事は本文から日付を読む）。
- robots.txt で禁止されていないことを確かめる。README の「費用の試算」と「1日あたりの外部への呼び出し」を更新する。

## Jev の問いやしきい値を変える

1. TypeSafe の公式ドキュメント（https://docs.typesafe.ai/llms.txt、primitives の Noul と Choice、confidence）を読む
2. `config/jev.json` を変える（問いは英語。問いの ID はモデルに送られないので、文面だけで意味が通じるようにする）
3. `TYPESAFE_API_KEY=... make try` で実データを判定し、`/tmp/x-bookmark-digest-try/data/` の `jev` と `data/excluded/` を見比べる
4. しきい値を変えたら `tests/test_classify_jev.py` の境界値のテストも直す

## トピックを変える

トピックの ID と並び順は、日をまたいでレポートの話題マップと絞り込みをそろえるために固定している。変えるのは本当に必要なときだけ。

1. `config/topics.json` を変える（`description` は英語。Jev の選択肢の説明になる）
2. `template/report.html` の `TOPICS`（ID と表示名、並び順）を同じに直す（トピックごとの色はない。強調色は青に統一している）
3. `other` は最後に置いたままにする

## レポートの表示を変える

1. `template/report.html` を直す。データ中の文字列は `el(tag, attrs, text)`（textContent）で入れ、innerHTML を使わない。リンクは `safeUrl` / `link` を通す
2. 色はトークン（`--ink`、`--blue` など）だけを使い、強調色は `--blue` にそろえる。企業のロゴ（`.av.org[data-co]`）だけは例外。ライトとダークの両方の値を `:root` と2つのダークのブロックに書く
3. `make preview` で見た目を確かめる（サンプルデータで、スマホ幅とダークモードも）
4. `make check`（テンプレートのサンプルデータの検証を含む）

## report-data に項目を足す

report-data はルーチンが書き、テンプレートが読む。3か所を同時に直す。

1. `ROUTINE.md` の手順5の表に項目を足す（ルーチンへの指示）
2. `scripts/report_tools.py` の `validate_data` に検証を足す（必須なら必ず検証する）
3. `template/report.html` で表示し、サンプルデータ（report-data ブロック）にもその項目を入れる
4. `tests/test_report_tools.py` に検証のテストを足す

## data/ のファイルに項目を足す

1. `scripts/lib/models.py` の TypedDict に足す
2. 書く側のスクリプトで値を入れる（mypy が漏れを教えてくれる）
3. `docs/data.md` を直す
4. ルーチンに使わせるなら `ROUTINE.md` の手順2・5に書く
