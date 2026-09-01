# Chunk Studio 工程测试报告（2026-08-31）

## 环境

- API：单 Uvicorn 进程，`127.0.0.1:8000`
- 数据库：PostgreSQL 16 + pgvector
- 协调服务：Redis 7
- 对象存储：MinIO（Windows 保留端口冲突，本次映射为 59100/59101）
- Agent 模型：`deepseek-v4-flash`
- 测试问题：`evaluation/load_questions.txt` 中 5 条工程问题

## 真实 Agent 延迟

| 场景 | 请求 | 成功 | TTFT p50/p95 | 总耗时 p50/p95 | 吞吐 |
|---|---:|---:|---:|---:|---:|
| 冒烟，并发 1 | 1 | 1 | 1.62s / 1.62s | 11.29s / 11.29s | 0.086 RPS |
| 串行基线，并发 1 | 5 | 5 | 1.09s / 6.79s | 10.52s / 11.58s | 0.097 RPS |
| 稳态，并发 2 | 6 | 6 | 1.22s / 5.79s | 11.13s / 13.70s | 0.168 RPS |
| 限流，并发 4 | 8 | 2（其余 429） | 0.84s / 0.86s（仅成功项） | 11.31s / 11.42s（仅成功项） | 并发上限 2 生效 |

并发 2 相比串行吞吐提高约 73%，但成功请求总耗时 p95 增加约 18%。TTFT 存在明显长尾：多数约 0.8–1.7 秒，两个样本达到 5.79–6.79 秒。

当前 API 进程内 8 个成功并发测试请求的节点均值：

- Agent 总耗时：11.07 秒/请求。
- TTFT：1.36 秒/请求。
- 两次 LLM：合计 5.43 秒/请求，约 2.72 秒/次。
- 检索工具：5.58 秒/请求。
- 检索内部：lexical 3.65 秒、query planning 1.03 秒、embedding 0.38 秒、rerank 0.41 秒、dense 0.10 秒。

最优先性能优化对象是 lexical retrieval，其次是 query planning 和 TTFT 长尾。

## 多 query 消融（5 例方向性结果）

| 模式 | 严格完整召回 | Evidence group recall | 平均耗时 | p95 | 平均候选数 |
|---|---:|---:|---:|---:|---:|
| single | 0.000 | 0.167 | 3.09s | 4.07s | 65.8 |
| dual | 0.000 | 0.167 | 5.46s | 5.76s | 67.4 |
| triple | 0.000 | 0.167 | 5.52s | 5.77s | 67.6 |

在这 5 例上，多 query 没有带来可观测质量增益，平均延迟却增加约 76%–78%，候选池只增加约 2 个。样本过小，不能直接决定下线；完整 33 例需要单独的数据外发授权。

## HTTP 与故障注入

- Liveness：500 请求、并发 20、零错误、201.75 RPS、p95 73.10 ms。
- 全依赖 readiness：100 请求、并发 5、零错误、37.80 RPS、p95 229.19 ms。
- PostgreSQL、Redis、MinIO 分别停机时：liveness 保持 200，readiness 正确变为 503；依赖恢复后回到 200。
- 最终全依赖状态：healthy。

## 测试发现并修复的问题

1. Readiness 错误检查兼容 SQLite，而不是生产 PostgreSQL；现按配置检查数据库、Redis、对象存储。
2. PostgreSQL 短暂故障导致 worker 进程退出；现领取任务和失败写回均退避重试，恢复后继续轮询。
3. Redis conversation lock 在请求线程获取、后台线程释放时因 thread-local token 抛异常，导致 SSE 永久挂起；现显式支持跨线程释放，且 cleanup 失败也一定结束响应流。
4. 所有新会话共用 `new` 锁，导致并发请求错误返回 409；现仅已有同一 conversation 串行，新会话可以独立并发。

## 验证

- Python：426 passed。
- 前端 lint/build：通过（存在项目原有 warning）。
- API、worker、PostgreSQL、Redis、MinIO 在报告完成时仍保持运行。
