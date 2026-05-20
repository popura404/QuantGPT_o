# 部署指南

将 QuantGPT 部署到你自己的服务器上运行。

---

## 1. 环境要求

| 项目 | 最低配置 | 推荐配置 |
|:-----|:---------|:---------|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 磁盘 | 20 GB | 50 GB（含行情缓存） |
| OS | Ubuntu 22.04 / Debian 12 / macOS | 同左 |
| Python | 3.10+ | 3.12 |
| Docker | 24.0+（Docker 部署方式） | 同左 |

---

## 2. 部署方式

### 方式一：Docker 部署（推荐）

```bash
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT

# 准备配置
cp .env.example .env
# 编辑 .env（见第 3 节）

# 构建并启动
docker compose up -d --build

# 查看日志
docker compose logs -f
```

服务启动后访问 `http://localhost:8003`。

### 方式二：裸机部署

```bash
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT

python3 -m venv venv
source venv/bin/activate
pip install -e .

# 构建前端
cd frontend && npm ci && npm run build && cd ..

cp .env.example .env
bash restart.sh
```

---

## 3. 配置说明

编辑 `.env` 文件：

### 3.1 LLM（可选）

```bash
# 不填则为纯表达式模式（手动输入因子表达式，无 AI 生成）
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

### 3.2 数据库

```bash
# 默认 SQLite，零配置，留空即可
# 如需 PostgreSQL：
# DATABASE_URL=postgresql+asyncpg://quantgpt:password@localhost:5433/quantgpt
```

### 3.3 认证

```bash
# 生产必须保持 false；只有本地单用户开发才可临时设为 true
AUTH_DISABLED=false

# 生产必须配置强随机 JWT 密钥和强管理密码
JWT_SECRET_KEY=（openssl rand -hex 32 生成）
QUANTGPT_ADMIN_PASSWORD=（使用密码管理器生成的长随机密码）

# 如需暴露 HTTP MCP (/mcp 或 /mcp-sse)，还必须配置独立 Bearer Token：
QUANTGPT_MCP_HTTP_TOKEN=（openssl rand -hex 32 生成）

# CORS 只配置本机、VPN 或可信内网前端地址；不要使用公网通配配置
QUANTGPT_CORS_ORIGINS=http://localhost:5173,http://localhost:8003

# 邮箱登录需要配置 SMTP：
# SMTP_HOST=smtp.example.com
# SMTP_PORT=465
# SMTP_USER=your_email
# SMTP_PASSWORD=your_password
# SMTP_FROM=your_email
# SMTP_USE_TLS=true
```

---

## 4. MCP 集成

部署完成后，可通过 MCP 协议连接 Claude Code / Claude Desktop：

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "quantgpt"],
      "cwd": "/你的项目路径/QuantGPT"
    }
  }
}
```

推荐使用上面的 stdio MCP，只暴露给本机进程。HTTP 服务也挂载了 `/mcp` 和 `/mcp-sse`，但生产环境不要直接公网暴露；如需反向代理访问，必须保持 `AUTH_DISABLED=false`，配置 `QUANTGPT_MCP_HTTP_TOKEN`，并让客户端发送 `Authorization: Bearer <QUANTGPT_MCP_HTTP_TOKEN>`。

### 公网暴露边界

QuantGPT 适合部署在本机、VPN 或可信内网中，不建议直接暴露在公网。它包含管理后台、任务执行、报告读取、HTTP MCP 和 WQ BRAIN 提交能力；公网部署会显著放大凭证泄露、任务滥用和账号误操作风险。确需跨机器访问时，建议使用 VPN、零信任隧道或带身份认证的反向代理，并保持 `AUTH_DISABLED=false`、限制 `QUANTGPT_CORS_ORIGINS`、配置强 `QUANTGPT_ADMIN_PASSWORD` 和独立 `QUANTGPT_MCP_HTTP_TOKEN`。

配置文件位置：
- Claude Code：项目根目录 `.mcp.json`
- Claude Desktop (Mac)：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Claude Desktop (Win)：`%APPDATA%\Claude\claude_desktop_config.json`

---

## 5. 数据预热（可选）

首次回测会自动下载行情数据，也可以提前预热：

```bash
python -m quantgpt --prefetch hs300 csi500
```

---

## 6. 常见问题

### Windows 中文系统启动报 UnicodeDecodeError

确保使用最新版代码（已修复）。如果仍有问题，将 `.env` 文件用记事本另存为 UTF-8 编码。

### Docker 中连接不上宿主机 PostgreSQL

使用 `host.docker.internal` 替代 `localhost`：

```bash
DATABASE_URL=postgresql+asyncpg://quantgpt:password@host.docker.internal:5433/quantgpt
```

### 前端页面空白

确认已构建前端：`cd frontend && npm ci && npm run build`。Docker 方式会自动构建。

### MCP 连接失败

1. 确认 `cwd` 是绝对路径
2. 在项目目录下手动运行 `python3 -m quantgpt` 检查是否有报错
3. 确认 `.env` 文件编码正确且无语法错误
