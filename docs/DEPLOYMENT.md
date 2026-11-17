# QuantGPT 部署指南

本文给出一套可执行的部署路径：Docker 适合服务器或长期运行，裸机部署适合本地/内网调试。生产或多人共享环境必须开启认证。

## 0. 选择部署方式

| 场景 | 推荐方式 | 说明 |
|:---|:---|:---|
| 本机体验、Agent 调试 | 裸机安装后运行 `make dev` 或 `./scripts/dev.ps1` | 监听回环地址，首次启动前配置认证密钥 |
| 单台服务器、长期运行 | Docker Compose | 镜像内构建前端，数据落到 Docker volume，升级和回滚更可控 |
| 小团队内网/VPN | Docker Compose + PostgreSQL | 保持认证开启，CORS 只允许可信域名或内网地址 |
| 公网域名访问 | 不建议直接暴露 | 必须放在 VPN、零信任隧道或带认证的反向代理之后 |

服务启动后，一个 HTTP 进程同时提供：

- FastAPI REST API：`/api/v1/*`
- React 前端：由后端服务 `frontend/dist`
- 报告和图表静态资源：`/api/v1/reports/*`、`/charts/*`
- HTTP MCP 位于 `/mcp/`、`/mcp-sse/`。开启认证时支持用户 JWT、用户 API key 或部署令牌。

## 1. 部署前检查

| 项目 | 最低要求 | 推荐 |
|:---|:---|:---|
| OS | 裸机安装提供 Windows x64 和 Linux x64 依赖锁 | Docker 部署使用 Linux 容器 |
| CPU | 2 核 | 4 核以上 |
| 内存 | 4 GB | 8 GB以上 |
| 磁盘 | 20 GB | 50 GB以上，行情缓存会增长 |
| Python | CPython 3.12 | 后端开发和运行均使用 3.12 |
| Node.js | 构建浏览器工作台时需要 | 仓库 CI 使用 Node 24 |
| Docker | 24+，仅 Docker 部署需要 | Docker Compose v2 |

对外访问建议放在 localhost、VPN 或可信内网。QuantGPT 包含管理后台、任务执行、报告读取、HTTP MCP 和 WQ BRAIN 提交能力，不建议直接暴露在公网。

部署前确认仓库里至少存在这些文件：

```bash
Dockerfile
docker-compose.yml
.env.example
alembic.deploy.ini
docs/DEPLOYMENT.md
```

## 2. 推荐部署：Docker

```bash
git clone https://github.com/popura404/QuantGPT_o.git
cd QuantGPT_o

cp .env.example .env
```

编辑 `.env`，至少改掉下面三项：

```bash
AUTH_DISABLED=false
JWT_SECRET_KEY=<openssl rand -hex 32 生成>
QUANTGPT_ADMIN_PASSWORD=<使用密码管理器生成的长随机密码>
```

`docker-compose.yml` 中的 `8003:8003` 默认在宿主机所有接口发布端口。若从本机使用，或同机 Nginx 负责对外访问，在首次启动前将 `services.quantgpt.ports` 中的这一条改成 `"127.0.0.1:8003:8003"`。用 `docker compose port quantgpt 8003` 核对实际发布地址。

启动服务后检查端口和健康状态。

```bash
docker compose up -d --build
docker compose ps
docker compose port quantgpt 8003
docker compose logs -f quantgpt
```

访问：

```text
http://localhost:8003
```

健康检查：

```bash
curl http://localhost:8003/api/v1/health
```

期望返回包含 `"status":"ok"` 的 JSON。

### Docker 数据持久化

默认 `docker-compose.yml` 使用 named volume 保存运行数据：

| 内容 | 容器路径 | Volume |
|:---|:---|:---|
| SQLite 数据库 | `/app/data/quantgpt.db` | `quantgpt-data` |
| 行情/任务数据 | `/app/data` | `quantgpt-data` |
| HTML 报告 | `/app/reports` | `quantgpt-reports` |

如果 `.env` 里设置了 `DATABASE_URL`，Compose 会使用该地址；否则默认使用容器内 SQLite：`sqlite+aiosqlite:////app/data/quantgpt.db`。

### Docker 运行命令速查

```bash
docker compose up -d --build      # 构建并启动
docker compose logs -f quantgpt   # 跟随日志
docker compose restart quantgpt   # 重启服务
docker compose down               # 停止服务，保留 volume
docker compose pull               # 仅在使用远程镜像时需要
```

## 3. 裸机部署

```bash
git clone https://github.com/popura404/QuantGPT_o.git
cd QuantGPT_o

make setup
# 编辑 .env，设置 JWT_SECRET_KEY 和 QUANTGPT_ADMIN_PASSWORD
# 如需浏览器工作台，执行 make frontend
make dev
```

`make setup` 使用 CPython 3.12 创建 `.venv312`，安装锁定的开发依赖，并在本地缺少 `.env` 时复制 `.env.example`。默认 `AUTH_DISABLED=false`。已有 `.env` 会继续使用。Windows 使用 `./scripts/setup.ps1` 安装，配置密钥后运行 `./scripts/dev.ps1`，两种启动方式均绑定 `127.0.0.1:8003`。

常用命令：

```bash
make run              # 前台启动后端，默认 http://localhost:8003
make dev              # 显式使用 8003 端口
cd frontend && npm run dev
```

## 4. 配置矩阵

### 必填生产配置

```bash
AUTH_DISABLED=false
JWT_SECRET_KEY=<openssl rand -hex 32>
QUANTGPT_ADMIN_PASSWORD=<strong password>
QUANTGPT_ALLOW_GUEST_BACKTEST=false
```

### Web 与 CORS

```bash
QUANTGPT_CORS_ORIGINS=http://localhost:5173,http://localhost:8003
QUANTGPT_ALLOWED_HOSTS=localhost,127.0.0.1
```

只填写可信的前端地址。远程访问时建议使用 VPN、零信任隧道或带身份认证的反向代理。
`QUANTGPT_ALLOWED_HOSTS` 是 FastAPI 的 Host header allowlist，填写 host 名，不带端口。

如果用统一域名反向代理，例如 `https://quantgpt.example.com`，则配置：

```bash
QUANTGPT_CORS_ORIGINS=https://quantgpt.example.com
QUANTGPT_ALLOWED_HOSTS=quantgpt.example.com,localhost,127.0.0.1
```

### 数据库

SQLite 零配置，适合本机、小团队或单机 Docker：

```bash
DATABASE_URL=
```

PostgreSQL 示例：

```bash
DATABASE_URL=postgresql+asyncpg://quantgpt:<password>@<host>:5432/quantgpt
```

空数据库可在启动时由 SQLAlchemy 创建。已有数据库先停写、备份并核对 Alembic 版本，再按[迁移说明](RESEARCH_MIGRATION.md)操作。

裸机执行迁移：

```bash
.venv312/bin/python -m alembic -c alembic.deploy.ini upgrade head
```

Docker 执行迁移：

```bash
docker compose run --rm quantgpt alembic -c alembic.deploy.ini upgrade head
```

### LLM

不配置 LLM 时仍可使用表达式模式。

```bash
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

### 行情与 WQ BRAIN

```bash
RQDATAC_USERNAME=
RQDATAC_PASSWORD=
WQ_BRAIN_EMAIL=
WQ_BRAIN_PASSWORD=
```

WQ BRAIN 远程模拟可用于研究验证；正式 submit 路径仍会经过本地 OOS/data-quality/promotion preflight，或要求调用方显式提供 `submission_override_reason`。

### 任务执行

```bash
QUANTGPT_TASK_BACKEND=process
QUANTGPT_WORKER_PROCESSES=4
```

单机部署优先使用 `process`。如需 Celery，配置 Redis broker/result backend，并安装 `quantgpt[celery]`。

### Rust 加速引擎

```bash
QUANTGPT_RUST_ENGINE=1
```

`QUANTGPT_RUST_ENGINE=1` 允许加载已安装的扩展。受信任的研究计算还检查 Rust 构建的数值一致性和表达式支持范围。当前一致性标记尚未验证，受信任表达式沿用 Python。Docker 镜像默认使用 Python 后端。

## 5. MCP 集成

优先使用 stdio MCP，只暴露给本机进程：

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "/absolute/path/to/QuantGPT_o/.venv312/bin/python",
      "args": ["-m", "quantgpt", "--transport", "stdio"],
      "cwd": "/absolute/path/to/QuantGPT_o"
    }
  }
}
```

Windows stdio 的解释器路径为项目内 `.venv312/Scripts/python.exe`。多代理共享研究统一按[主代理和子代理操作指南](agent_safe_workflow.md)分配方向和凭据。

HTTP 服务在开启认证时，从 `Authorization: Bearer <凭据>` 解析身份。使用用户 JWT 或用户 API key 会以该用户的项目权限访问。若服务间共用部署令牌，则设置以下变量，把令牌仅交给可信客户端。部署令牌映射到服务器配置的 `QUANTGPT_MCP_HTTP_USER_ID`，省略时使用 MCP 系统身份。

```bash
QUANTGPT_MCP_HTTP_TOKEN=<openssl rand -hex 32>
QUANTGPT_MCP_REMOTE_FETCH_STOCK_LIMIT=50
```

客户端请求需带：

```text
Authorization: Bearer <QUANTGPT_MCP_HTTP_TOKEN>
```

Claude Code 或 Claude Desktop 的配置文件位置：

| 客户端 | 配置位置 |
|:---|:---|
| Claude Code | 项目根目录 `.mcp.json` 或全局 MCP 配置 |
| Claude Desktop macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

MCP 重工具默认只读本地行情缓存。单股研究先用 `get_stock_history` / `check_market_cache`；全股票池工具只有显式
`allow_remote_fetch=true` 时才会尝试远程补数。若预计补齐股票数超过
`QUANTGPT_MCP_REMOTE_FETCH_STOCK_LIMIT`，工具会返回 `REMOTE_PREFETCH_REQUIRED` 和建议的预热命令。

长时间运行的因子工具建议使用异步模式：对 `run_backtest`、`score_factor`、`compute_factor_values`、
`run_anti_overfit`、`run_rolling_validation` 传入 `submit_only=true`，再用 `get_mcp_task_status(task_id)`
轮询进度；需要停止时调用 `cancel_mcp_task(task_id)`。取消是协作式的：工具会在批次边界、股票循环或
数据补齐步骤之间尽快停止，但已进入的 baostock/rqdatac/socket 调用仍需等待返回或超时。

## 6. 反向代理边界

最小 Nginx 示例：

```nginx
server {
    listen 443 ssl http2;
    server_name quantgpt.example.com;

    ssl_certificate /etc/letsencrypt/live/quantgpt.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/quantgpt.example.com/privkey.pem;

    client_max_body_size 20m;

    if ($host != quantgpt.example.com) { return 421; }

    location ~ ^/(mcp|mcp-sse)(/|$) {
        proxy_pass http://127.0.0.1:8003;
        proxy_http_version 1.1;
        proxy_set_header Host 127.0.0.1:8003;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8003;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

部署反向代理时同时确认：

- `AUTH_DISABLED=false`
- `QUANTGPT_CORS_ORIGINS=https://quantgpt.example.com`
- `QUANTGPT_ALLOWED_HOSTS` 包含普通 Web 请求的域名和 MCP 上游的 `127.0.0.1`。FastMCP 独立校验 Host，示例代理为 MCP 路径转发已获准的回环 Host。此配置面向不发送 `Origin` 的 MCP 客户端。浏览器跨源 MCP 还需要在 FastMCP 的传输配置中显式允许所需 Origin。
- `/mcp/` 和 `/mcp-sse/` 不对公网匿名开放
- 管理后台密码、JWT 密钥、WQ BRAIN 凭证不进入前端代码或截图
- 长任务需要较长 `proxy_read_timeout`，否则 SSE 或任务状态连接可能被中断

## 7. 更新、备份与回滚

Docker 更新：

```bash
git pull
docker compose up -d --build
docker compose logs -f quantgpt
```

裸机更新：

```bash
git pull --ff-only
make setup
make frontend
make dev
```

### 在线备份默认 SQLite 数据库

SQLite 使用 WAL。以下命令通过 SQLite 在线备份接口生成包含已提交事务的独立副本，在宿主机的专用目录保存。确认使用的是 Compose 默认 SQLite 路径。数据库文件来自容器，宿主机命令使用 Bash。

```bash
set -eu
umask 077
backup_dir=$(mktemp -d "$HOME/quantgpt-backup.XXXXXXXX")
docker compose exec -T quantgpt python - > "$backup_dir/database.sqlite.partial" <<'PY'
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    dest = Path(tmp) / "backup.db"
    with closing(sqlite3.connect("file:/app/data/quantgpt.db?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(dest)) as db:
            src.backup(db)
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("SQLite backup failed integrity_check")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("SQLite backup failed foreign_key_check")
    with dest.open("rb") as f:
        shutil.copyfileobj(f, sys.stdout.buffer)
PY
mv "$backup_dir/database.sqlite.partial" "$backup_dir/database.sqlite"
(cd "$backup_dir" && sha256sum database.sqlite > SHA256SUMS)
printf 'SQLite backup: %s\n' "$backup_dir/database.sqlite"
```

单独恢复这份副本时，把 `database.sqlite` 和 `SHA256SUMS` 复制到目标机器的同一目录，在相同版本的新 Compose 部署中执行以下命令。它只恢复数据库。对应的行情快照、外部证据和 HTML 报告需从同一时期的文件备份另行恢复。

```bash
set -eu
backup_dir=/absolute/path/to/quantgpt-backup
(cd "$backup_dir" && sha256sum -c SHA256SUMS)
test -z "$(docker compose ps -a -q quantgpt)"
docker compose create --build quantgpt
service_id=$(docker compose ps -a -q quantgpt)
test -n "$service_id"
docker run --rm --volumes-from "$service_id" \
  --mount "type=bind,source=$backup_dir,target=/backup,readonly" alpine sh -ec '
  test -d /app/data && test -z "$(ls -A /app/data)" || exit 1
  cp /backup/database.sqlite /app/data/quantgpt.db.partial
  chmod 600 /app/data/quantgpt.db.partial
  mv /app/data/quantgpt.db.partial /app/data/quantgpt.db
'
docker compose run --rm -T --no-deps quantgpt python - <<'PY'
import sqlite3
from contextlib import closing

with closing(sqlite3.connect("file:/app/data/quantgpt.db?mode=ro", uri=True)) as db:
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchone():
        raise SystemExit("Restored database failed integrity checks")
PY
docker compose start quantgpt
curl --fail --retry 10 --retry-connrefused http://127.0.0.1:8003/api/v1/health
```

### 完整备份数据卷与报告卷

需要一起恢复数据库、行情快照和报告时，先停止所有写入这些卷的进程。示例在停机前取得正在运行的服务容器 ID，用 `docker inspect` 核对 `/app/data`、`/app/reports` 的挂载。归档工具直接继承该容器的卷，可用于自定义 Compose 项目名。另有挂载的输入或证据目录时，应将其纳入同一次备份。

```bash
set -eu
umask 077
archive_dir=$(mktemp -d "$HOME/quantgpt-volumes.XXXXXXXX")
service_id=$(docker compose ps -q quantgpt)
test -n "$service_id"
docker inspect --format '{{range .Mounts}}{{println .Destination .Name .Source}}{{end}}' "$service_id"
docker compose stop quantgpt
trap 'docker compose start quantgpt' EXIT
docker run --rm --volumes-from "$service_id:ro" alpine \
  tar czf - -C /app data reports > "$archive_dir/volumes.tgz.partial"
gzip -t "$archive_dir/volumes.tgz.partial"
mv "$archive_dir/volumes.tgz.partial" "$archive_dir/volumes.tgz"
(cd "$archive_dir" && sha256sum volumes.tgz > SHA256SUMS)
docker compose start quantgpt
trap - EXIT
printf 'Volume backup: %s\n' "$archive_dir"
```

### 恢复到新机器的空卷

在新机器准备相同版本、Compose 配置和自己的 `.env`，将受信任的归档及校验文件复制过去。在项目根目录执行以下命令，设置 `archive_dir` 为该备份的绝对路径。新建服务容器时只创建挂载，恢复完成后再启动。示例在解包前检查两个目标目录均为空。

```bash
set -eu
archive_dir=/absolute/path/to/quantgpt-volumes-backup
(cd "$archive_dir" && sha256sum -c SHA256SUMS)
test -z "$(docker compose ps -a -q quantgpt)"
docker compose create --build quantgpt
service_id=$(docker compose ps -a -q quantgpt)
test -n "$service_id"
test "$(docker inspect --format '{{.State.Running}}' "$service_id")" = false
docker inspect --format '{{range .Mounts}}{{println .Destination .Name .Source}}{{end}}' "$service_id"
docker run --rm -i --volumes-from "$service_id" alpine sh -ec '
  test -d /app/data && test -d /app/reports || exit 1
  test -z "$(ls -A /app/data)" && test -z "$(ls -A /app/reports)" || exit 1
  tar xzf - -C /app
' < "$archive_dir/volumes.tgz"
docker compose start quantgpt
curl --fail --retry 10 --retry-connrefused http://127.0.0.1:8003/api/v1/health
```

登录后核对备份时的项目、任务及报告。使用 PostgreSQL 时需另用数据库原生备份工具，并将数据库与本次停写期间的文件归档配对。回滚代码前核对其能否读取当前数据库布局。结构回退条件见[研究数据库迁移与恢复](RESEARCH_MIGRATION.md)。

## 8. 验收清单

部署完成后确认：

- `curl http://localhost:8003/api/v1/health` 返回 `status=ok`。
- 浏览器可打开 `http://localhost:8003`。
- 生产 `.env` 中 `AUTH_DISABLED=false`。
- `JWT_SECRET_KEY` 和 `QUANTGPT_ADMIN_PASSWORD` 已从示例值替换。
- `QUANTGPT_CORS_ORIGINS` 只包含可信地址。
- Docker 部署下 `docker compose ps` 显示服务为 running。

可选 smoke 测试：

```bash
curl -s http://localhost:8003/api/v1/health
curl http://localhost:8003/
```

## 9. 常见问题

### 前端页面空白

裸机部署确认已构建前端：

```bash
cd frontend && npm ci && npm run build
```

Docker 部署会在镜像构建阶段自动构建前端。

### 启动时报认证密钥错误

生产模式下 `AUTH_DISABLED=false`，必须设置非示例值的 `JWT_SECRET_KEY` 和 `QUANTGPT_ADMIN_PASSWORD`。

### `.env` 修改后不生效

Docker 部署用更新后的配置重新创建容器。

```bash
docker compose up -d --force-recreate quantgpt
```

裸机部署停止旧服务后重新启动。

```bash
make dev
```

### Docker 中连接不上宿主机 PostgreSQL

Linux 可使用宿主机网关地址或数据库服务器内网地址。Docker Desktop 可尝试：

```bash
DATABASE_URL=postgresql+asyncpg://quantgpt:password@host.docker.internal:5432/quantgpt
```

### MCP 连接失败

确认 `cwd` 是绝对路径，并在项目目录手动运行：

```bash
.venv312/bin/python -m quantgpt --transport stdio
```

HTTP MCP 使用对应用户的有效 JWT/API key，或与服务端一致的部署令牌。经反向代理访问时检查 MCP 专用 Host 转发配置。
直接用浏览器或普通 `curl` 打开 `/mcp/` 可能返回 `406 Not Acceptable`；这不等于 MCP 不可用。用支持 streamable-http 的 MCP 客户端，并带上 `Accept: application/json, text/event-stream`。

### Windows 中文系统启动报 UnicodeDecodeError

将 `.env` 用编辑器另存为 UTF-8 编码。Windows 本地部署也可以优先使用 WSL2。
