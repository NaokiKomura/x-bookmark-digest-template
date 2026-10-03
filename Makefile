# よく使うコマンド。迷ったら make help
.PHONY: help setup fmt lint typecheck test check try preview

TRY_DIR ?= /tmp/x-bookmark-digest-try

help: ## コマンドの一覧
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  make %-10s %s\n", $$1, $$2}'

setup: ## 依存を入れる
	uv sync

fmt: ## 整形と自動修正
	uv run ruff format .
	uv run ruff check --fix .

lint: ## lint と整形の確認
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## 型チェック（scripts/）
	uv run mypy

test: ## テスト（外部 API は呼ばない）
	uv run pytest

check: lint typecheck test ## コミット前に必ず通す（lint + 型 + テスト + テンプレートの検証）
	python3 scripts/report_tools.py validate template/report.html

try: ## 実際のサイトから取得して試す（X は呼ばない。出力は TRY_DIR。TYPESAFE_API_KEY があれば Jev も呼ぶ）
	@case "$(abspath $(TRY_DIR))/" in "$(CURDIR)/"*) echo "TRY_DIR をリポジトリの中にしないでください（data/ と state/ を壊さないため）"; exit 1;; esac
	rm -rf $(TRY_DIR) && mkdir -p $(TRY_DIR)/state
	DIGEST_ROOT=$(TRY_DIR) uv run python -m scripts.fetch_sources
	DIGEST_ROOT=$(TRY_DIR) uv run python -m scripts.fetch_articles
	DIGEST_ROOT=$(TRY_DIR) uv run python -m scripts.classify_jev
	@echo "結果: $(TRY_DIR)/data/"

preview: ## サンプルデータ入りのレポートをブラウザで開く
	open template/report.html || xdg-open template/report.html
