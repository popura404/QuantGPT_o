# QuantGPT 部署指南

本文给出一套可执行的部署路径：Docker 适合服务器或长期运行，裸机部署适合本地/内网调试。生产或多人共享环境必须开启认证。

## 0. 选择部署方式

| 场景 | 推荐方式 | 说明 |
|:---|:---|:---|
| 本机体验、Agent 调试 | 裸机 `make setup` + `bash restart.sh` | 自动生成本地开发 `.env`，默认关闭认证，只适合单用户本机 |
| 单台服务器、长期运行 | Docker Compose | 镜像内构建前端，数据落到 Docker volume，升级和回滚更可控 |
| 小团队内网/VPN | Docker Compose + PostgreSQL | 保持认证开启，CORS 只允许可信域名或内网地址 |
| 公网域名访问 | 不建议直接暴露 | 必须放在 VPN、零信任隧道或带认证的反向代理之后 |

服务启动后，一个 HTTP 进程同时提供：

- FastAPI REST API：`/api/v1/*`
- React 前端：由后端服务 `frontend/dist`
- 报告和图表静态资源：`/api/v1/reports/*`、`/charts/*`
- HTTP MCP：`/mcp/`、`/mcp-sse/`，生产环境需要独立 Bearer Token

## 1. 部署前检查

| 项目 | 最低要求 | 推荐 |
|:---|:---|:---|
| OS | Ubuntu 22.04 / Debian 12 / macOS / WSL2 | Ubuntu 22.04 LTS |
| CPU | 2 核 | 4 核以上 |
| 内存 | 4 GB | 8 GB以上 |
| 磁盘 | 20 GB | 50 GB以上，行情缓存会增长 |
| Python | 3.10+ | 3.11 或 3.12 |
| Node.js | 20+ | 20 LTS |
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

启动服务：

```bash
docker compose up -d --build
docker compose ps
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
bash restart.sh
```

`make setup` 会创建 `.venv`、安装开发依赖，并在本地没有 `.env` 时从 `.env.example` 生成 `.env`。为了便于本机单用户启动，它会把生成的 `.env` 中 `AUTH_DISABLED` 改为 `true`。

裸机生产或共享环境不要使用本地开发配置，改为：

```bash
cp .env.example .env
# 编辑 .env，保持 AUTH_DISABLED=false，并配置 JWT_SECRET_KEY / QUANTGPT_ADMIN_PASSWORD
make setup
bash restart.sh
```

如果已经误用 `make setup` 生成了开发 `.env`，先手动把 `AUTH_DISABLED=true` 改回 `AUTH_DISABLED=false`，再配置生产密钥。

常用命令：

```bash
make run              # 前台启动后端，默认 http://localhost:8003
make dev              # 显式使用 8003 端口
cd frontend && npm run dev
```

`bash restart.sh` 会构建前端并把服务放到后台，日志写入 `logs/server.log`。

### 裸机运行命令速查

```bash
tail -f logs/server.log                  # 查看后台服务日志
lsof -i :8003                            # 检查端口占用
.venv/bin/python -m quantgpt --prefetch hs300 csi500
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

当前服务启动时会通过 SQLAlchemy `create_all` 确保表存在；已有生产数据库升级时再使用 Alembic，并先备份数据库。

裸机执行迁移：

```bash
.venv/bin/alembic -c alembic.deploy.ini upgrade head
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

`quantgpt_engine` 安装成功时会自动启用 Rust 表达式计算；未安装时会回退到 Python 实现。Docker 镜像默认不强制安装 Rust 引擎，因此生产部署不应依赖它作为必需组件。

## 5. MCP 集成

优先使用 stdio MCP，只暴露给本机进程：

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "/absolute/path/to/QuantGPT_o/.venv/bin/python",
      "args": ["-m", "quantgpt"],
      "cwd": "/absolute/path/to/QuantGPT_o"
    }
  }
}
```

HTTP 服务也挂载 `/mcp/` 和 `/mcp-sse/`。生产环境不要直接公网暴露；如需反向代理访问，保持 `AUTH_DISABLED=false`，配置独立 token：

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

## 6. 反向代理边界

最小 Nginx 示例：

```nginx
server {
    listen 443 ssl http2;
    server_name quantgpt.example.com;

    ssl_certificate /etc/letsencrypt/live/quantgpt.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/quantgpt.example.com/privkey.pem;

    client_max_body_size 20m;

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
- `QUANTGPT_ALLOWED_HOSTS` 包含反向代理传入的 Host，例如 `quantgpt.example.com`
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
git pull
.venv/bin/pip install -e ".[dev]"
cd frontend && npm ci && npm run build && cd ..
bash restart.sh
```

SQLite 备份：

```bash
docker compose exec quantgpt python - <<'PY'
import shutil
from datetime import datetime
src = "/app/data/quantgpt.db"
dst = f"/app/data/quantgpt-{datetime.now():%Y%m%d-%H%M%S}.db.bak"
shutil.copy2(src, dst)
print(dst)
PY
```

Docker volume 导出：

```bash
docker run --rm \
  -v quantgpt_quantgpt-data:/data \
  -v "$PWD":/backup \
  alpine tar czf /backup/quantgpt-data.tgz -C /data .
```

Docker volume 恢复到新机器：

```bash
docker volume create quantgpt_quantgpt-data
docker run --rm \
  -v quantgpt_quantgpt-data:/data \
  -v "$PWD":/backup \
  alpine sh -c 'cd /data && tar xzf /backup/quantgpt-data.tgz'
```

回滚代码版本：

```bash
git log --oneline -5
git checkout <last-good-commit>
docker compose up -d --build
```

如果使用 PostgreSQL，升级前先用数据库原生工具备份；SQLite 备份只覆盖容器内默认 SQLite 场景。

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

Docker 部署需要重启容器：

```bash
docker compose up -d
docker compose restart quantgpt
```

裸机部署需要重新启动后台服务：

```bash
bash restart.sh
```

### Docker 中连接不上宿主机 PostgreSQL

Linux 可使用宿主机网关地址或数据库服务器内网地址。Docker Desktop 可尝试：

```bash
DATABASE_URL=postgresql+asyncpg://quantgpt:password@host.docker.internal:5432/quantgpt
```

### MCP 连接失败

确认 `cwd` 是绝对路径，并在项目目录手动运行：

```bash
.venv/bin/python -m quantgpt
```

HTTP MCP 还需确认 `QUANTGPT_MCP_HTTP_TOKEN` 与请求头一致。
直接用浏览器或普通 `curl` 打开 `/mcp/` 可能返回 `406 Not Acceptable`；这不等于 MCP 不可用。用支持 streamable-http 的 MCP 客户端，并带上 `Accept: application/json, text/event-stream`。

### Windows 中文系统启动报 UnicodeDecodeError

将 `.env` 用编辑器另存为 UTF-8 编码。Windows 本地部署也可以优先使用 WSL2。
