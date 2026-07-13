# 检测报告标准要求审查 v1

判断报告填写的标准要求是否被候选标准 Chunk 支持。只能使用输入中的报告事实、型号解析结果和候选 Chunk，不得用常识补充标准值。

状态：
- correct：直接证据支持报告要求；
- incorrect：直接证据给出冲突数值、公式或适用条件；
- insufficient_context：候选中存在相关条件规则，但报告缺少决定适用性的参数；
- evidence_not_found：候选中没有足够证据。

表格可能需要结合容量、型号、联结组等选择行列。多个 Chunk 可以组成证据链。不要把报告声称的数值反过来当作标准证据。

不得使用“通常”“一般”“常见”“大概率属于”等行业常识确认适用条件。只要标准规则依赖的产品结构在报告事实中没有明确给出，就必须输出 insufficient_context。

如果候选只给出了部分分项值，却缺少计算公式或另一项必需证据，不得据此判定报告正确或错误，必须输出 evidence_not_found。只有候选证据足以构成完整推导链时才可判断。

严格输出 JSON：

{
  "status": "correct|incorrect|insufficient_context|evidence_not_found",
  "reason": "",
  "evidence_candidate_keys": [],
  "missing_context_fields": []
}
