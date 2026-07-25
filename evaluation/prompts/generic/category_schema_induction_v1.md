# 品类参数 Schema 归纳 v1

你是电力检测审查配置设计师。根据标准语料摘录与样例报告摘录，归纳该品类的报告级参数 schema。

## 任务

1. 设计 `parameter_schema`：只包含**报告级样品参数**（型号、额定值、结构特征等），不要放试验项目结果（阻抗、损耗、温升、耐压结果等）。
2. 字段 `key` 用简短英文或拼音 snake_case；`label` 用中文；`required` 仅对审查必需字段为 true；`hint` 说明在报告中通常出现的位置或写法。
3. 必须包含 `model`（或等价的产品型号字段，key 仍用 `model`）且 `required=true`。
4. 字段数量建议 4～12 个，避免堆砌。
5. `allow_extra` 建议为 true，以便抽参时保留未列字段。
6. 只能依据输入材料归纳；材料不足时仍给出合理最小 schema。

## 输出

严格输出 JSON，不要 Markdown 代码块：

```json
{
  "parameter_schema": {
    "version": 1,
    "allow_extra": true,
    "fields": [
      {"key": "model", "label": "型号", "required": true, "hint": ""},
      {"key": "other_key", "label": "中文名", "required": false, "hint": ""}
    ]
  }
}
```
