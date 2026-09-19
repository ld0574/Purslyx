#!/usr/bin/env bash
# Purslyx 单机蓝绿发布：宿主机 OpenResty，应用使用 Docker 双槽位。
# 新版本先启动 inactive 槽位并通过健康检查，再原子替换 OpenResty upstream。
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${PURSLYX_COMPOSE_FILE:-$ROOT_DIR/compose.yaml}"
ENV_FILE="${PURSLYX_ENV_FILE:-$ROOT_DIR/.env}"
STATE_DIR="${PURSLYX_DEPLOY_STATE_DIR:-/data/purslyx/.deploy}"
PROJECT_PREFIX="${PURSLYX_COMPOSE_PROJECT_PREFIX:-purslyx}"
OPENRESTY_BIN="${OPENRESTY_BIN:-$(command -v openresty || true)}"
OPENRESTY_CONF="${OPENRESTY_CONF:-/usr/local/openresty/nginx/conf/nginx.conf}"
CONFD_DIR="${PURSLYX_OPENRESTY_CONFD_DIR:-/usr/local/openresty/nginx/conf/conf.d}"
UPSTREAM_CONF="${PURSLYX_UPSTREAM_CONF:-$CONFD_DIR/purslyx-upstream.conf}"

HEALTH_TIMEOUT="${PURSLYX_HEALTH_TIMEOUT:-180}"
DRAIN_SECONDS="${PURSLYX_DRAIN_SECONDS:-30}"
STOP_TIMEOUT="${PURSLYX_STOP_TIMEOUT:-30}"

usage() {
  cat <<'USAGE'
用法：
  sudo scripts/release.sh deploy      构建并发布下一版本
  sudo scripts/release.sh rollback    切回上一个已发布版本
  scripts/release.sh status           查看当前槽位、版本和容器状态

发布过程：
  1. 构建 inactive 槽位的带版本标签镜像；
  2. 使用候选镜像执行 Alembic 向前迁移和幂等种子；
  3. 启动候选 API 与 Worker，等待 /health 和 Worker 进程通过；
  4. 原子替换 OpenResty upstream 并平滑 reload；
  5. 等待旧请求排空后停止旧槽位。

重要环境变量：
  PURSLYX_RELEASE_ID          自定义版本标签；默认 UTC 时间 + Git 短 SHA
  PURSLYX_NO_CACHE=1          构建时禁用 Docker 缓存
  PURSLYX_PYPI_INDEX_URL      Python/uv 包索引；默认 https://pypi.org/simple/
  PURSLYX_HEALTH_TIMEOUT=180  候选槽位健康检查超时秒数
  PURSLYX_DRAIN_SECONDS=30    切流量后等待旧请求排空的秒数
  PURSLYX_DEPLOY_STATE_DIR    默认 /data/purslyx/.deploy
  PURSLYX_ENV_FILE            默认仓库根目录 .env

数据库迁移必须遵循 expand/contract：切流量时旧槽位仍可能短暂处理请求，
不要在一次发布中删除旧字段或破坏旧接口。脚本不会执行数据库降级。
USAGE
}

log() {
  printf '[release] %s\n' "$*"
}

warn() {
  printf '[release] 警告：%s\n' "$*" >&2
}

die() {
  printf '[release] 错误：%s\n' "$*" >&2
  exit 1
}

validate_uint() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || die "$name 必须是非负整数：$value"
}

validate_token() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || die "$name 含有不允许的字符：$value"
}

slot_project() {
  printf '%s-%s\n' "$PROJECT_PREFIX" "$1"
}

slot_port() {
  case "$1" in
    blue) printf '%s\n' "${PURSLYX_BLUE_API_PORT:-18001}" ;;
    green) printf '%s\n' "${PURSLYX_GREEN_API_PORT:-28001}" ;;
    *) die "未知槽位：$1" ;;
  esac
}

slot_subnet() {
  case "$1" in
    blue) printf '%s\n' "${PURSLYX_BLUE_NETWORK_SUBNET:-172.29.109.0/24}" ;;
    green) printf '%s\n' "${PURSLYX_GREEN_NETWORK_SUBNET:-172.29.110.0/24}" ;;
    *) die "未知槽位：$1" ;;
  esac
}

image_name() {
  printf 'purslyx-app:%s\n' "$1"
}

release_id() {
  local value="${PURSLYX_RELEASE_ID:-}"
  if [[ -z "$value" ]]; then
    local sha
    sha="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || printf 'nogit')"
    value="$(date -u +%Y%m%d%H%M%S)-$sha"
    if [[ -n "$(git -C "$ROOT_DIR" status --porcelain 2>/dev/null || true)" ]]; then
      value+="-dirty"
      warn '工作区存在未提交改动，本次发布标签已加 dirty；生产发布前建议使用固定 Git 提交。'
    fi
  fi
  validate_token PURSLYX_RELEASE_ID "$value"
  ((${#value} <= 120)) || die '发布标签长度不能超过 120。'
  printf '%s\n' "$value"
}

require_tools() {
  [[ "$EUID" -eq 0 ]] || die 'deploy/rollback 需要 root，以便更新 OpenResty 配置并 reload。'
  [[ -f "$COMPOSE_FILE" ]] || die "Compose 文件不存在：$COMPOSE_FILE"
  [[ -r "$ENV_FILE" ]] || die "生产环境文件不存在或不可读：$ENV_FILE"
  command -v docker >/dev/null 2>&1 || die '找不到 docker。'
  docker compose version >/dev/null 2>&1 || die '当前 Docker 未提供 docker compose 子命令。'
  command -v curl >/dev/null 2>&1 || die '找不到 curl。'
  command -v systemctl >/dev/null 2>&1 || die '找不到 systemctl。'
  command -v flock >/dev/null 2>&1 || die '找不到 flock；请安装 util-linux。'
  [[ -n "$OPENRESTY_BIN" ]] || die '找不到 openresty 可执行文件。'
  [[ -f "$OPENRESTY_CONF" ]] || die "OpenResty 主配置不存在：$OPENRESTY_CONF"
  [[ -d "$CONFD_DIR" ]] || die "OpenResty conf.d 目录不存在：$CONFD_DIR"
  [[ -f "$CONFD_DIR/purslyx.conf" ]] || die "域名配置不存在：$CONFD_DIR/purslyx.conf"
  grep -Eq '^[[:space:]]*proxy_pass[[:space:]]+http://purslyx_api;' "$CONFD_DIR/purslyx.conf" \
    || die "$CONFD_DIR/purslyx.conf 尚未代理到 purslyx_api。"
  validate_uint PURSLYX_HEALTH_TIMEOUT "$HEALTH_TIMEOUT"
  validate_uint PURSLYX_DRAIN_SECONDS "$DRAIN_SECONDS"
  validate_uint PURSLYX_STOP_TIMEOUT "$STOP_TIMEOUT"
  mkdir -p "$STATE_DIR"
  chmod 0700 "$STATE_DIR"
}

acquire_lock() {
  exec 9>"$STATE_DIR/release.lock"
  flock -n 9 || die '已有另一个 deploy/rollback 正在运行。'
}

compose_slot() {
  local slot="$1"
  local tag="$2"
  shift 2
  local project port subnet
  project="$(slot_project "$slot")"
  port="$(slot_port "$slot")"
  subnet="$(slot_subnet "$slot")"
  env \
    PURSLYX_RUNTIME_ENV_FILE="$ENV_FILE" \
    PURSLYX_IMAGE_TAG="$tag" \
    PURSLYX_API_PORT="$port" \
    PURSLYX_NETWORK_NAME="$project" \
    PURSLYX_NETWORK_SUBNET="$subnet" \
    docker compose \
      --project-name "$project" \
      --env-file "$ENV_FILE" \
      -f "$COMPOSE_FILE" \
      "$@"
}

write_meta() {
  local file="$1"
  local slot="$2"
  local tag="$3"
  local tmp="$file.tmp.$$"
  umask 077
  printf 'SLOT=%s\nRELEASE=%s\nPORT=%s\n' \
    "$slot" "$tag" "$(slot_port "$slot")" > "$tmp"
  mv -f "$tmp" "$file"
}

load_meta() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  META_SLOT=''
  META_RELEASE=''
  META_PORT=''
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    value="${value%$'\r'}"
    case "$key" in
      ''|'#'*) continue ;;
      SLOT) META_SLOT="$value" ;;
      RELEASE) META_RELEASE="$value" ;;
      PORT) META_PORT="$value" ;;
      *) die "未知发布状态字段：$key" ;;
    esac
  done < "$file"
  [[ "$META_SLOT" == 'blue' || "$META_SLOT" == 'green' ]] || die "状态文件槽位不合法：$file"
  validate_token RELEASE "$META_RELEASE"
  [[ "$META_PORT" == "$(slot_port "$META_SLOT")" ]] || die "状态文件端口与槽位不匹配：$file"
}

load_active() {
  ACTIVE_KIND='none'
  ACTIVE_SLOT=''
  ACTIVE_RELEASE=''
  ACTIVE_PORT=''
  if load_meta "$STATE_DIR/active.env"; then
    ACTIVE_KIND='slot'
    ACTIVE_SLOT="$META_SLOT"
    ACTIVE_RELEASE="$META_RELEASE"
    ACTIVE_PORT="$META_PORT"
  fi
}

service_running() {
  local project="$1"
  local service="$2"
  local container
  container="$(docker ps -q \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$service" | head -n 1)"
  [[ -n "$container" ]] || return 1
  [[ "$(docker inspect -f '{{.State.Status}}' "$container")" == 'running' ]]
}

wait_candidate() {
  local slot="$1"
  local tag="$2"
  local project port deadline
  project="$(slot_project "$slot")"
  port="$(slot_port "$slot")"
  deadline=$((SECONDS + HEALTH_TIMEOUT))
  log "等待 $project 健康检查：API $port"
  while ((SECONDS < deadline)); do
    if curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$port/health" >/dev/null 2>&1 \
      && service_running "$project" worker; then
      log "$project 已通过 API 和 Worker 检查。"
      return 0
    fi
    sleep 2
  done
  compose_slot "$slot" "$tag" ps >&2 || true
  return 1
}

build_candidate() {
  local slot="$1"
  local tag="$2"
  local -a args=(build app)
  [[ "${PURSLYX_NO_CACHE:-0}" == '1' ]] && args=(build --no-cache app)
  log "构建 $slot 槽位镜像：$(image_name "$tag")"
  compose_slot "$slot" "$tag" "${args[@]}"
}

migrate_candidate() {
  local slot="$1"
  local tag="$2"
  log "使用 $slot 槽位镜像执行 Alembic 迁移。"
  compose_slot "$slot" "$tag" run --rm --no-deps app \
    /opt/venv/bin/python -m alembic upgrade head
  compose_slot "$slot" "$tag" run --rm --no-deps app \
    /opt/venv/bin/python scripts/init_201.py
}

start_candidate() {
  local slot="$1"
  local tag="$2"
  compose_slot "$slot" "$tag" up -d app worker
}

stop_slot() {
  local slot="$1"
  local tag="$2"
  local project
  project="$(slot_project "$slot")"
  if docker ps -aq --filter "label=com.docker.compose.project=$project" | grep -q .; then
    log "停止旧槽位：$project"
    compose_slot "$slot" "$tag" down --remove-orphans --timeout "$STOP_TIMEOUT" >/dev/null 2>&1 || \
      warn "旧槽位未能完全停止：$project"
  fi
}

cleanup_candidate() {
  local slot="$1"
  local tag="$2"
  compose_slot "$slot" "$tag" down --remove-orphans --timeout "$STOP_TIMEOUT" >/dev/null 2>&1 || true
}

render_upstream() {
  local port="$1"
  local file="$2"
  printf '%s\n' \
    '# Generated by scripts/release.sh; do not edit manually.' \
    'upstream purslyx_api {' \
    "    server 127.0.0.1:$port;" \
    '    keepalive 32;' \
    '}' > "$file"
}

restore_upstream() {
  local backup="$1"
  local existed="$2"
  if [[ "$existed" == '1' ]]; then
    mv -f "$backup" "$UPSTREAM_CONF"
  else
    rm -f "$UPSTREAM_CONF" "$backup"
  fi
  "$OPENRESTY_BIN" -t -c "$OPENRESTY_CONF" >/dev/null 2>&1 || true
  systemctl reload openresty >/dev/null 2>&1 || true
}

switch_upstream() {
  local port="$1"
  local tmp="$CONFD_DIR/.purslyx-upstream.$$"
  local backup="$CONFD_DIR/.purslyx-upstream.backup.$$"
  local existed=0
  render_upstream "$port" "$tmp"
  if [[ -e "$UPSTREAM_CONF" ]]; then
    cp -p "$UPSTREAM_CONF" "$backup"
    existed=1
  fi
  mv -f "$tmp" "$UPSTREAM_CONF"
  if ! "$OPENRESTY_BIN" -t -c "$OPENRESTY_CONF"; then
    restore_upstream "$backup" "$existed"
    return 1
  fi
  if ! systemctl reload openresty; then
    restore_upstream "$backup" "$existed"
    return 1
  fi
  if ! systemctl is-active --quiet openresty; then
    restore_upstream "$backup" "$existed"
    return 1
  fi
  rm -f "$backup"
}

deploy() {
  require_tools
  acquire_lock
  load_active

  local candidate tag old_slot old_release switched=0
  if [[ "$ACTIVE_KIND" == 'slot' ]]; then
    if [[ "$ACTIVE_SLOT" == 'blue' ]]; then candidate='green'; else candidate='blue'; fi
    old_slot="$ACTIVE_SLOT"
    old_release="$ACTIVE_RELEASE"
  else
    candidate='blue'
    old_slot=''
    old_release=''
  fi
  tag="$(release_id)"

  trap 'if (( switched == 0 )); then cleanup_candidate "$candidate" "$tag"; fi' ERR
  build_candidate "$candidate" "$tag"
  migrate_candidate "$candidate" "$tag"
  start_candidate "$candidate" "$tag"
  wait_candidate "$candidate" "$tag"
  switch_upstream "$(slot_port "$candidate")"
  switched=1

  if [[ -n "$old_slot" ]]; then
    write_meta "$STATE_DIR/previous.env" "$old_slot" "$old_release"
  else
    rm -f "$STATE_DIR/previous.env"
  fi
  write_meta "$STATE_DIR/active.env" "$candidate" "$tag"

  if [[ -n "$old_slot" ]]; then
    log "等待旧请求排空：${DRAIN_SECONDS}s"
    sleep "$DRAIN_SECONDS"
    stop_slot "$old_slot" "$old_release"
  fi
  trap - ERR
  log "发布完成：$candidate / $tag"
}

rollback() {
  require_tools
  acquire_lock
  load_active
  [[ "$ACTIVE_KIND" == 'slot' ]] || die '没有可回滚的 active 槽位。'
  load_meta "$STATE_DIR/previous.env" || die '没有可回滚的 previous 槽位。'

  local rollback_slot="$META_SLOT"
  local rollback_release="$META_RELEASE"
  local current_slot="$ACTIVE_SLOT"
  local current_release="$ACTIVE_RELEASE"
  local switched=0

  docker image inspect "$(image_name "$rollback_release")" >/dev/null 2>&1 || \
    die "找不到上一版本镜像：$(image_name "$rollback_release")；请勿在回滚前清理旧镜像。"

  trap 'if (( switched == 0 )); then cleanup_candidate "$rollback_slot" "$rollback_release"; fi' ERR
  compose_slot "$rollback_slot" "$rollback_release" up -d app worker
  wait_candidate "$rollback_slot" "$rollback_release"
  switch_upstream "$(slot_port "$rollback_slot")"
  switched=1

  write_meta "$STATE_DIR/active.env" "$rollback_slot" "$rollback_release"
  write_meta "$STATE_DIR/previous.env" "$current_slot" "$current_release"
  log "等待旧请求排空：${DRAIN_SECONDS}s"
  sleep "$DRAIN_SECONDS"
  stop_slot "$current_slot" "$current_release"
  trap - ERR
  log "回滚完成：$rollback_slot / $rollback_release"
}

status() {
  load_active
  printf 'active: %s\n' "$ACTIVE_KIND"
  if [[ "$ACTIVE_KIND" == 'slot' ]]; then
    printf 'slot: %s\nrelease: %s\nport: %s\n' "$ACTIVE_SLOT" "$ACTIVE_RELEASE" "$ACTIVE_PORT"
    compose_slot "$ACTIVE_SLOT" "$ACTIVE_RELEASE" ps || true
  fi
  if load_meta "$STATE_DIR/previous.env"; then
    printf 'previous: %s / %s / %s\n' "$META_SLOT" "$META_RELEASE" "$META_PORT"
  else
    printf 'previous: none\n'
  fi
}

main() {
  case "${1:-}" in
    deploy) deploy ;;
    rollback) rollback ;;
    status) status ;;
    -h|--help|help) usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"
