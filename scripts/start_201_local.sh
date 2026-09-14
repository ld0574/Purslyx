#!/usr/bin/env bash

# 使用本地开发环境文件启动 Purslyx。该文件只读取连接信息，不修改或输出密码。
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
local_env_file="${PURSLYX_LOCAL_ENV_FILE:-$project_dir/docs/02-technical/deployment/本地开发环境.md}"

if [[ ! -f "$local_env_file" ]]; then
  echo "找不到本地开发环境文件：$local_env_file" >&2
  exit 1
fi

postgres_endpoint="$(awk -F: '/^postgresql:/{print $2 ":" $3; exit}' "$local_env_file")"
postgres_host="${postgres_endpoint%:*}"
postgres_port="${postgres_endpoint##*:}"
postgres_user="$(awk -F: '/^用户名:/{sub(/^[^:]*:/, ""); print; exit}' "$local_env_file")"
postgres_database="$(awk -F: '/^数据库:/{sub(/^[^:]*:/, ""); print; exit}' "$local_env_file")"
postgres_password="$(awk -F: '/^postgresql:/{in_postgres=1; next} in_postgres && /^密码:/{sub(/^[^:]*:/, ""); print; exit}' "$local_env_file")"
if [[ -z "$postgres_host" || -z "$postgres_port" || -z "$postgres_user" || -z "$postgres_database" || -z "$postgres_password" ]]; then
  echo "本地开发环境文件中缺少完整 PostgreSQL 连接信息" >&2
  exit 1
fi
if [[ "$postgres_host" != "10.10.10.201" ]]; then
  echo "本地开发环境文件中的 PostgreSQL 必须是 201 环境" >&2
  exit 1
fi

expected_database_url="postgresql+psycopg://${postgres_user}@${postgres_host}:${postgres_port}/${postgres_database}"
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
