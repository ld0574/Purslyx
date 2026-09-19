# Purslyx Debian 单机部署

> Debian 宿主机运行 OpenResty、PostgreSQL、Redis；Purslyx 应用使用 Docker；应用通过 `purslyx-api1` / `purslyx-api2` 双槽位滚动发布和回滚。

## 1. 部署架构

Vue 前端在 Docker 镜像构建阶段生成 `src/web/dist`，再由同一个 FastAPI 进程提供页面和 API。应用镜像包含：

- `app`：FastAPI + Vue 生产构建产物；
- `worker`：同一镜像中的 PostgreSQL outbox Worker；
- OpenResty：宿主机 HTTPS 入口和反向代理；
- PostgreSQL、Redis：宿主机安装和持久化。

当前任务 Worker 的队列和租约由 PostgreSQL 实现。Redis 按本方案部署并通过 `REDIS_URL` 注入，作为缓存/队列基础设施预留；现有任务执行路径不会因为 Redis 不可用而切换。

| 组件 | 位置 | 地址 |
| --- | --- | --- |
| OpenResty | Debian 宿主机 | `0.0.0.0:80/443` |
| PostgreSQL | Debian 宿主机 | `127.0.0.1`、Docker 宿主网关 |
| Redis | Debian 宿主机 | `127.0.0.1`、Docker 宿主网关 |
| API + Vue | Docker `purslyx-api1` / `purslyx-api2` | `127.0.0.1:18001` / `28001` |
| Worker | Docker `purslyx-worker1` / `purslyx-worker2` | 无公网端口 |

默认 Docker 网段：`purslyx-api1` 为 `172.29.109.0/24`，`purslyx-api2` 为 `172.29.110.0/24`。安全组只放行 `80`、`443` 和受限的 `22`，不要放行 `5432`、`6379`、`18001`、`28001`。

这是单机双槽位发布，不是多机高可用。切流量时新旧应用会短暂并存；数据库迁移必须采用 expand/contract，不能在一次发布中删除旧字段或破坏旧接口。

## 2. 服务器目录和依赖

本文以 Debian 12 Bookworm 为基线。OpenResty 官方当前提供 Debian 11/12 的 amd64 和 arm64 包；Debian 13 不要强行使用 Bookworm 仓库。[OpenResty 官方包说明](https://openresty.org/en/linux-packages.html)

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release rsync util-linux
sudo install -d -m 0755 /data
sudo install -d -m 0755 -o "$USER" /data/purslyx
sudo install -d -m 0700 /data/purslyx/data /data/purslyx/.deploy
sudo install -d -m 0755 -o www-data -g www-data /data/logs/openresty
git clone <仓库地址> /data/purslyx
cd /data/purslyx
sudo chown -R 10001:10001 /data/purslyx/data
```

安装 Docker Engine 和 Compose v2 插件时使用 Docker 官方 Debian 方式，确认：

```bash
docker --version
docker compose version
sudo usermod -aG docker "$USER"
```

参考：[Docker Compose plugin](https://docs.docker.com/compose/install/linux/)。重新登录后 Docker 组权限才会生效。

## 3. 宿主机 PostgreSQL

Debian 自带 PostgreSQL；需要指定版本时使用 PostgreSQL 官方 APT 仓库。[PostgreSQL Debian 安装说明](https://www.postgresql.org/download/linux/debian/)

```bash
sudo apt install -y postgresql postgresql-client
sudo systemctl enable --now postgresql
sudo -u postgres createuser --pwprompt --no-superuser --no-createdb --no-createrole purslyx
sudo -u postgres createdb --owner=purslyx purslyx
```

确认 Docker 宿主网关，通常为 `172.17.0.1`：

```bash
docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

编辑 `/etc/postgresql/<version>/main/postgresql.conf`：

```text
listen_addresses = '127.0.0.1,172.17.0.1'
password_encryption = 'scram-sha-256'
```

在 `pg_hba.conf` 末尾加入：

```text
host    purslyx    purslyx    172.29.109.0/24    scram-sha-256
host    purslyx    purslyx    172.29.110.0/24    scram-sha-256
```

```bash
sudo systemctl restart postgresql
sudo ss -lntp | grep 5432
psql 'postgresql://purslyx@127.0.0.1:5432/purslyx' -c 'SELECT 1;'
```

如需独立磁盘，设置 `data_directory = '/data/postgresql/<version>/main'`，并用 `SHOW data_directory;` 验证迁移结果后再删除旧目录。

## 4. 宿主机 Redis

当前 Worker 不依赖 Redis，但 Redis 按要求在宿主机运行。[Redis 官方安装说明](https://redis.io/docs/latest/operate/oss_and_stack/install/)

```bash
sudo apt install -y redis-server
sudo install -d -m 0750 -o redis -g redis /data/redis
sudo systemctl enable redis-server
```

编辑 `/etc/redis/redis.conf`，设置随机密码、监听和持久化：

```text
bind 127.0.0.1 172.17.0.1
protected-mode yes
port 6379
dir /data/redis
appendonly yes
appendfsync everysec
save 900 1
save 300 10
save 60 10000
maxmemory-policy noeviction
requirepass <随机密码>
```

```bash
sudo systemctl restart redis-server
redis-cli --askpass PING
redis-cli -h 172.17.0.1 --askpass PING
```

两次都应返回 `PONG`。Redis 不应暴露到公网；AOF/RDB 不代替 PostgreSQL 业务备份。

## 5. 宿主机 OpenResty 和证书

如果已有 Nginx，先释放 80/443：

```bash
sudo systemctl disable --now nginx 2>/dev/null || true
```

Debian 12 安装 OpenResty：

```bash
sudo apt install -y --no-install-recommends wget gnupg ca-certificates
wget -O - https://openresty.org/package/pubkey.gpg \
  | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/openresty.gpg
codename="$(. /etc/os-release && printf '%s' "$VERSION_CODENAME")"
arch="$(dpkg --print-architecture)"
if [ "$arch" = "arm64" ]; then repo_arch=arm64; else repo_arch=debian; fi
echo "deb https://openresty.org/package/$repo_arch/debian $codename openresty" \
  | sudo tee /etc/apt/sources.list.d/openresty.list
sudo apt update
sudo apt install -y --no-install-recommends openresty
sudo systemctl enable --now openresty
```

确保 OpenResty 主配置的 `http {}` 内包含 `include conf.d/*.conf;`，然后安装仓库配置：

```bash
sudo install -d -m 0755 /usr/local/openresty/nginx/conf/conf.d
sudo cp infra/openresty/purslyx.conf \
  /usr/local/openresty/nginx/conf/conf.d/purslyx.conf
sudo cp infra/openresty/purslyx-upstream.conf.example \
  /usr/local/openresty/nginx/conf/conf.d/purslyx-upstream.conf
```

证书路径：

```text
/etc/letsencrypt/live/purslyx.com/fullchain.pem
/etc/letsencrypt/live/purslyx.com/privkey.pem
```

```bash
sudo openresty -t
sudo systemctl reload openresty
sudo certbot renew --deploy-hook 'systemctl reload openresty'
```

`purslyx.conf` 的 `proxy_pass` 指向 `http://purslyx_api;`；发布脚本会原子替换同目录的 `purslyx-upstream.conf`。

## 6. 生产环境变量

```bash
cd /data/purslyx
cp .env.example .env
chmod 600 .env
```

至少设置：

```dotenv
DATABASE_URL=postgresql+psycopg://purslyx@host.docker.internal:5432/purslyx
PGPASSWORD=<postgres-password>
PURSLYX_ALLOWED_DATABASE_HOSTS=host.docker.internal
REDIS_URL=redis://:<url-encoded-redis-password>@host.docker.internal:6379/0

PURSLYX_DEBUG=false
PURSLYX_ENVIRONMENT=production
PURSLYX_AUTO_VERIFY_LOCAL=false
PURSLYX_AUTO_CREATE_SCHEMA=false
PURSLYX_EXECUTION_MODE=worker
PURSLYX_TOKEN_SECRET=<至少32个字符的随机值>
PURSLYX_DATA_DIR=/var/lib/purslyx
PURSLYX_DATA_DIR_HOST=/data/purslyx/data

PURSLYX_PRODUCT_ORIGIN=https://purslyx.com
PURSLYX_ALLOWED_ORIGINS=https://purslyx.com
PURSLYX_COOKIE_SECURE=true
PURSLYX_ALLOWED_BROWSER_ORIGINS=https://www.zhipin.com,https://zhipin.com,https://www.liepin.com,https://liepin.com
PURSLYX_MODEL_PROVIDER=local
```

首次初始化管理员时临时设置 `PURSLYX_ADMIN_EMAIL` 和 `PURSLYX_ADMIN_PASSWORD`。Redis 密码含 URL 特殊字符时需要先编码；`PGPASSWORD` 保留原始密码。

## 7. 首次构建和部署

应用镜像包含 Vue 构建产物、FastAPI、Alembic 文件和容器 Worker。不要在宿主机单独启动 Python API。

```bash
cd /data/purslyx
docker compose --env-file .env config >/tmp/purslyx-compose.config
sudo scripts/release.sh deploy
sudo scripts/release.sh status
```

发布脚本会：

1. 构建 inactive 槽位的带版本标签镜像；
2. 使用候选镜像执行 `alembic upgrade head` 和幂等种子；
3. 启动候选 API 与 Worker；
4. 检查候选 API 的 `/health` 和 Worker 进程；
5. 原子替换 OpenResty upstream，执行配置检查并 reload；
6. 等待旧请求排空后停止旧槽位。

验收：

```bash
curl --fail https://purslyx.com/health
curl --fail -I https://purslyx.com/
sudo systemctl status postgresql redis-server openresty --no-pager
```

## 8. 滚动发布和回滚

正常发布：

```bash
cd /data/purslyx
git pull --ff-only
sudo scripts/release.sh deploy
sudo scripts/release.sh status
```

发布路径为：当前 `purslyx-api1` → 构建 `purslyx-api2` → 迁移并启动 `purslyx-api2` → 健康检查 → upstream 切到 `28001` → 排空旧请求 → 停止 `purslyx-api1`；下一次发布反向进行。

回滚只切回上一版应用镜像，不会自动回滚数据库：

```bash
sudo scripts/release.sh rollback
sudo scripts/release.sh status
```

不要在确认下一版稳定前执行 `docker image prune -a`。旧镜像必须保留，数据库迁移必须保证上一版仍可读取当前结构。

可选参数：

```bash
sudo env PURSLYX_NO_CACHE=1 scripts/release.sh deploy
sudo env PURSLYX_HEALTH_TIMEOUT=300 PURSLYX_DRAIN_SECONDS=45 scripts/release.sh deploy
```

## 9. 备份

```bash
sudo install -d -m 0700 -o postgres -g postgres /data/backup/postgresql
sudo -u postgres pg_dump --format=custom \
  --file=/data/backup/postgresql/purslyx-$(date +%F).dump purslyx
sudo -u postgres pg_restore --list \
  /data/backup/postgresql/purslyx-$(date +%F).dump >/dev/null
sudo rsync -aH --delete /data/purslyx/data/ /data/backup/purslyx-data/
```

只备份数据库而不备份 `/data/purslyx/data`，会导致业务记录仍在但上传原件和 PDF 无法下载。备份还应复制到异机或对象存储。

## 10. 常见问题

### 容器提示数据库主机不允许

确认 `.env` 同时包含：

```dotenv
DATABASE_URL=postgresql+psycopg://purslyx@host.docker.internal:5432/purslyx
PURSLYX_ALLOWED_DATABASE_HOSTS=host.docker.internal
```

### 容器连接不上 PostgreSQL

检查 `listen_addresses`、`pg_hba.conf` 和两个 Docker 网段：

```bash
docker network inspect purslyx-api1
sudo ss -lntp | grep 5432
sudo grep -n '172.29.10' /etc/postgresql/*/main/pg_hba.conf
```

### 首次迁移提示 `DuplicateColumn: page_count already exists`

这是旧版迁移链的兼容性问题：`0001_baseline` 会按当前 ORM 模型创建基线，
而旧版 `0008_preference_source_contract` 又无条件添加 `resume_exports.page_count`。
当前版本已将 `0008` 和 `0009` 改为幂等迁移，已有列或约束会自动跳过。

如果旧镜像已经在这里失败，不要手动删除 `page_count`，先拉取修复后的代码再重新发布：

```bash
cd /data/purslyx
git pull --ff-only
sudo scripts/release.sh deploy
```

PostgreSQL 会回滚失败迁移事务，通常不需要手动修改 `alembic_version`。如需确认：

```bash
psql 'postgresql://purslyx@127.0.0.1:5432/purslyx' \
  -c 'SELECT version_num FROM alembic_version;'
psql 'postgresql://purslyx@127.0.0.1:5432/purslyx' \
  -c "SELECT column_name FROM information_schema.columns WHERE table_name = 'resume_exports' AND column_name = 'page_count';"
```

### 任务一直处于 queued

确认 API 配置了 `PURSLYX_EXECUTION_MODE=worker`，并查看 Worker：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker logs --tail=200 purslyx-worker1
```

当前 Worker 依赖 PostgreSQL outbox，不要只检查 Redis。

### OpenResty 检查失败

```bash
sudo openresty -t -c /usr/local/openresty/nginx/conf/nginx.conf
sudo grep -n 'purslyx_api\|proxy_pass' /usr/local/openresty/nginx/conf/conf.d/*.conf
```

确认 `purslyx.conf` 的 `proxy_pass` 是 `http://purslyx_api;`，且 upstream 文件位于主配置 `include` 的目录中。

## 11. 仓库内相关文件

- [`compose.yaml`](../../../compose.yaml)：应用和 Worker 的单槽位 Compose 定义。
- [`infra/docker/Dockerfile`](../../../infra/docker/Dockerfile)：Vue 构建和 Python 运行时的多阶段镜像。
- [`scripts/release.sh`](../../../scripts/release.sh)：`purslyx-api1` / `purslyx-api2` 发布、切流量和回滚。
- [`scripts/worker.py`](../../../scripts/worker.py)：容器化 Worker 入口。
- [`infra/openresty/purslyx.conf`](../../../infra/openresty/purslyx.conf)：域名、证书和反向代理配置。
- [`201环境部署.md`](201环境部署.md)：仅用于本地 201 开发环境。
