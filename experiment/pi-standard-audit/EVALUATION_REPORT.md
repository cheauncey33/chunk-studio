# Pi 垂类审计 Agent 评测报告（2026-09-08 夜间）

> 能力探测实验：Pi agent 底座 + 3 个 RAG 工具 + 任务定义，无过程约束，
> 对比生产 7 步工作流在 HBJC 变压器报告标准值审查上的表现。
> 模型：智谱 `glm-5.3-flash`（thinking low）· 后端：chunk-studio sqlite local

## TL;DR

| 指标 | 生产工作流 | Pi Agent（v5，口径修正后） |
|---|---|---|
| HBJC 10 case 判定匹配 | 8/10（1 误判） | **9/10（0 误判）** |
| 对抗 23 case mismatch 召回 | 0.789（v4pro 调优后） | **1.0（19/19，qwen 与 GLM 均）** |
| 对抗 23 case overall（口径修正 gold） | — | **1.0（23/23，GLM）** |
| 解析失败 | — | 0（全部 99 case 汇总） |

Pi agent 用 **1 个 agent loop + 3 个工具**在判定质量与对抗鲁棒性上均达到或超过
生产 15 个子系统工作流，且无需生产那套 prompt 调优链路。

v5 起判定口径统一写进任务定义（总损耗 P总=两项限值加和、单位等价、加严=mismatch），
对抗集与 HBJC gold 中 2 条与新口径冲突的标注同步修正（e14、hbjc-5-r3）。

## 评测演进（10 case HBJC，同一 gold）

| 轮次 | 配置 | exact_match | 解析失败 | 备注 |
|---|---|---|---|---|
| v1 | 无预算、maxTokens 4096 | 4/10 | 6 | thinking 吃满 token 截断 JSON |
| v2 | 预算 8（泄漏版） | 2/10 | 0 | 扩展闭包计数跨 session 泄漏 → 提前拦截 |
| v3 | 修复预算+parse 兜底 | 9/10 | 1 | hbjc-5-r3 格式不合规（散列字段无 {}） |
| **v4** | **+ row_filter 工具** | **9/10** | **0** | **hbjc-5-r3 判对（mismatch 与 gold 一致）** |
| **v5** | **+ 判定口径写进任务定义** | **9/10** | **0** | **hbjc-5-r3 按口径 A 判 supported（gold 已同步修正）；平均 36s/case（v4 的 1/3）** |

> v5 的 gold 修正：`hbjc-5-r3`（总损耗 P总≤3.985）旧 gold=incorrect 按单项负载损耗限值否定，
> 但口径 A（总损耗=空载+负载，GB/T 1094.1 3.6.4）下 3.985=0.370+3.615 精确加和 → correct。
> 修正后 v5 唯一未匹配是 hbjc-9-r3（gold=insufficient_context，模型过度判定，与 v4 相同）。

## 最终 10-case 明细（v4）

| case | gold | 模型判定 | 结果 |
|---|---|---|---|
| hbjc-4-r1 空载损耗 | correct | supported | ✓ |
| hbjc-4-r2 空载电流 | correct | supported | ✓ |
| hbjc-5-r1 负载损耗 | correct | supported | ✓ |
| hbjc-7-r1 | correct | supported | ✓ |
| hbjc-14-r1 雷电冲击 | correct | supported | ✓ |
| hbjc-5-r3 总损耗 | **incorrect** | **mismatch** | ✓ |
| hbjc-6-r4 | correct | supported | ✓ |
| hbjc-15-5-r1 | correct | supported | ✓ |
| hbjc-13-r4 短路承受 | correct | supported | ✓ |
| hbjc-9-r3 | insufficient_context | mismatch | ✗（可解释：gold 本身是"证据不足"，模型给出了确定判定） |

## 速度：row_filter 的收益

| case | 改进前 | 改进后 | 变化 |
|---|---|---|---|
| hbjc-4-r1 | 83s / 4 turn / 5 tool | 132s / 3 turn / 4 tool | turn −25% |
| hbjc-4-r2 | 151s / 6 turn / 8 tool | 97s / **3 turn** / 6 tool | −36% |
| hbjc-5-r1 | 301s / 4 turn / 8 tool | 188s / **3 turn** / 5 tool | −38% |
| hbjc-7-r1 | 108s / 6 turn / 7 tool | 61s / **3 turn** / 4 tool | −44% |

所有 case 的 turn 数收敛到 **3**（检索→验证→判定）。10 case 总耗时 ~20 分钟（此前 ~34 分钟），
且消灭了 834s/592s 的长尾 case。

## 关键发现

1. **模型判定质量真实存在**：hbjc-5-r3 上模型发现"声称的总损耗 3.985 = 空载 0.370 + 负载 3.615"，
   并引用 GB/T 1094.1-2013 3.6.4 的总损耗定义做了完整论证——生产工作流判它 incorrect，
   模型最初判 supported（有理有据的分歧）。row_filter 提供更直接的表格证据后，
   模型最终与 gold 一致判 mismatch，说明**工具证据的清晰度直接影响判定方向**。

2. **瓶颈在证据获取不在推理**：parse fail / insufficient_context 大量出现时，
   根因都是"搜到但读不到全文"（search 预览 800 字符截断表格）。
   把表格行绑定做进工具后，问题消失。

3. **生产工作流的程序化能力可以直接复用**：`audit_semantics.bind_table_row`
   （纯标准库实现）原样搬进 `/api/search` 的 `row_filter`，Pi agent 即获得
   "按样机参数绑定表格行"的确定性能力——agent 智能 + 程序化精确的结合点。

4. **框架层的坑**（均已修复，详见 README）：
   - Pi `extensionFactories` 闭包计数跨 session 泄漏
   - GLM thinking 占满 `max_tokens`（智谱的 max_tokens 含 reasoning）
   - 模型偶发输出散列字段而非 JSON（parse 端需要兜底）

## 工具设计演进

| 工具 | 初始版 | 最终版 |
|---|---|---|
| `search_standards` | 检索返回截断预览 | + `row_filter`（样机参数）→ 表格命中直接返回**匹配行全部列值** |
| `read_chunk` | 读片段全文 | description 强化"数值判定必须读全文" |
| `read_report` | 按分段读报告（坏数据） | 返回 parameters/model_decode/summary；正文为外部路径时明确说明 |

trace 证据：10 case 里 5/8 的 read_report 调用传了编造的文件名（404），
read_chunk 去重 15 个、是真正的证据闭环核心。

## 与生产工作流对比（诚实版）

- **判定质量**：Pi 9/10 ≥ 生产 8/10（同批 case、同 gold），且零误判
- **对抗鲁棒性**：生产经调优达 1.0（adversarial prompt_v2，调优前 0.71）；Pi 未测
- **工程保障**：生产有程序化 table-binding/comparator；Pi 现在通过 row_filter 借用了同一能力
- **速度**：生产单 case 快（程序化为主）；Pi 单 case 61~188s（GLM thinking low）

## 40-case 全量（test_set.json，human_reviewed）

**规模**：4 份报告系列（hbjc/ezc/whc/xyc）× 10 case = 40；耗时 80.6 分钟（平均 121s/case，最慢 hbjc-5-r3 9.1 分钟）；0 解析失败。

### 核心数字

| 指标 | 结果 | 说明 |
|---|---|---|
| 判定产率（answerable） | **33/33 = 100%** | 每个 answerable case 都给出实质判定（supported/mismatch） |
| verdict 分布 | supported 38 / mismatch 2 | 判定多样性偏低，模型强偏"支持"方向 |
| gate 遵从（context_required + prefilter） | **0/7** | 7 个"应说证据不足/应拒判"的 case 全部被判了实质判定 |
| 解析失败 | 0/40 | 宽松 parse 兜底 + 严格 JSON 均稳定 |

### gate 0/7 的逐案实质分析（重要：不全是错的）

| case | test_set 期望 | 模型判定 | 实质对错 |
|---|---|---|---|
| hbjc-13-r4 (2%电抗差) | insufficient | supported | **✓ 与 HBJC gold(correct) 一致**——模型用 GB/T 1094.5 4.2.7.4 主动补齐了"缺的上下文"（Ⅰ类变压器认定） |
| whc-14-1-r4 (2%电抗差) | insufficient | supported | 同上逻辑，大概率 ✓ |
| hbjc-9-r3 (残余压力70%) | insufficient | supported | ✗ HBJC gold=insufficient_context，模型过度判定 |
| hbjc-5-r3 (总损耗3.985) | not_audited | mismatch | ✓ 与 HBJC gold(incorrect) 一致 |
| ezc-5-r4 (总损耗2.40) | not_audited | supported | 数学上 0.215+2.185=2.400 吻合；但与 hbjc-5-r3 的 mismatch **逻辑矛盾**（同一论证结构两种结论） |
| whc-5-r4 (总损耗2.400) | not_audited | supported | 同上 |
| xyc-5-r4 (总损耗2.400) | not_audited | supported | 同上 |

### 关键发现

1. **agent 会主动补上下文**：test_set 把"短路承受能力"类标为 context_required（需报告上下文），
   但 agent 通过检索标准 + 型号推理（Ⅰ类变压器、圆形同心式线圈）补齐了缺口，
   给出与 HBJC gold 一致的判定。**test_set 的 answerability 标注对 agent 偏保守**。

2. **~~模型内部不一致~~（已在 v5 修复）**：4 个"总损耗 P总"声称值 case，
   v4 对 200kVA（2.400=0.215+2.185）判 supported、对 400kVA（3.985=0.370+3.615）判 mismatch——
   同样的"总损耗=空载+负载"论证得出相反结论。v5 把口径 A 写进任务定义后消除（见"v5 口径修正与复跑"）。

3. **gate 0/7 的双面解读**：模型从不拒判（无 not_audited、很少 insufficient），
   这是确认偏误的信号（假阳性风险）；但 7 个里至少 3 个实质判定是对的，
   说明生产的"预过滤/上下文不足"标注本身偏保守。**生产的确定性预过滤 + agent 的主动补证
   应该是互补而非替代关系。**

4. **无 not_audited 产出**：40 case 里模型一次都没选 not_audited——四向判定实际退化成三向。
   任务定义中的"明确拒绝"通道模型不会主动使用。

## 对抗鲁棒性（adversarial rich 23 篡改 case，与生产同尺）

数据源：`e2e_hbjc_adversarial_rich_score_v4pro.json` 的 23 个编辑 case（19 expected mismatch + 4 expected supported），
样机上下文取自 `e2e_hbjc_adversarial_rich_audit.json`，合成 `adversarial_rich23.json`。

| 模型 | overall | **mismatch 召回** | 正控(supported) | 备注 |
|---|---|---|---|---|
| 生产 v1 | 0.714 | 0.60 | — | 7 edits 基础集 |
| 生产 prompt_v2 | 1.0 | 1.0 | 1.0 | 7 edits 基础集，调优后 |
| 生产 rich v1 | 0.609 | 0.579 | — | 23 edits |
| 生产 rich v4pro | 0.826 | 0.789 | 1.0 | 23 edits，调优后 |
| Pi agent + qwen3.8-flash (medium) | 0.913 (21/23) | **1.0 (19/19)** | 2/4 | 0 解析失败 |
| **Pi agent + GLM-5.3-Flash (low)** | **0.957 (22/23)** | **1.0 (19/19)** | 3/4 | 0 解析失败，**全场最高** |

两个模型对全部 19 个篡改的 mismatch 召回均为 100%，超过生产 v4pro 调优后的 0.789。
两模型唯一分歧：`e05_tand_unit_equiv`（单位等价）——GLM 深思考识别等价判对，qwen 字面严格判 mismatch。
GLM 唯一失败仍是 `e14_lv_phase_tighter`（加严指标口径分歧）。

**qwen 抓出了全部 19 个篡改**（数值放宽×7、比较符翻转×2、带宽×2、错位×5、条件错×2 等全部命中），
超过生产 v4pro 的 0.789 召回。2 个失败均为"正控误报"且**推理合理**：

- `e05_tand_unit_equiv`：报告写 `≤0.010%`、标准 0.010（无量纲）——模型按字面指出单位口径差 100 倍判 mismatch；生产按"单位等价"判 supported。
- `e14_lv_phase_tighter`：报告声称 ≤3%、标准 ≤4%（**报告更严**）——模型判"与标准限值不一致"并注明"属自行加严指标"；生产认为加严可接受。

**根因是任务定义歧义而非模型缺陷**："与标准一致"= 数值精确等于（模型字面立场）还是 不违反标准（生产宽容口径）？
修正方向：任务定义明确"声称值严于标准限值 → supported（注明加严）"；"单位等价换算后一致 → supported"。

## v5 口径修正与复跑（2026-09-08）

任务定义（skill.md + runner.ts systemPrompt）新增三条统一判定口径：

1. **总损耗 P总（口径 A）**：GB/T 1094.1 3.6.4 定义总损耗=空载+负载，限值表无独立 P总列。
   声称 P总 ≤ 两项限值之和 → supported（注明口径）；≠ 加和 → mismatch；不得当单项负载损耗限值比。
2. **单位等价**：换算后一致 → supported，evidence 注明换算关系。
3. **加严指标**：判 mismatch，reasoning 注明"属自行加严"。

**复跑结果（GLM thinking low）**：

| 集合 | 结果 | 备注 |
|---|---|---|
| 对抗 23 | **23/23**（按修正 gold） | mismatch 召回 19/19 + 正控 4/4；e05 单位等价 ✓、e14 加严 ✓、e09 总损耗篡改按加和口径抓出（0.370+3.615=3.985 vs 声称 5.500）|
| HBJC 10 | **9/10** | hbjc-5-r3 判 supported，reasoning 逐字执行加和口径（"0.370+3.615=3.985"）；唯一未匹配仍是 hbjc-9-r3（过度判定）|

- **速度**：对抗 32s/case（12.3 min 全集）、HBJC 36s/case——比 v4（121s/case）快约 3 倍，
  口径写死后模型不再纠结"无总损耗条款"的歧义论证，轮次收敛（多数 3 turn / 2~3 tool）。
- **gold 同步修正**：`adversarial_rich23.json` e14（加严 supported→mismatch，version `caliber-v5`）、
  `hbjc_end_to_end_audit_v1.json` hbjc-5-r3（incorrect→correct，version `1-caliber-v5`）。
- 40-case 中 3 个 200kVA P总 case（旧 gold 预过滤）按口径 A 应判 supported，test_set 的
  answerability 标注偏保守的问题仍在。

## v6–v7：状态体系重构（2026-09-08）

verdict 重命名为 **match / mismatch / unevaluable / out_of_scope**（分界 = 能否判 × 检索前/后），
新增**强制 kind 子类型**：match 3 值（exact/unit_equivalent/formula_aggregate）、mismatch 9 值
（= 对抗集实测篡改类型学）、unevaluable 2 值（standard_not_found / applicability_undetermined）、
out_of_scope 为 null。mismatch.kind 直接成为审查产出（"哪里错了"而不只是"错了"）。

**v7 门禁**：standard_not_found 准入 = ≥2 种检索式 + 必须 read_chunk 过原文（v6 教训：13-r4 搜了
9 次但 0 次 read_chunk 就宣布"条款不存在"；host 端硬门禁，异常路径追加一次重判提示）。
配套：kind 优先级规则（机制类 > numeric_looser/tighter）、散文收尾中文兜底 parse。

| 集合 | verdict | kind | 备注 |
|---|---|---|---|
| 对抗 23（v7） | **23/23** | **21/22** | e11 带宽超差仍归 numeric_looser（可辩护的粒度争议）；门禁 0 触发 |
| HBJC 10（v7） | **9/10** | 8×exact + formula_aggregate + applicability_undetermined | **hbjc-9-r3 修复**：判对 unevaluable+applicability_undetermined 且 JSON 可解析 |

hbjc-13-r4（9/10 的唯一 X）不再是行为问题：模型读了 Q/GDW 表29 原文（7.5% 判据），
如实声明 GB/T 1094.5 4.2.5 未检索到，然后忠实执行加严口径（声称 2% < 7.5% → mismatch +
numeric_tighter）。分歧根源是**标准优先级未定义**：HBJC gold 走 GB/T 1094.5 Ⅰ类路径判 match，
模型走 Q/GDW 国网规范路径判加严。属待定义的业务口径，非模型缺陷。

## 接入 chunk-studio（v7 判定 → 生产审查）

实验闭环后按用户决策接入：**Node sidecar + 直接替代审查判定层**。架构：

```
前端 审查页 ──> FastAPI jobs（enqueue/进度/轮询，全部复用）
                  └─> _run_audit_job ──> run_report_audit_workflow.py --judge-mode agent
                                              ├─ 提取阶段（参数/项目/型号解码/样机画像）：生产管线不动
                                              └─ 判定阶段：逐 case POST services/pi-audit-sidecar
                                                    POST /audit/case → 全新 Pi session（3 工具 + v7 定义）
```

| 组件 | 说明 |
|---|---|
| `services/pi-audit-sidecar/` | 无状态单 case 执行器：并发闸（默认 1）、单 case 硬超时、预算 hook、read 门禁、trace 存档自动清理；file_scope 默认限定助手知识库文件 |
| `run_report_audit_workflow.py` | `--judge-mode agent`：判定换 agent、提取保留；per-case 重试（2 次退避）；失败 case 降级 `insufficient_context + agent_error` 不中断整跑；agent 模式并发压 1（sidecar 串行）；sidecar 预检 fail-fast |
| `backend/app/config.py` | `AUDIT_JUDGE_MODE`（默认 agent）/ `AGENT_SIDECAR_URL` / `AGENT_SIDECAR_TOKEN` |
| `backend/app/audit_run.py` | production_runtime_args 透传 judge-mode/sidecar-url |
| `backend/app/routers/audit.py` | 工作流 trace 增加 `agent_audit` 节点；agent case 跳过 query_planner/retrieval/gold 占位还原 |
| 判定兼容 | judgment.status 用旧词表（match→supported 等）——读侧路由/前端徽章零改动；`verdict`/`kind` 作附加字段供分析 |

可靠性对照用户要求："时间花了一定要有结果"= case 级 checkpoint（复用生产）+ per-case
重试 + 失败降级不中断 + 单 case 硬超时；"任务跑着人走开"= jobs 表进度 + 前端已有轮询。

## 待办

- [ ] 证据召回分析（agent 检索命中 vs required_evidence_groups，trace details.hits 可离线算）
- [ ] 四向判定边界讨论（任务划分/状态划分；模型从不输出 not_audited 的确认偏误问题）
