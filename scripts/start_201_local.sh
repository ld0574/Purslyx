#!/usr/bin/env bash

# 使用本地开发环境文件启动 Purslyx。该文件只读取密码，不修改或输出密码。
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
local_env_file="${PURSLYX_LOCAL_ENV_FILE:-$project_dir/docs/02-technical/deployment/本地开发环境.md}"

if [[ ! -f "$local_env_file" && -z "${PGPASSWORD:-}" ]]; then
  echo "找不到本地开发环境文件：$local_env_file" >&2
  exit 1
fi

postgres_password="${PGPASSWORD:-}"
if [[ -z "$postgres_password" ]]; then
  postgres_password="$(awk '/^postgresql:/{in_postgres=1; next} in_postgres && /^密码:/{sub(/^密码:/, ""); print; exit}' "$local_env_file")"
fi
if [[ -z "$postgres_password" ]]; then
  echo "本地开发环境文件中没有 PostgreSQL 密码" >&2
  exit 1
fi

expected_database_url="postgresql+psycopg://purslyx@10.10.10.201:5432/purslyx"
database_url="${DATABASE_URL:-$expected_database_url}"
if [[ "$database_url" != "$expected_database_url" ]]; then
  echo "DATABASE_URL 必须固定为本地开发环境文件中的 201 PostgreSQL" >&2
  exit 1
fi

export DATABASE_URL="$database_url"
export PGPASSWORD="$postgres_password"
export PURSLYX_DEBUG="${PURSLYX_DEBUG:-true}"
export PURSLYX_AUTO_VERIFY_LOCAL="${PURSLYX_AUTO_VERIFY_LOCAL:-true}"
export PURSLYX_AUTO_CREATE_SCHEMA="${PURSLYX_AUTO_CREATE_SCHEMA:-true}"
export PURSLYX_TOKEN_SECRET="${PURSLYX_TOKEN_SECRET:-purslyx-local-demo-secret-2026-change-me}"

if [[ ! -x "$project_dir/.venv/bin/uvicorn" ]]; then
  echo "找不到 .venv/bin/uvicorn，请先执行：uv venv .venv && uv pip install -e '.[test]'" >&2
  exit 1
fi

cd "$project_dir"
exec "$project_dir/.venv/bin/uvicorn" server.app.main:app \
  --host "${PURSLYX_HOST:-127.0.0.1}" \
  --port "${PURSLYX_PORT:-8001}"
