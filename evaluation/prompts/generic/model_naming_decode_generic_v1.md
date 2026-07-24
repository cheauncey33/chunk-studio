# 通用型号命名规则解析 v1

只依据报告原始型号、报告参数和输入的型号命名规则 Markdown 解析型号。不得使用行业常识补全，不得生成任何标准限值。

每个已解析特征必须附带规则中的短原文证据；无法确认的片段放入 unresolved_segments。解析结果仅用于检索 Query 改写，不是审查证据。

严格输出 JSON：

{
  "raw_model": "",
  "decoded_features": [
    {"segment": "", "meaning": "", "evidence_quote": ""}
  ],
  "retrieval_terms": [],
  "unresolved_segments": []
}
