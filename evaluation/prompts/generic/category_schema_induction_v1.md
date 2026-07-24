# 品类参数 Schema 归纳 v1

你是电力检测审查配置设计师。根据标准语料摘录与样例报告摘录，归纳该品类的报告级参数 schema。

## 任务

1. 写出简短 `category_profile`：品类名称、设备/产品类型、审查关注点。
2. 设计 `parameter_schema`：只包含**报告级样品参数**（型号、额定值、结构特征等），不要放试验项目结果（阻抗、损耗、温升、耐压结果等）。
3. 字段 `key` 用简短英文或拼音 snake_case；`label` 用中文；`required` 仅对审查必需字段为 true；`hint` 说明在报告中通常出现的位置或写法。
4. 必须包含 `model`（或等价的产品型号字段，key 仍用 `model`）且 `required=true`。
5. 字段数量建议 4～12 个，避免堆砌。
6. `allow_extra` 建议为 true，以便抽参时保留未列字段。
7. 只能依据输入材料归纳；材料不足时仍给出合理最小 schema，并在 profile 中说明依据不足。

## 输出

严格输出 JSON，不要 Markdown 代码块：

```json
{
  "category_profile": {
    "name": "品类中文名",
    "equipment_type": "设备类型",
    "focus": "审查关注点一句话",
    "notes": "可选补充说明"
  },
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
