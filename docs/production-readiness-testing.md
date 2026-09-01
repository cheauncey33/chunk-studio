# 上线前性能、可靠性与可观测性方案

这份方案把“效果好不好”和“服务扛不扛得住”分开测。所有结果都应记录代码版本、模型、数据集版本、配置、机器规格和时间；没有这些信息的单次耗时不能作为可比较基线。

## 当前已落地的测量能力

- `GET /api/health/live`：只验证进程存活，供容器重启策略使用。
- `GET /api/health/ready`：按当前部署配置验证 PostgreSQL/SQLite、Redis 和对象存储；任一关键依赖失败返回 503，供摘流使用。响应仅暴露错误类型和各依赖耗时，不泄露连接串。
- `GET /api/metrics`：输出 Prometheus 文本格式。它沿用应用认证，不应直接暴露到公网。
- 所有 HTTP 响应带 `X-Request-ID` 和 `Server-Timing`。HTTP 指标按模板路由聚合，避免把资源 ID 变成高基数标签。
- `/api/search` 响应增加 `timings_ms`，包含 `query_planning`、`query_embedding`、`dense_retrieval`、`lexical_retrieval`、`candidate_merge`、`rerank`、`evidence_enrichment` 和 `total`。
- Agent SSE 增加 `first_token`、`node_timing` 事件，最终响应增加 `performance.total_ms`、`performance.ttft_ms` 和各次 LLM/tool 节点耗时。
- `scripts/benchmark_agent_api.py` 从客户端测请求头、首事件、首 token、总耗时、吞吐、错误率和状态码分布，输出 p50/p95/p99 原始样本。
- `scripts/evaluate_multi_query_ablation.py` 在冻结测试集上比较 1、2、3 条 query 的召回、候选数、降级数和延迟。

注意：流式接口的通用 HTTP middleware 只能代表“到响应头”的时间；完整生成耗时看 `chunk_studio_agent_duration_seconds`，真正端到端 TTFT 和总耗时以压测客户端报告为准。

## 上线必须关注的数据

### 用户体验

- 端到端 TTFT：p50、p95、p99。
- 端到端总耗时：p50、p95、p99；按简单问答、检索问答、恢复检索、业务查询拆分。
- SSE 中途断开率、无 `final` 事件率、客户端取消率。
- 每轮 LLM、检索工具和各检索节点耗时；同时看平均值和长尾，不能只看平均值。

### 流量与可靠性

- QPS、并发中的请求数、2xx/4xx/5xx、429、超时率。
- 依赖错误率：模型 API、Embedding、Rerank、PostgreSQL、Redis、对象存储分别统计。
- 降级比例：`query_rewrite_failed`、`rewrite_embedding_failed`、`lexical_retrieval_failed`、`rerank_failed`。
- worker 队列深度、最老任务等待时间、处理耗时、重试次数、死信/永久失败数。
- 数据库连接池占用、慢查询、锁等待、事务回滚；Redis 延迟和内存；对象存储请求错误与耗时。

### RAG 效果与成本

- 严格完整召回率、evidence group recall、MRR、无证据率；按 retrieval class 分桶。
- 1/2/3-query 的质量增益、延迟增量、候选池膨胀和 rerank 成本。只有质量增益稳定大于资源成本时才保留多 query。
- Agent 平均轮数、搜索调用数、恢复检索触发率、工具失败率、超预算/超时 stop reason。
- 输入/输出 token、Embedding 条数、Rerank 文档数、单请求成本；按模型和业务场景拆分。
- 回答正确性还要做人工抽检：引用是否支持结论、是否遗漏条件、无证据时是否拒答、是否跨 workspace 泄露。

指标标签只能使用有限枚举，如路由模板、节点名、模型名、状态类别。不要把 query、用户 ID、conversation ID、file ID 放入 Prometheus 标签；它们进入带采样和脱敏的 trace/log。

## 测试矩阵

| 类别 | 怎么测 | 主要判定 |
|---|---|---|
| 单请求基线 | 固定 20–50 个问题串行跑，预热后统计 | 节点 p50/p95、TTFT、总耗时、效果指标 |
| 多 query 消融 | 同一冻结集跑 single/dual/triple | 质量差值、p95 差值、候选数和成本差值 |
| 阶梯负载 | 并发 1→2→4→8→16，每档 5–10 分钟 | 最大稳定吞吐、拐点、429 是否符合预期 |
| 突发流量 | 空闲后瞬时发起 2–5 倍稳定并发 | 不雪崩；限流生效；恢复后无租约泄漏 |
| 稳态耐久 | 目标并发持续 2–8 小时 | 错误率不爬升、内存/连接/线程不持续增长 |
| 故障注入 | 模型 429/5xx/慢响应，关闭 Redis/数据库/对象存储 | 超时有界、可解释降级、ready 摘流、恢复后可用 |
| 幂等与重连 | 重复 Idempotency-Key、SSE 断开后 Last-Event-ID | 不重复生成/写入，事件不丢不乱 |
| 数据隔离 | 两个 workspace 并发读写与越权请求 | 0 条跨租户结果，越权稳定为 401/403/404 |
| 发布回归 | 新旧版本各跑同一 workload | SLO 和效果不退化；数据库迁移可前滚/回滚 |

同一个开发用户默认 `AGENT_CONCURRENCY_LIMIT=2`，所以用更高并发压同一个 assistant 时，429 是 admission control 的预期结果，不等同于服务容量。测容量需要准备多个已授权测试用户/工作区，或者在隔离压测环境明确调高该限制；不能在生产临时关闭限流。

独立 worker 在领取任务或写回失败状态时遇到临时依赖故障，会记录错误并按固定退避继续轮询，不能因一次数据库超时退出。进程管理器仍应配置异常退出自动重启；退避日志和 worker 指标需要由 worker 自身的采集端点或集中日志系统收集。

## 建议的初始 SLO 与告警

以下数值是第一轮基线的候选门槛，不是假装已有数据的正式承诺。跑完代表性 workload 后再冻结：

- 可用性：非预期 5xx + 超时低于 1%；预期 429 单独统计。
- 流式完整率：收到 `final` 的请求不少于 99%。
- 延迟：先记录并冻结各场景 p95；发布门禁建议“不比基线恶化 20% 以上”。
- 检索效果：严格完整召回率和 evidence group recall 不低于冻结基线；不能用更快但效果明显变差的配置过门。
- 告警采用多窗口：5 分钟发现突发，30–60 分钟确认持续问题。高错误率、ready 失败和队列最老等待时间应分页；单次慢请求只进入 trace。

## 执行命令

先启动 API 和 worker，然后运行真实 SSE 压测：

```powershell
$env:PYTHONPATH='backend'
uv run python scripts/benchmark_agent_api.py `
  --assistant-id assistant_oil_transformer_audit `
  --questions-file evaluation/load_questions.txt `
  --requests 100 --concurrency 2 `
  --max-error-rate 0.01 `
  --max-ttft-p95-ms 5000 `
  --output evaluation/experiments/load/c2.json
```

多 query 消融：

```powershell
$env:PYTHONPATH='backend'
uv run python scripts/evaluate_multi_query_ablation.py `
  --modes single,dual,triple --top-k 10 `
  --output evaluation/experiments/multi_query_ablation.json
```

采集服务指标：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/metrics
Invoke-WebRequest http://127.0.0.1:8000/api/health/ready
```

提交报告时至少附：汇总 JSON、失败样本、Prometheus 截图或导出、机器与配置、git commit、测试集版本，以及对最慢 5 个请求的节点级分析。

## 一段可信的实习项目闭环

完整经历不应写成“做了压测”，而应形成闭环：先定义用户场景和 SLO；建立冻结效果集和性能 workload；补请求 ID、指标、节点 timing 和客户端测量；用消融说明多 query 的收益/成本；通过阶梯、突发、耐久和故障注入找到瓶颈；优化后用同一环境复测；最后把阈值接入发布门禁，并把告警、runbook 和复盘沉淀到仓库。面试时重点讲一到两个真实瓶颈的证据链，而不是罗列工具名。
