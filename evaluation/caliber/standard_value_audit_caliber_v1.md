# 检测报告标准值审查口径规范 v1

- `caliber_version`: `1`
- 状态: `active`
- 机器可读副本: `evaluation/caliber/caliber_v1.json`

## 0. 本文档的权威性

本文档是标准值审查判定口径的**唯一权威来源**。

在本文档之前，判定口径分散在五处且互相冲突：`experiment/pi-standard-audit/skill.md`、
`services/pi-audit-sidecar/src/prompt.ts`、`evaluation/prompts/standard_value_audit_judge_v1.md`、
`backend/schemas/standard_value_audit_result.schema.json`、以及
`data/evaluation_private/transformer_reports_v2/manifest.json` 的缺陷集标注。
今后这五处的判定语义必须从本文档派生；出现分歧时以本文档为准。

三条硬约束：

1. **gold 由规范推导，不由逐条意见填写。** 每条 case 的 `expected_status` 必须是
   `(报告声称值, 标准事实, 适用条件)` 经本文档规则计算的结果，并附 `derivation` 记录用到的规则号。
2. **口径变更 = 新版本号 + 全量重新推导 + 记录 diff。** 禁止为了让某次运行结果"看起来对"
   而回改单条 gold。历史上 `adversarial_rich23.json` 的 e14 与 `hbjc_end_to_end_audit_v1.json`
   的 hbjc-5-r3 就是这样被回改的（标记为 `caliber-v5`），导致分数与口径耦合、跨版本不可比。
3. **口径规则必须可执行。** 每条规则的触发条件用确定性比较器的输出表达
   （`audit_semantics.compare_claims` 的 `relation` / `tightness`、`normalize_unit_pair` 的单位状态），
   而不是自然语言描述。不可执行的规则只能作为"未决项"列在第 6 节，不得进入判定链路。

## 1. 状态模型

### 1.1 verdict（四态）

分界依据是「能否判定 × 判定发生在检索前还是检索后」。

| verdict | 含义 | 检索 |
|---|---|---|
| `match` | 声称值不违反适用标准要求 | 检索后 |
| `mismatch` | 声称值与适用标准要求冲突 | 检索后 |
| `unevaluable` | 该审，但证据不足以下结论 | 检索后 |
| `out_of_scope` | 声称值不构成限值声称，无需检索即可判 | 检索前 |

`match` 的定义是**不违反**，不是**数值精确等于**。这是 v1 相对 v7 口径的实质性改动，见规则 C-01。

### 1.2 kind（必填，`out_of_scope` 时为 `null`）

| verdict | kind | 说明 |
|---|---|---|
| `match` | `exact` | 数值、比较符、条件精确一致（含仅显示精度差异） |
| `match` | `unit_equivalent` | 单位换算后一致 |
| `match` | `formula_aggregate` | 派生公式一致（如总损耗 = 两项限值加和） |
| `match` | `within_standard` | 声称值严于标准限值，不违反（配 flag `tighter_than_standard`） |
| `mismatch` | `numeric_looser` | 限值放宽 |
| `mismatch` | `comparator_flip` | 比较方向反转 |
| `mismatch` | `bandwidth_exceeded` | 容差带宽超出标准允许区间 |
| `mismatch` | `wrong_level` | 电压等级 / 能效等级写错 |
| `mismatch` | `wrong_condition` | 试验条件 / 次数 / 时长写错 |
| `mismatch` | `wrong_label` | 标号 / 联结组写错 |
| `mismatch` | `magnitude_error` | 数量级错误 |
| `mismatch` | `formula_aggregate` | 派生公式与限值加和不符 |
| `unevaluable` | `standard_not_found` | 检索不到适用标准 / 条款 |
| `unevaluable` | `applicability_undetermined` | 缺决定适用性的参数 / 上下文 |
| `unevaluable` | `basis_conflict` | 多个适用标准给出冲突要求，且优先级规则无法裁定 |

相对 v7 的变动：

- **删除 `mismatch.numeric_tighter`**。加严改判 `match` + `within_standard`，见 C-01。
- **新增 `match.within_standard`**（承接加严）。
- **新增 `unevaluable.basis_conflict`**（承接标准优先级无法裁定，见 C-06）。

### 1.3 kind 优先级

同一问题符合多个 `mismatch` kind 时，取最能刻画错误机制的：

```
comparator_flip > wrong_level > wrong_condition > wrong_label
                > magnitude_error > bandwidth_exceeded > numeric_looser
```

`numeric_looser` 只在问题纯粹是数值宽严时使用。

### 1.4 附加标记（flags，与 verdict 正交）

flags 不改变 verdict，只承载需要提示但不构成不合规的信息。

| flag | 触发 | 用途 |
|---|---|---|
| `tighter_than_standard` | 声称值严于标准限值 | 提示自行加严，供人工关注，不计入缺陷 |
| `basis_edition_mismatch` | 报告声明的标准版次与知识库现行版次不一致 | 独立的审查发现，见 C-07 |
| `basis_incomplete` | 报告检测依据中的标准未全部命中知识库 | 见 C-07 |

## 2. 口径规则

每条规则给出：判定、触发条件（可执行）、理由、影响面。
执行体是 `backend/app/audit_caliber.py`，规则号与该模块的判定分支一一对应。

### C-00 关系决定 verdict（基础规则）

**触发**：`evidence_state == found` 且确定性比较器给出了 `relation`。

**判定**：`relation ∈ {equal, supports}` → `match`；`relation ∈ {conflicts, different}` → `mismatch`。
`match` 的 kind 默认 `exact`，由 C-01 / C-02 / C-03 / C-08 / C-11 细化；
`mismatch` 的 kind 按 1.3 优先级选取。

**理由**：v1 初稿只写了例外情形，没有写「相等即 match」这个平凡情形，
导致 derive 在最常见的路径上输出空 `rules`，而空 `rules` 按 3.1 的约定是无效判定。

### C-01 加严指标 → `match` + `within_standard` + flag `tighter_than_standard`

**触发**：单位归一后比较方向一致，且 `tightness == "stricter"`（上限类报告数值 < 标准数值；下限类报告数值 > 标准数值）。

**判定**：`verdict=match`，`kind=within_standard`，`flags=["tighter_than_standard"]`。

**理由**：标准值审查要回答的问题是「报告声称的判定要求是否**违反**适用标准」。
报告写 ≤3% 而标准要求 ≤4%，报告更严，按该报告判定的产品必然也满足标准，不构成不合规。
把它判 `mismatch` 有两个具体代价：

1. 真实报告中保守填写限值很普遍，判 mismatch 会产生大量误报，直接抬高人工复核成本——
   而复核成本正是这套系统的价值瓶颈。
2. 它会污染 `mismatch` 这个信号。`mismatch` 的业务含义应当是「这份报告的判定依据写错了，需要纠正」，
   加严不属于此类。

加严仍然需要被看见，所以用 flag 而不是丢弃。

**影响面**：

- 撤销 `skill.md` / `sidecar prompt.ts` 的第 6 条（"加严 → mismatch + numeric_tighter"）。
- 撤销 `adversarial_rich23.json` 中 e14 的 `caliber-v5` 改动（应回到 `supported`）。
- 与 `transformer_reports_v2/manifest.json` 的 5 条 `tighter` 编辑（标注 `supported`，
  note "仍属于不宽于标准"）一致，这 5 条不需要改动。
- HBJC 的 hbjc-13-r4（声称 2% vs Q/GDW 表 29 的 7.5%）在本规则下判 `match`；
  其与 gold 的分歧转由 C-06 处理。

### C-02 单位等价 → `match` + `unit_equivalent`

**触发**：`normalize_unit_pair` 返回 `converted`，换算后数值与比较方向一致。

**判定**：`verdict=match`，`kind=unit_equivalent`，evidence 须注明换算关系。

**理由**：单位表述差异不是内容差异。`0.010` 与 `0.010%`、`10 kV` 与 `10000 V`、
`0.215 kW` 与 `215 W` 指同一要求。仅当换算后仍不一致才进入 `mismatch` 判定。

**影响面**：与 v7 一致，保留。覆盖对抗集 e05 与缺陷集 `transformer-control-01-unit-equivalence`。

### C-03 总损耗派生 → `formula_aggregate`

**触发**：声称值属性匹配「总损耗 / P总 / total loss」，且标准限值表中不存在独立总损耗列。

**判定**：按声称值扮演的角色分两种，不可混用。

- **限值声称**（声称值带 `≤ / ≥ / < / >`）：两项限值之和就是标准上限，
  按普通界限语义比较，因此声称值严于加和时按 C-01 判 `match` + `within_standard`。
- **实测加和核验**（声称值为等式值）：要求严格相等，任一方向的差异都是
  `mismatch` + `formula_aggregate`。

**禁止**：把总损耗声称值当作单项负载损耗限值去比较。

**v1 初稿修订**：初稿写「≠ 加和（无论偏大偏小）→ mismatch」，把上述两种角色混为一体。
实测中报告写 `总损耗P总（kW）：≤2.400` 而标准加和为 `3.205 kW`，属于自行加严，
按初稿会被判成缺陷。

**理由**：GB/T 1094.1 第 3.6.4 条定义总损耗 = 空载损耗 + 负载损耗，但限值表按分项给出，
无独立总损耗行。派生关系由 `evaluation/manual_knowledge_rules_v1.json` 的
`transformer_total_loss_sum_v1` 承载，程序化路径由 `audit_semantics.evaluate_derived_sum` 执行。

**影响面**：与 v7 一致，保留。覆盖 hbjc-5-r3（3.985 = 0.370 + 3.615）、
ezc/whc/xyc-5-r4（2.400 = 0.215 + 2.185）、对抗集 e09。

### C-04 显示精度差异 → `match` + `exact`

**触发**：单位与比较方向一致，数值在相对容差 `max(1e-6, |target| × 1e-4)` 内相等，
差异仅来自尾零、小数位或千分位写法（`60` 与 `60.0`、`35` 与 `35.0`、`0.3` 与 `0.3`）。

**判定**：`verdict=match`，`kind=exact`。

**理由**：v7 未定义此情形，而缺陷集用 4 条 `same_unit` 编辑专门测它（作为正控）。
不定义会让模型把 `60` 与 `60.0` 判成 `different` → 误报。

**影响面**：新增。覆盖缺陷集 `defect-04/05/08/09-same_unit`。

### C-05 定性条款 → `out_of_scope`，且由程序前置判定

**触发**：声称值中不存在可比数值、比较符、枚举值或可判定条件。
典型形态：`应无渗漏、无损伤`、`提供试验数据`、`外观检查无异常`、`应无异常迹象`、
`试验电压应不出现突然下降`、`波形图比较应无明显差异`。

**占位值**：声称值为 `/`、`-`、`—`、`–`、`无`、`N/A`、空 时同样 `out_of_scope`。
报告在「判定要求」列填占位符表示未提出限值；把占位符当分类值比较会产出
`吊心检查应无明显缺陷：/` 这类假 `wrong_label` 缺陷（实测 6 条）。

**判定**：`verdict=out_of_scope`，`kind=null`。

**关键约束**：该判定**必须由确定性前置路由产出，不得交给模型选择**。

**理由**：这是修复「四向判定退化成三向」的唯一办法。实测证据：40 case 评测中模型
一次都没有输出过 `not_audited`，gate 遵从 0/7；73 case 真实报告中 16 条 `unstructured_text`
全部进入了判定层。「拒判」交给模型，模型不会用——它总能编出一个判定。
把无可比限值的识别做成程序前置分类，模型就不再有机会过度判定。

**影响面**：新增（v7 有 `out_of_scope` 状态但无准入机制）。
611 case 全量重推实测命中 179 条（29.3%）。

**空文本不等于定性条款**：声称值文本为空是输入缺失，不是定性条款。
`derive` 对空文本返回 `verdict=null` + `underivable_reason=requirement_text_missing`，
不得走 C-05，否则任何上游抽取失败都会被静默记成「不纳入审查」。

### C-06 标准优先级 → 显式顺序，无法裁定时 `unevaluable` + `basis_conflict`

**触发**：两个及以上适用标准对同一试验项给出不同要求。

**裁定顺序**（自高到低）：

1. 报告「检测依据」中声明的顺序靠前者
2. 产品专用标准（表题明确指向被测产品类型）优先于通用标准
3. 同为专用或同为通用时：合同 / 招标技术规范 > 企业标准 > 行业标准（JB/T 等）
   > 国家标准通用部分（GB/T 1094.x 等）
4. 上述规则仍无法区分 → `verdict=unevaluable`，`kind=basis_conflict`，
   reasoning 列出冲突的两条依据

**无关冲突不阻塞**：若冲突的各条依据对该声称值给出相同关系（典型情形是声称值与
记录到的标准事实逐字相同），冲突不可能改变 verdict，必须照常判定并记 note
`basis_conflict_immaterial`。实测 4 条 `conflicting_table_bindings` 全属此类。

**理由**：`standard_value_audit_judge_v1.md` 目前明确把优先级交给模型
（"Q/GDW、JB/T、GB/T、GB 等标准的优先级只能依据输入证据中的适用范围、表题、引用关系和产品特征判断"），
而 `experiment/pi-standard-audit/EVALUATION_REPORT.md` 自己把 hbjc-13-r4 的唯一失败
归因为「标准优先级未定义」——模型走 Q/GDW 表 29 路径，gold 走 GB/T 1094.5 Ⅰ类路径。
这是业务裁定权，不是模型能力问题，必须显式化。

**影响面**：新增。611 条 case 每条声明 13 个标准号（含 GB/T 1094.x 系列、JB/T 501-2021、
GB/T 6451-2023、GB 20052-2024，报告 03 另有 Q/GDW 12126.4-2024），冲突面不小。

### C-07 标准版次与依据完整性 → 独立审查发现，不是运行异常

**触发**：

- 报告声明的标准版次与知识库中现行版次不一致 → flag `basis_edition_mismatch`
- 报告检测依据中的标准未在知识库全部命中 → flag `basis_incomplete`

**判定**：flags 独立输出，不改变该条 case 的 verdict；同时在报告级产出一条独立的
依据审查结论（对应 result schema 的 `basis_assessment.status` 与
`error_types` 中已有的 `obsolete_basis`）。

**理由**：`scripts/run_report_audit_workflow.py` 的
`_filter_evidence_file_ids_by_detection_basis` 目前在标准未命中时
`raise ValueError("报告检测依据中的标准未在当前知识库找到")`，直接终止整跑。
但「报告引用了作废或错误版次的标准」是一条真实且高价值的审查发现，
把它变成一次任务失败既丢掉了发现，也让整份报告无法审查。
数据里已有实例：报告 09 因 GB 20052 版本缺失被整份排除；
`reports.json` 中报告 01–02、04–07 声明 `GB/T 1094.10-2022` 而报告 03 声明 `GB/T 1094.101-2023`。

**影响面**：新增。需要 `standard_no` 从单一字符串扩展为 `(标准号, 版次, 有效性)`。

### C-08 容差与区间 → 区间代数

**触发**：任一侧为区间、容差或带宽表达式。
典型形态：`≤0.16(1+30%)`、`4.0(1±10%)`、`15≤t≤60`、`0.5±10%`、`35(1±1%)`、`3μs~6μs`。

**判定**：把两侧归一为闭区间后比较。

- 报告允许区间 ⊆ 标准允许区间 → `match`（若真包含则同时置 `tighter_than_standard`）
- 报告允许区间 ⊇ 标准允许区间，或部分重叠 → `mismatch` + `bandwidth_exceeded`
- 两区间不相交 → `mismatch`，kind 按 1.3 优先级对标称值取（不是 `bandwidth_exceeded`）
- 无法解析为区间 → `unevaluable` + `applicability_undetermined`

**先对齐，再比较**。区间比较前须依次处理三件事，缺一都会产生假缺陷：

1. **比较符方向**：标准事实常只记了限值数字而 `operator` 为 `eq`。退化区间 `[0.8, 0.8]`
   与 `(-∞, 0.234]` 会被判成不相交。须让退化的一侧继承另一侧的界限方向，
   与标量路径的处理一致。
2. **极性**：冲击电压报告写 `-75 kV` 而标准表写 `75 kV` 是同一要求。
   当一侧为带符号标称值、另一侧无符号，且比较符为等值或容差带时，按幅值比较。
3. **标称值优先**：两侧都是「标称值 ± 容差」时，先比标称值。标称值差异是数值错误
   （kind 按 1.3 取 `magnitude_error` 或 `numeric_looser`），不是带宽错误；
   只有标称值一致时带宽才决定判定。报告写 `-76(1±3%)` 而标准是 `75(1±3%)` 时，
   错的是标称值。

**标准侧容差必须被记录**。若报告侧用相对容差扩展某基值，而该基值恰等于记录到的标准值，
且标准事实未记录自己的容差，则判 `unevaluable` + `applicability_undetermined`
（note `standard_tolerance_not_recorded`）。
实测中同一条 `空载电流I₀（%）：≤0.18（1+30%）` 在 7 份报告里被标成 `mismatch`、
在另外几份里被标成 `supported`，分歧根源正是标注时是否把 GB/T 1094.1 表 1 的
`+30%` 偏差记进标准事实。此时判 `mismatch` 是猜。
一旦容差被抽取进 `real.tolerance`，本条即不再触发，标准上限按容差展开后正常比较。

**理由**：这些是变压器检测报告中最规范的容差写法，不是模型才能处理的模糊表达。
实测 73 case 中 11 条被 `extract_reason=multiple_numbers` 拒收、2 条 `expression` 拒收，
合计 13 条（18%）因为解析器不支持区间而退回 LLM。
新集 611 条中 `real.operator` 为 `range` 12 条、`tolerance` 10 条，另有 38 条带 `tolerance` 字段。

**影响面**：新增。需要区间表达式解析器与区间包含判断，属有界工程量。

### C-09 缺适用性参数 → `unevaluable` + `applicability_undetermined`

**触发**：找到了条件性条款，但决定适用性的样机参数在报告事实与型号解析结果中均缺失。

**禁止**：用「通常 / 一般 / 常见 / 大概率」等未被输入证明的行业经验补齐适用性。

**影响面**：与 v7 一致，保留。

### C-10 检索不到条款 → `unevaluable` + `standard_not_found`，带准入门槛

**触发**：满足全部准入条件后仍无适用条款。

**准入门槛**（缺一不可）：

1. 至少 2 种不同表述检索（按标准号 / 项目名 / 参数名）
2. 对最相关命中执行过全文读取——检索只返回截断预览，预览里没有不等于条款不存在
3. reasoning 列出已尝试的检索式与已读片段

**理由**：v6 实测教训——case 13-r4 检索了 9 次但 0 次读取全文就宣布「条款不存在」。
门槛在宿主端硬校验，异常路径追加一次重判提示。

**影响面**：与 v7 一致，保留。

### C-11 分类值比较 → `exact` / `wrong_label` / 拒判

**触发**：声称值形态为枚举或标号（`Dyn11`、`D/yn11`、`Yyn0`），`evidence_state == found`。

**判定**：

- 归一后逐字相同 → `match` + `exact`
- 两个明确不同的标号 → `mismatch` + `wrong_label`
- 标准侧是多个标号被拼接（如 `Dyn11Yyn0`）→ `unevaluable` + `applicability_undetermined`

**禁止**：在标准侧为拼接串时判定成员关系。拼接说明抽取丢掉了标号边界，
必须先把单元格拆成标号集合再报分。实测 6 条 `联结组标号：Dyn11` 对 `Dyn11Yyn0`
被标成 `mismatch`，但 `Dyn11` 很可能正是标准允许的两个标号之一。

**理由**：`wrong_label` 出现在 1.2 的 kind 表里，但 v1 初稿没有任何规则会产出它。

### C-12 原文引用 → `match` + `exact`

**触发**：声称值与记录到的标准值经归一（去标记、全角折半、去空白、大小写折叠）后逐字相同。

**判定**：`verdict=match`，`kind=exact`。该检查排在 C-08、C-11 之前。

**理由**：大量报告直接抄录标准原文，例如
`主分接电压比偏差：规定电压比的±0.5%与实际阻抗电压百分数的±1/10中较低者`。
双方字符串相同时无需解析即可判定；初稿会把这类 case 归为解析失败。
实测 611 条中本规则命中 91 条。

## 3. gold 推导

### 3.1 推导函数

```
derive(reported_requirement, real, conditions, caliber_v1)
  -> { verdict, kind, flags[], scoreable, derivation: { rules[], comparison } }
```

`derivation.rules` 必须记录用到的规则号（如 `["C-00", "C-08"]`），
`derivation.comparison` 记录归一后的两侧值、单位状态、`relation` 与 `tightness`。
没有 `derivation` 的 gold 条目视为无效。

实现与运行方式：

- 规则执行体：`backend/app/audit_caliber.py`（`derive` / `derive_case`）
- 全量重推与 diff：`scripts/derive_caliber_gold.py`
- 回归测试：`tests/test_audit_caliber.py`

```powershell
$env:PYTHONPATH='backend'; uv run python scripts/derive_caliber_gold.py
```

脚本不写 gold，只产出 `caliber_v0_to_v1_diff.json` 供人工确认。
当输入是流水线异常（`conditions` 含 `workflow_error`）时，`derive` 返回
`verdict=null` 并记 `underivable_reason`——异常不是判定结论，不允许被编造成一个。

### 3.2 可报分性（scoreable）

`scoreable=true` 仅当满足其一：

- `real.discovery_method ∈ {deterministic_comparison, deterministic_table_binding_cell}`，或
- `review.status == "human_confirmed"`（人工逐条确认，非批量通过）

`real.discovery_method == "unverified_model_value"` 一律 `scoreable=false`，
不论其 `expected_status` 是什么。

当前 `transformer_reports_v2/gold.json` 的实际分布：

| `real.discovery_method` | 条数 | 当前 scoreable |
|---|---:|---|
| `deterministic_comparison` | 83 | 是 |
| `deterministic_table_binding_cell` | 7 | 是 |
| `judgment_comparison` | 397 | 否（需人工确认） |
| `unverified_model_value` | 124 | 永不 |

即：**611 条中当前可直接信任的只有 90 条**；32 条真实 mismatch 中程序化确认的只有 8 条，
其余 24 条来自 `judgment_comparison`，必须人工确认后才能进入 MISS 集。

### 3.3 报告侧溯源（当前为阻塞项）

`gold.json` 全部 611 条的 `source_report` 为
`source_present=false`、`source_hash_matches=false`、`extraction_verified=false`，
因为源文件路径 `tmp/transformer-intake-20260907/` 已不存在。

审查的对象是「报告声称值」。若声称值本身无法回溯到源报告，判定对错就无从谈起。
因此在 7 份源文件（01–07 的 docx/pdf，sha256 已记录在 `reports.json`）重新归档并通过校验之前，
**611 条全部不得报分**，与 `report_judgment_scoreable` 当前全为 `false` 的状态一致。

证据侧溯源状况良好，无需重做：634 条 evidence 全部 `quote_verified=true`，
locator 带 `standard_no` / `page_start-end` / `text_sha256` / `bbox` / mineru `parse_id` 与 block 索引。

### 3.4 首次全量重推结果

`derive` 对 611 条真实 case 与 19 条缺陷 case 的重推结果（`caliber_v0_to_v1_diff.json`）：

| 项 | 值 |
|---|---:|
| 与原标签一致 | 428 |
| 改判 | 175 |
| 无法推导（`workflow_error`） | 8 |
| 缺陷集 19 条一致（verdict / kind / flags / 旧词表标签） | 19 / 19 |

改判分布与成因：

| 迁移 | 条数 | 成因 |
|---|---:|---|
| `not_audited` → `insufficient_context` | 66 | 旧词表把「无限值声称」和「查不到标准」都塞进 `not_audited`，本规范把后者归 `unevaluable` |
| `supported` → `not_audited` | 64 | C-05：定性条款与占位值不构成限值声称 |
| `supported` → `insufficient_context` | 24 | C-08 / C-09 / C-10：区间不可解析、单位不可换算、标准事实缺失 |
| `mismatch` → `insufficient_context` | 15 | C-08 标准侧容差未记录（7）、C-11 标号拼接（6）、复合条款不可解析（2） |
| `mismatch` → `supported` | 4 | 原标签自相矛盾，见下 |
| 其他 | 2 | — |

`mismatch` 总数从 32 降到 13。这不是判定变宽，而是原标签里有 19 条不成立：

- 4 条 `试验次数：9` 对标准 `9` 判 `mismatch`，而 basis 正文自己写着「与标准规定一致」。
  其中一条的理由是「程序校验指出确定性比较关系为 'different'，与 supported 冲突」——
  模型让位给了一个有 bug 的程序信号；另两条的理由是「未给出每次试验持续时间，无法确认」，
  那是 `unevaluable` 的理由，不是 `mismatch` 的理由。同样的 `试验次数：9`
  在报告 01 / 05 / 07 里被标成 `supported`。
- 7 条 `空载电流I₀（%）：≤0.18（1+30%）` 的分歧完全取决于标注时检索到哪本标准、
  以及是否把 GB/T 1094.1 表 1 的 `+30%` 记进标准事实（见 C-08）。
- 6 条 `联结组标号：Dyn11` 对拼接串 `Dyn11Yyn0`（见 C-11）。

结论：**32 条真实 mismatch 里只有 13 条经得起规则推导**，其中程序化来源的仍只有 8 条。
这直接决定 MISS 集的可用规模，必须在收敛计划里按 13 而不是 32 计。

### 3.5 唯一生产链路

没有模式开关。召回发生在程序审查**之前**，因为绑格需要表格 chunk。

```
抽参 / 抽试验项 / 型号解码     （固定工作流，可调用模型）
  → 每条 case：
       程序抽声称值
       C-05 定性条款 → 结束（不检索）
       查询规划 + 混合检索          ← 召回在这里
       选列、绑行、读格
       derive 能闭合 → 结束
       否则 Pi agent 取标准事实
       再 derive；仍闭不合 → unevaluable
```

旧 LLM judge、整段切换 `AUDIT_JUDGE_MODE=agent`、以及 recovery agent 都不再参与判定。
Agent 只交回 `standard_value` / 证据，不充当第二套法官。

`scripts/run_report_audit_workflow.py::_run_audit_judge_with_consistency` 的顺序：

1. **C-05 前置路由**：只看声称值，命中 `out_of_scope` 就直接出
   `not_audited`。
2. **口径裁定**：表格唯一绑定或派生加和成立时，从
   `trace.evidence_claim.value` 还原未换算的标准事实交给 `derive`。
3. **agent 取证**：`derive` 闭不合时请 sidecar 检索，把它返回的
   `standard_value` 再交给同一个 `derive`。闭不合就保持开放，不再另找 judge。

判定权标记为 `authority=programmatic_caliber`。C-05 的 `not_audited` 同样计入
`authority_closed`。

判定层对比（`scripts/compare_judge_layers.py`，两层读同一批输入）：

| 项 | 旧判定层 | 口径引擎 |
|---|---:|---:|
| 611 条程序闭合率 | 231 / 611（37.8%） | 415 / 611（67.9%） |
| 仍需调判定模型 | 380 条 | 196 条（−48.4%） |
| 与原标签一致率（各自闭合集内） | 204 / 231（88.3%） | 345 / 415（83.1%） |
| 缺陷集闭合 | 16 / 19 | 19 / 19 |
| 缺陷集判对 | 16 / 19（84.2%） | 19 / 19（100%） |

口径引擎的一致率更低，是因为它多闭合了 184 条旧层根本不碰的 case，
其中 64 条是 C-05 改判、4 条是 3.4 已核实的错标。它在原标签判 `supported`
的 case 上没有产出任何新的 `mismatch`。
旧层漏掉的 3 条缺陷是 `multiple_numbers`——`-75.0（1±3％）` 这类容差带
与 `0.3（构造值：0.3m）` 这类复述值，旧层数到多个数字就放弃。

## 4. 词表对照（crosswalk）

### 4.1 verdict ↔ 生产旧词表

生产读侧（`backend/schemas/standard_value_audit_result.schema.json` 的
`audit_assessment.status`、前端徽章、`gold.json` 的 `expected_status`）使用旧词表。
本规范以 v7 词表为内部表示，旧词表作为兼容别名：

| 规范 verdict | 旧词表 status |
|---|---|
| `match` | `supported` |
| `mismatch` | `mismatch` |
| `unevaluable` | `insufficient_context` |
| `out_of_scope` | `not_audited` |

**迁移建议**：`verdict` 与 `kind` 应升为一等字段并贯穿到读侧。当前 sidecar
把 `verdict` 映射回旧词表后，`kind` 在读侧被丢弃——而 `kind` 正是审查产出的价值所在
（回答「哪里错了」而不只是「错了」）。

### 4.2 缺陷集 `edit_kind` ↔ 规范

`data/evaluation_private/transformer_reports_v2/manifest.json` 的 19 条编辑：

| `edit_kind` | 条数 | 规范判定 | 规则 |
|---|---:|---|---|
| `looser` | 5 | `mismatch` + `numeric_looser` | C-01 反向 |
| `tighter` | 5 | `match` + `within_standard` + flag | C-01 |
| `different` | 4 | `mismatch` + `wrong_condition` 或 `numeric_looser` | 1.3 优先级 |
| `same_unit` | 4 | `match` + `exact` | C-04 |
| `unit_equivalence` | 1 | `match` + `unit_equivalent` | C-02 |

按本规范，19 条的 expected_status 与缺陷集现有标注**完全一致**，无需回改。
`scripts/derive_caliber_gold.py` 对这 19 条逐条校验 verdict、kind、flags 与旧词表标签，
当前 19 / 19 全部一致。冲突方是 `skill.md` / `prompt.ts` 的 v7 第 6 条，应按 C-01 修正。

### 4.3 `real.operator` ↔ 比较路径

| `real.operator` | 条数 | 路径 |
|---|---:|---|
| `eq` | 206 | C-04 / 精确比较 |
| `le` | 124 | C-01 / C-08 |
| `unknown` | 119 | 需先补齐算子，否则 `scoreable=false` |
| （空） | 124 | 同上，与 `unverified_model_value` 高度重合 |
| `ge` | 15 | C-01 / C-08 |
| `range` | 12 | C-08 |
| `tolerance` | 10 | C-08 |
| `gt` | 1 | C-01 / C-08 |

## 5. 变更流程

1. 口径变更提案写入本文档的新版本（`caliber_v2.md` + `caliber_v2.json`），
   旧版本保留不改。
2. 用新版本对全部 gold 重新执行 `derive`，产出 `caliber_v1_to_v2_diff.json`，
   列出每条改判的 case、原判定、新判定、触发规则。
3. diff 需经人工确认后方可切换 `active` 版本。
4. 所有评测结果必须记录所依据的 `caliber_version`；跨版本分数不得直接比较。
5. **禁止**单条 gold 的手工改判。发现某条 gold 不合理时，改的是规则，不是标签。

## 6. 未决项（需业务方裁定，不得由模型或实现方即兴决定）

1. **加严的容忍上限**。C-01 判 `match`，但报告声称值严于标准 10 倍是否仍算 `match`？
   建议引入阈值，超过则置 `tighter_than_standard` 之外再加一条提示，但阈值需业务给定。
2. **合同 / 招标规范的地位**。C-06 第 3 条把它排在企标之上，但当报告检测依据未声明它时，
   是否仍可作为裁定依据？
3. **`GB/T 1094.10-2022` 与 `GB/T 1094.101-2023` 的关系**。属版次替代还是并行有效标准？
   直接决定 C-07 是否对报告 01–02、04–07 触发 `basis_edition_mismatch`。
4. **`judgment_comparison` 的 397 条如何确认**。全部人工不现实。
   建议只确认进入 DEV / VAL / TEST / MISS 的抽样部分（约 370 条中的非程序化条目），
   优先级最高的是 24 条 `judgment_comparison` 来源的 mismatch。
5. **报告 08 / 09 是否补齐**。08 为扫描件 OCR 失败，09 缺 GB 20052 对应版本标准库。
   补齐可把池子从 611 扩到约 780。
6. **空载电流的 `+30%` 偏差是否普遍适用**。GB/T 1094.1 表 1 给出空载电流偏差为设计值的
   `+30%`。若适用，则报告写 `≤0.18(1+30%)` 是在引用标准自身的容差，判 `match`；
   若不适用，则是自行放宽 30%，判 `mismatch`。这一条裁定直接影响 3.4 中 7 条 case，
   且必须落到 `real.tolerance` 的抽取规则里，不能停在口径文字上。
7. **联结组标号的允许集合**。标准单元格 `Dyn11Yyn0` 是两个允许标号还是一个值？
   决定 6 条 case 判 `match` 还是 `wrong_label`，需要把表格单元格的标号边界抽取出来。
