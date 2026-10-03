"""取得層の共通部品。入口のスクリプト（scripts/*.py）だけがここを import する。

| モジュール | 中身 |
| --- | --- |
| store.py | リポジトリ上のパス、日付、JSON の読み書き、設定の読み込み |
| urls.py | URL の正規化・キー・記事かどうかの判定、HTML から文字だけを取り出す |
| web.py | HTTP クライアント、上限付きの取得、robots.txt の判定 |
| actions.py | GitHub Actions への注釈と出力 |
| models.py | data/ と state/ に書く JSON の形（TypedDict） |
"""
