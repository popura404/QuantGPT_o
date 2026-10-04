# 研究数据库迁移与恢复

本轮使用扩展迁移 `017`。旧 `factor_hash`、收藏和原始报告不重新解释，新研究使用独立 definition/evaluation/strategy 哈希。现有 SQLite 文件、凭据和行情缓存没有纳入 Git；本轮迁移演练使用临时 SQLite，没有修改用户数据库。

启动会先检查旧表缺列。发现旧布局时返回 `DATABASE_MIGRATION_REQUIRED`，在修改任何表前停止，避免 `create_all` 留下半套新表。空数据库仍可正常创建完整布局。

升级步骤：

1. 停止 API、MCP 和后台 worker，备份数据库及不可变 snapshot 目录。SQLite 使用数据库 backup API 或停机后复制完整数据库；不要在写入时仅复制 `.db` 忽略 WAL。
2. 使用项目 Python 3.12 环境，设置 `DATABASE_URL`，检查当前 Alembic revision。带 Alembic 历史的数据库执行 `alembic upgrade head`。
3. 若数据库过去由 `create_all` 建立、没有 Alembic 版本，先核对其表/列与 `016` 布局一致，再 stamp `016` 后 upgrade。不能直接对未知旧库自动 stamp，也不能对已是新布局的库再次运行 `017`。
4. 重启服务，检查项目、旧收藏、任务和报告。个人收藏迁移通过 `POST /api/v1/research/projects/{id}/favorites-migration?dry_run=true` 预览；移除 dry-run 后只复制当前认证用户的收藏，原行保留，重复运行幂等。

本地 `alembic.ini` 可能包含数据库 URL，已忽略。需要从无配置环境运行时可使用 Alembic `Config()` 设置 `script_location=quantgpt/migrations` 并由 `DATABASE_URL` 提供连接配置。切勿将实际连接串写进提交或验收日志。

回退：迁移 `017` 仅在没有新项目或新持久任务写入时允许结构 downgrade；否则拒绝删除新字段。保留扩展数据库和产物，停机恢复到已备份的版本，或使用经验证的兼容代码读取旧记录。数据库结构回退不是丢弃新研究数据的快捷方式。

已执行：SQLite 从 `001` 到 `017`、无新写入 downgrade/upgrade、新写入后拒绝破坏性 downgrade。PostgreSQL 真实升级/回退尚未执行，不能把 SQLite 通过视为 PostgreSQL 通过。
