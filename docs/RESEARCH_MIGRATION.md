# 研究数据库迁移与恢复

当前 Alembic head 为 `018`。`017` 引入项目、评价及持久任务字段，`018` 增加研究轮次、方向和评价执行租约。原有收藏和报告保留旧标识，新评价与策略使用独立哈希。

启动会检查已有 Alembic 版本及研究表字段。带版本记录的数据库低于 `018`，或旧表缺少必需字段时，会返回 `DATABASE_MIGRATION_REQUIRED`。全新数据库可由服务创建完整布局。

## 升级已有数据库

1. 停止写入的 API、MCP 和 worker，备份数据库、不可变快照及报告。SQLite 使用在线 backup API，或在停写后归档包含 WAL 的完整数据目录。[备份与恢复](DEPLOYMENT.md)
2. 在项目根目录使用 CPython 3.12 环境，确认 `DATABASE_URL` 指向目标库。仓库提供的配置文件是 `alembic.deploy.ini`。先执行下列前两条命令，分别查看源码 head 和目标库当前版本。
3. 已有 Alembic 历史的数据库可以升级到 head。若旧库由 `create_all` 建立且没有版本记录，先核对表、列、索引、外键和唯一约束与哪个迁移版本一致。仅在布局确认后对该版本执行 `stamp`，再升级。例如，完整匹配 `016` 时使用 `stamp 016`。布局不明时在备份副本上排查。

```bash
.venv312/bin/python -m alembic -c alembic.deploy.ini heads
.venv312/bin/python -m alembic -c alembic.deploy.ini current
.venv312/bin/python -m alembic -c alembic.deploy.ini upgrade head
.venv312/bin/python -m alembic -c alembic.deploy.ini current
```

Windows 将解释器替换为 `./.venv312/Scripts/python.exe`。升级后确认输出 `018 (head)`，重启服务，核对项目、任务和报告。

## 个人收藏导入项目

持有项目写权限的用户先调用 `POST /api/v1/research/projects/{id}/favorites-migration?dry_run=true` 预览，核对后显式传 `?dry_run=false` 执行。缺省为预览。导入只复制当前用户的收藏，保留原行，重复运行幂等。

## 回退条件

- 从 `018` 退至 `017` 前，迁移会检查轮次、方向和执行租约三张表。任一表有记录就拒绝删除这些表。
- 从 `017` 退至 `016` 前，迁移会检查研究项目和带请求哈希、执行尝试或远端引用的持久任务；存在相关记录时拒绝结构回退。

需要回到旧版本时，先验证旧代码能否读取现有数据库。若必须恢复旧布局，在停写后使用升级前的备份和配套文件，并核对备份时间之后的业务记录。
