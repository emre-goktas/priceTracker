#!/usr/bin/env bash
# price-bot proje iskeletini oluşturur (boş klasör + placeholder dosyalar)
# Kullanım: bash scaffold.sh [hedef_dizin]
# Örnek:    bash scaffold.sh ~/projects/price-bot

set -e

ROOT="${1:-price-bot}"

echo "Oluşturuluyor: $ROOT"

# --- Klasörler ---
mkdir -p "$ROOT"/crawler
mkdir -p "$ROOT"/core/fetchers
mkdir -p "$ROOT"/core/parsers/site_plugins
mkdir -p "$ROOT"/matching
mkdir -p "$ROOT"/selfheal/providers
mkdir -p "$ROOT"/configs/tr
mkdir -p "$ROOT"/configs/versions/gratis/history
mkdir -p "$ROOT"/configs/versions/rossmann/history
mkdir -p "$ROOT"/configs/versions/watsons/history
mkdir -p "$ROOT"/db/migrations
mkdir -p "$ROOT"/dags
mkdir -p "$ROOT"/shared
mkdir -p "$ROOT"/.github/workflows
mkdir -p "$ROOT"/tests/crawler
mkdir -p "$ROOT"/tests/core
mkdir -p "$ROOT"/tests/matching
mkdir -p "$ROOT"/tests/selfheal

# --- crawler/ ---
touch "$ROOT"/crawler/__init__.py
touch "$ROOT"/crawler/db.py
touch "$ROOT"/crawler/models.py
touch "$ROOT"/crawler/robots_parser.py
touch "$ROOT"/crawler/sitemap_parser.py
touch "$ROOT"/crawler/engine.py
touch "$ROOT"/crawler/sync_to_postgres.py
touch "$ROOT"/crawler/cli.py

# --- core/ ---
touch "$ROOT"/core/__init__.py
touch "$ROOT"/core/storage.py
touch "$ROOT"/core/db.py
touch "$ROOT"/core/fetchers/__init__.py
touch "$ROOT"/core/fetchers/category_fetcher.py
touch "$ROOT"/core/fetchers/product_fetcher.py
touch "$ROOT"/core/parsers/__init__.py
touch "$ROOT"/core/parsers/base_parser.py
touch "$ROOT"/core/parsers/site_plugins/__init__.py
touch "$ROOT"/core/parsers/site_plugins/gratis.py
touch "$ROOT"/core/parsers/site_plugins/rossmann.py
touch "$ROOT"/core/parsers/site_plugins/watsons.py

# --- matching/ ---
touch "$ROOT"/matching/__init__.py
touch "$ROOT"/matching/duckdb_pipeline.py
touch "$ROOT"/matching/normalize.py
touch "$ROOT"/matching/ean_match.py
touch "$ROOT"/matching/fuzzy_match.py
touch "$ROOT"/matching/sanity_checks.py
touch "$ROOT"/matching/review_queue.py
touch "$ROOT"/matching/load_to_postgres.py

# --- selfheal/ ---
touch "$ROOT"/selfheal/__init__.py
touch "$ROOT"/selfheal/failure_detector.py
touch "$ROOT"/selfheal/context_builder.py
touch "$ROOT"/selfheal/validator.py
touch "$ROOT"/selfheal/engine.py
touch "$ROOT"/selfheal/providers/__init__.py
touch "$ROOT"/selfheal/providers/gemini_provider.py
touch "$ROOT"/selfheal/providers/antigravity_cli_provider.py
touch "$ROOT"/selfheal/providers/nvidia_nim_provider.py

# --- configs/ ---
touch "$ROOT"/configs/sites.yaml
touch "$ROOT"/configs/tr/gratis.yaml
touch "$ROOT"/configs/tr/rossmann.yaml
touch "$ROOT"/configs/tr/watsons.yaml
touch "$ROOT"/configs/versions/gratis/active.yaml
touch "$ROOT"/configs/versions/rossmann/active.yaml
touch "$ROOT"/configs/versions/watsons/active.yaml

# --- db/ ---
touch "$ROOT"/db/postgres_schema.sql
touch "$ROOT"/db/migrations/0001_init.sql

# --- dags/ ---
touch "$ROOT"/dags/crawler_dag.py
touch "$ROOT"/dags/fetch_dag.py
touch "$ROOT"/dags/matching_dag.py
touch "$ROOT"/dags/selfheal_dag.py

# --- shared/ ---
touch "$ROOT"/shared/__init__.py
touch "$ROOT"/shared/hashing.py
touch "$ROOT"/shared/http_client.py
touch "$ROOT"/shared/logging_config.py

# --- .github/workflows/ ---
touch "$ROOT"/.github/workflows/test.yml
touch "$ROOT"/.github/workflows/lint.yml
touch "$ROOT"/.github/workflows/config-validate.yml

# --- tests/ ---
touch "$ROOT"/tests/__init__.py
touch "$ROOT"/tests/crawler/__init__.py
touch "$ROOT"/tests/core/__init__.py
touch "$ROOT"/tests/matching/__init__.py
touch "$ROOT"/tests/selfheal/__init__.py

# --- kök dosyalar ---
touch "$ROOT"/.env.example
touch "$ROOT"/requirements.txt
touch "$ROOT"/pyproject.toml
touch "$ROOT"/README.md

echo "Tamamlandı. Ağaç yapısı:"
if command -v tree >/dev/null 2>&1; then
  tree "$ROOT"
else
  find "$ROOT" | sed -e "s|[^/]*/|  |g"
fi
