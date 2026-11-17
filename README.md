# QuantGPT

QuantGPT 是供主代理和子代理共同使用的日频因子研究工具。它通过 MCP 和 HTTP 提供公式查重、因子评价、组合回测和样本外验证。新检出的仓库从空研究项目起步，真实行情、试验记录和策略报告由各部署自行保存。

**主代理和子代理共用一份操作指南**，其中写明各自负责的步骤和交接条件。[多代理研究操作指南](docs/agent_safe_workflow.md) · [可复制的 Agent 提示词](PROMPT.md)

## 本地启动

默认克隆的 `main` 已包含多代理研究、日频窗口规划和组合终验工具。

```sh
git clone https://github.com/popura404/QuantGPT_o.git
cd QuantGPT_o
```

项目使用 **CPython 3.12**。在仓库根目录安装后，编辑生成的 `.env`。默认启用认证，首次运行 HTTP 服务前设置 `JWT_SECRET_KEY` 和 `QUANTGPT_ADMIN_PASSWORD`。已有的 `.env` 会继续使用。

| 系统 | 安装 | 启动 HTTP 服务 |
| --- | --- | --- |
| Windows PowerShell | `./scripts/setup.ps1` | `./scripts/dev.ps1` |
| Linux | `make setup` | `make dev` |

默认监听 `127.0.0.1:8003`。用 `GET /api/v1/health` 检查后端。需要浏览器工作台时，先运行 `npm --prefix frontend ci` 和 `npm --prefix frontend run build`，再启动 HTTP 服务，打开 `http://127.0.0.1:8003/`。[快速开始](docs/QUICKSTART.md) · [部署说明](docs/DEPLOYMENT.md)

MCP 客户端可以直接启动 stdio 服务。工作目录设为仓库根目录，Windows 选择 `.venv312/Scripts/python.exe`，Linux 选择 `.venv312/bin/python`，参数为 `-m quantgpt --transport stdio`。连接后用 `tools/list` 查看当前工具。[MCP 配置指南](docs/MCP_GUIDE.md)

## 默认日频窗口

| 阶段 | 交易日数 |
| --- | --- |
| 指标预热 | 至少 30，声明更长时取声明值 |
| 开发与选择 | 756，约 3 年 |
| 开发与终验之间的隔离 | 取 5、标签周期和声明隔离期中的最大值 |
| 最终留出验证 | 252，约 1 年 |

按 30 日预热、5 日隔离计，共需 **1,043 个交易日**。历史不足时，规划工具返回 `insufficient_data` 和缺口。达到窗口要求后，策略仍需通过对应的统计和数据证据检查。已有独立研究设计可显式指定两个窗口的完整起止日期。[研究接口与窗口规则](docs/RESEARCH_API.md)

## 本地数据与版本管理

默认 SQLite 数据库为根目录的 `quantgpt.db`。行情快照和缓存写入 `data/`，报告写入 `reports/`。这些内容连同本地配置和凭据由 Git 忽略。仓库保留数据库迁移、研究接口、确定性测试夹具和 CI 基线。[迁移说明](docs/RESEARCH_MIGRATION.md) · [测试指南](docs/TESTING.md)

## 开发与许可

Python 后端位于 `quantgpt/`，Rust 引擎位于 `engine/`，React 工作台位于 `frontend/`。开发者可查阅 [系统架构](docs/ARCHITECTURE.md)、[策略规范](docs/STRATEGY_SPEC.md) 和 [贡献指南](CONTRIBUTING.md)。

项目采用 MIT 许可证。原作者及衍生作品署名见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
