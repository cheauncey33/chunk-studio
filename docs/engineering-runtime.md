# 工程化运行时

## 本地基础设施

启动 PostgreSQL/pgvector、Redis 和 MinIO：

```powershell
docker compose -f docker-compose.engineering.yml up -d
```

默认端口是 PostgreSQL `55432`、Redis `56379`、MinIO API `59000`、MinIO Console `59001`。默认密码只用于本地开发，部署时必须通过环境变量覆盖。

初始化会话、任务和 pgvector 表：

```powershell
$env:PYTHONPATH = 'backend'
$env:CHUNK_STUDIO_DATABASE_URL = 'postgresql://chunkstudio:chunkstudio_dev_only@127.0.0.1:55432/chunkstudio'
uv run python scripts/migrate_postgres_schema.py --dsn $env:CHUNK_STUDIO_DATABASE_URL --apply
```

迁移 SQLite 中的身份、会话、事件和任务：

```powershell
uv run python scripts/migrate_sessions_jobs_to_postgres.py `
  --sqlite-path backend/data/chunkstudio.db `
  --dsn $env:CHUNK_STUDIO_DATABASE_URL `
  --apply
```

迁移前先执行 dry-run。任务迁移使用 `ON CONFLICT DO NOTHING`，不会覆盖已经被 PostgreSQL Worker 领取的任务。

## 分阶段切换边界

当前按以下阶段推进，阶段之间不自动切流量：

1. **阶段 1：数据基础**。迁移身份、会话、任务、文件、Chunk、知识库、助手和解析记录；Embedding 单独回填到 pgvector。SQLite 仍是业务读写源。
2. **阶段 2：Repository 垂直切片**。先切知识库/文件/Chunk 的读路径，运行 SQLite 与 PostgreSQL 对照，再切写路径。
3. **阶段 3：解析产物对象存储**。将 Markdown、layout ZIP、裁剪图迁移为 MinIO/S3 object key，数据库只保留 key、哈希和元数据。
4. **阶段 4：Worker 与队列**。解析、切块、Embedding、审查任务使用 PostgreSQL 队列，API 多实例关闭进程内 Worker。
5. **阶段 5：默认切换**。完成回归、尾部任务检查和回滚演练后，才将 PostgreSQL/pgvector 设为默认。

阶段 1 的业务内容迁移命令：

```powershell
uv run python scripts/migrate_business_content_to_postgres.py `
  --sqlite-path backend/data/chunkstudio.db `
  --dsn $env:CHUNK_STUDIO_DATABASE_URL `
  --apply
```

该命令默认 dry-run，重复执行时以 SQLite 为源同步同一批内容；不会迁移 Embedding，也不会删除 SQLite 数据。

## 服务与 Worker 分离

API 多实例部署时关闭进程内 Worker：

```powershell
$env:CHUNK_STUDIO_RUN_IN_PROCESS_WORKER = '0'
```

然后按任务类型启动独立 Worker：

```powershell
uv run python scripts/run_worker.py --types ocr,parse,chunk
uv run python scripts/run_worker.py --types embed,audit,assistant_init
```

PostgreSQL Worker 通过 `FOR UPDATE SKIP LOCKED` 领取任务；SQLite 仍然是本地开发兼容路径。

## 切换前验收顺序

1. 初始化 PostgreSQL/pgvector 和 Redis/MinIO。
2. 对 SQLite 数据执行迁移脚本的 dry-run。
3. 回填 Embedding，并用 `scripts/compare_vector_backends.py` 比较同一批 query 的候选数和 Top-K 重叠率。
4. 通过真实 Redis smoke test，确认会话锁、幂等键、Stream 恢复和限流。
5. 确认迁移数据、检索结果和任务尾部一致后，再设置 `DATABASE_BACKEND=postgres`、`VECTOR_BACKEND=pgvector` 和对象存储配置。
