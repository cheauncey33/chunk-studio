# Chunk Studio

变压器检测报告的标准符合性审查平台。审查员上传报告、逐项拿到判定和证据；知识库维护者管标准语料、切分规则和判定约定；平台侧管住助手与字段口径、盯住用量与检索质量。

![检测报告结果查看](https://raw.githubusercontent.com/cheauncey33/chunk-studio-showcase/main/01-report-viewer.png)

## 审查

- **提交报告**：一次选中多份检测报告，指定审查助手，立即跑或预约夜间批次；工作台右侧留着历史与批次。
- **逐项判定**：按报告里的检测项逐条对照标准，给出 `supported`（有证据支持）/ `mismatch`（与标准不一致）/ `insufficient_context`（证据不足）/ `not_audited` 四态，并写明判定理由。
- **证据落到条款和页**：每条结论挂到「标准号 + 类型 + 条款或表号 + 页码」，用稳定 locator 而不是切片 UUID——所以重建切片之后，历史结论的引用不会失效。
- **不信模型的自我保证**：判定完还要过一遍程序校验，结论必须带证据键，数值要按比较符、单位、松紧方向核对，缺证据的结论直接降级。判定口径既能固定成程序规则，也能交给判定 agent 逐条推理。
- **答不上来就说答不上来**：检索不到就落到 `insufficient_context`，不猜一个看起来合理的数。
- **看得见成本**：每条结论和整批审查的 token 成本、耗时都有账。

![切片管理](https://raw.githubusercontent.com/cheauncey33/chunk-studio-showcase/main/05-chunk-studio.png)

## 夜间批次

- **排期执行**：给一批报告设定开始时间，到点由 worker 拉起；每份报告仍是独立 job，一份失败不拖垮整批。
- **并发有上限**：报告级并发默认 3、单批上限 200 份，并留出全局槽位给交互式审查——不让批量任务把人工操作挤掉。
- **断点续跑**：以原子 checkpoint 记录进度，重试只跑没完成的 case，不整批重来。
- **状态可推导**：批次状态由子任务反推，不会出现「批次显示完成、里面有报告还在跑」。

## 知识库

- **标准语料**：上传标准与规范 PDF，由 MinerU 做全文档解析，按章节、表格、图片自动切成切片。
- **切片管理**：左边标准原文、右边切片列表，可框选新建、编辑、补 OCR，人工确认之后才进入检索。
- **切分规则与判定约定**：每个库独立配置切分规则和判定口径，换一个标准体系不必改代码。
- **元数据字段**：字段模板定义每个切片带哪些元数据，小模型先给建议、人工采纳。
- **检索测试**：写一个问题，看混合检索（向量 + 全文 + RRF 融合）和重排之后的召回结果与命中位置，验证这个库到底查不查得到。

## 智能问答

- **文档问答**：对着指定知识库提问，答案带引用；检索范围被限制在该助手授权的库内。
- **业务数据问答**：另有接口可用自然语言查系统里的业务与审查数据（Text2SQL，只允许单条只读查询，禁 DDL/DML/PRAGMA，结果限行数）。
- **助手机制**：问答与审查各是一套助手，配置提示词、检索参数和可用工具，改完存版本快照，随报告一起留档。

## 平台

- **助手与提示词**：审查助手配置判定策略、检索配置、工具集，每次运行把当时的版本写进报告，事后能解释「当时为什么这么判」。
- **字段模板**：统一元数据口径，避免各库各写一套。
- **系统设置**：模型与密钥、并发与排期上限、部署 profile 都在这里。
- **健康与用量**：请求量、阶段耗时、token 计费一类的运行指标以 Prometheus 文本格式暴露在 `/api/metrics`，就绪探针在 `/api/health/ready`；指标是进程内自己实现的，不额外拖一个监控中间件进来。

## 快速开始

后端与 worker（PowerShell，仓库根目录）：

```powershell
uv sync
docker compose -f docker-compose.engineering.yml up -d          # PostgreSQL/pgvector + Redis + MinIO
$env:PYTHONPATH='backend'
uv run python scripts/migrate_postgres_schema.py --apply
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
uv run python scripts/run_worker.py --types ocr,parse,chunk,embed,audit,assistant_init
```

前端：

```powershell
cd frontend
npm install
npm run dev        # npm run lint / npm run build
```

不想起 Docker 时，设 `CHUNK_STUDIO_DEPLOYMENT_PROFILE=local` 走零依赖的 SQLite 路径；测试会自动用这个 profile。

判定 agent 是一个独立的 Node sidecar：

```powershell
cd services/pi-audit-sidecar
npm install
npm start          # 监听 8787，无状态 POST /audit/case
```

测试与质量检查：

```powershell
uv run --with pytest pytest -q
$env:PYTHONPATH='backend'; uv run python scripts/audit_chunk_quality.py
$env:PYTHONPATH='backend'; uv run python scripts/validate_test_set.py
```

## 技术组成

- **前端**：React 19 + TypeScript + Vite，路由与页面在 `frontend/src/`。
- **后端**：FastAPI + Python 3.12，路由在 `backend/app/routers/`，解析、检索、判定、仓储各自独立成模块。
- **数据**：默认 PostgreSQL + pgvector，配 Redis 与 MinIO；`local` profile 退化成 SQLite，一个文件跑起来。
- **队列**：任务落 jobs 表，由 `scripts/run_worker.py` 消费（PostgreSQL 下用 `FOR UPDATE SKIP LOCKED` 抢占），不引入额外消息中间件。
- **模型**：LLM 走 OpenAI 兼容的 JSON 调用，向量与重排分别用独立的 embedding / rerank 服务，解析质量交给 MinerU。
- **评估**：`evaluation/` 保留一套 40 例的检索测试集和一份冻结基线，只做相对对比；这些数字不代表经领域确认的生产准确率，目录里也写明了这一点。

截图素材备份在独立仓库 [chunk-studio-showcase](https://github.com/cheauncey33/chunk-studio-showcase)，本仓库不重复存放。
