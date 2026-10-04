---
name: sync-fork
description: テンプレート（upstream の x-bookmark-digest-template）の更新を、自分のリポジトリの main に取り込む。競合の解き方と、取り込んだあとの確認・push までを行う。
disable-model-invocation: true
---

テンプレートから作った自分のリポジトリで、テンプレートの更新を取り込む。README の「テンプレートの更新を取り込む」を Claude Code で行う手順。

## 1. 前提を確かめる

- `git remote get-url upstream` が `x-bookmark-digest-template` を指していること。なければ README の「テンプレートの更新を取り込む」を示して止まる。
- `origin` がテンプレート自身なら止まる（テンプレートでは取り込むものがない）。
- `git status --porcelain` で、追跡しているファイルに変更がないこと。あれば止めて利用者に伝える（未追跡のファイルは触らない）。

## 2. 取り込むものを見る

```bash
git pull --ff-only origin main      # 取得ワークフローが data/ を足しているので先に取り込む
git fetch upstream
git log --oneline main..upstream/main
```

取り込むコミットがなければ、そう伝えて終える。

## 3. マージする

```bash
git merge --no-edit upstream/main   # 初回だけ --allow-unrelated-histories を付ける
```

競合したら、ファイルごとに自分の側の変更を確かめてから解く（`git diff $(git merge-base HEAD upstream/main) HEAD -- <ファイル>`）。

| ファイル | 解き方 |
| --- | --- |
| 自分のリポジトリでだけ変えるもの（`config/enabled.json`、`config/report.json`、`data/`、`state/`） | 自分の側（`git checkout --ours`） |
| それ以外で、自分の側の変更がテンプレートにすでに入っている（同じ変更を別のコミットとして持っているだけ） | テンプレートの側（`git checkout --theirs`）。採用後に `git diff upstream/main -- <ファイル>` が空になることを確かめる |
| 自分で独自に変えた箇所がある | 両方を生かして手で直す。判断がつかなければ差分を見せて利用者に決めてもらう |

## 4. 確かめて push する

1. `make check` を通す。通らなければ push せず、結果を伝える。
2. 取り込んだコミットの一覧と `make check` の結果を見せ、push してよいか確認してから `git push origin main`（利用者がすでに push まで頼んでいれば確認を省く）。
3. テンプレートの見た目が変わったときは、日報のアーティファクトは翌朝のルーチンが公開し直すことを伝える。

## 再発を防ぐ

テンプレート側の不具合や改善は、自分のリポジトリで直さずにテンプレートで直してから、この手順で取り込む。
同じ変更が別のコミットとして両方に入ると、次の取り込みで競合する。
