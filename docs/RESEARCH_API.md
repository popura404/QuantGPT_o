# 共享研究 API 与 MCP

所有新 HTTP 路径以 `/api/v1/research` 开头，需要现有登录令牌。项目 ID 选择上下文，不能替代认证。成员撤销、viewer 写入、跨项目产物和未注册快照均由服务端检查。MCP HTTP 支持现有部署 token 的服务端身份映射，以及 JWT/API key 解析 principal；stdio 使用服务端 `QUANTGPT_MCP_STDIO_USER_ID`，未配置时为专用系统用户。须由项目管理员显式邀请系统身份，不能共享所有用户的私人收藏。

| 方法/路径 | 输入与结果 |
|---|---|
| GET/POST `/projects` | 可访问项目列表/创建项目 |
| PUT `/projects/{id}/members` | 管理员设置成员角色或移除成员 |
| POST `/projects/{id}/offline-demo` | 固定种子合成行情、实际冻结 manifest、definition/config/template；明确 synthetic |
| POST `/projects/{id}/hypotheses` | definition、economic_hypothesis、可选 frozen_baseline；在该定义的任何评价登记前冻结 |
| POST `/projects/{id}/holdout` | start/end；在项目研究开始前登记不可变最终窗口 |
| POST `/projects/{id}/evaluations` | definitions/config；同批一次面板构建，摘要+artifact refs，相同哈希复用 |
| GET `/projects/{id}/evaluations/{evaluation_id}` | 紧凑结果与冻结配置 |
| GET `.../evaluations/{evaluation_id}/validation?profile=local_factor` | local_factor/local_strategy/wq_remote 的必需、可选、不适用证据及阻断 |
| GET `/projects/{id}/artifacts/{artifact_id}` | 授权后核验内容哈希，按需读取大数组或研究卡 |
| POST `/projects/{id}/strategy-runs` | strategy_spec/v2 + config；服务器核验组件引用、版本、数据、方向和窗口 |
| GET `/projects/{id}/strategy-runs/{run_id}` | 不可变策略记录 |
| POST `.../strategy-runs/{run_id}/export` | 无调用者证明；服务器验证后返回导出或具体阻断 |
| POST `/projects/{id}/portfolio-optimizations` | 保存的 signal_ref + 优化配置；约束残差、基线/成本诊断、产物引用 |
| POST `/projects/{id}/tasks` | request.kind=factor/strategy + 完整请求 + 可选 idempotency_key；202 持久接受 |
| POST `.../evaluations/{evaluation_id}/reconcile` | WQ 已知远端引用仅 GET 对账，未知结果不盲重 POST |
| POST `.../evaluations/{evaluation_id}/cancel` | 停止 WQ 本地等待；不冒充平台已取消 |
| GET `/capabilities` | 实际注册市场、WQ 能力及未验证范围 |

任务继续使用 `/api/v1/tasks/{task_id}` 查询、取消与恢复入口。持久 outbox 在接受前提交，worker claim/lease/attempt/revision 保护结果发布，失去权限或取消后不能用旧 worker 覆盖终态。WQ 远端未知、对账需要和本地停止等待单独显示。原始日志、行情、SQLite、快照、前端产物保留本地且由 `.gitignore` 排除。

MCP 新工具：`list_research_projects`、`evaluate_factors`、`run_research_strategy`、`get_research_artifact`、`optimize_research_portfolio`、`export_research_strategy`。评价/策略工具默认 `submit_only=true`，返回 task_id；读取评分与产物不会重跑回测。旧入口保留读取和研究行为，但旧 payload 不能靠填写 `promotion_ready` 或 validation booleans 获得合法导出。

本地引擎身份由实际 Python 版本、全部 `quantgpt/**/*.py` 内容、已安装数值依赖版本、操作系统/架构与当前平台依赖锁摘要计算，替换调用者的引擎声明。更换 NumPy/pandas 等依赖会改变结果身份；已接受的持久任务遇到计算身份变化会要求重新提交。表达式必须声明所有使用的字段契约。输入快照按项目登记并核验实际文件和逻辑内容；仅知道 manifest hash 不构成读取授权。合成数据和免费演示数据的 `research_only` 不会因设置布尔值而升级。

本轮本地完整验证 profile 仍有未实现/未获得的独立证据，合法导出默认受阻。PIT 风险暴露、完整真实公司行动/退市终值、正式历史证券库和充分独立验证没有被合成测试代替。
