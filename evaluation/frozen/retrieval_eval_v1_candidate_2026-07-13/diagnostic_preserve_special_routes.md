# Frozen Retrieval Baseline

Freeze: `evaluation\frozen\retrieval_eval_v1_candidate_2026-07-13`
Generated at: `2026-07-13T05:12:01.853532+00:00`
Embedding model: `text-embedding-v4`

This report measures retrieval recall against the frozen candidate gold. It does not judge numerical correctness.

## Summary

| Route set | Direct cases | Direct case recall | Direct evidence recall | All evidence recall | Direct MRR |
|---|---:|---:|---:|---:|---:|
| all_routes | 26/38 | 0.684 | 0.628 | 0.727 | 0.192 |
| no_table_target | 22/38 | 0.579 | 0.535 | 0.612 | 0.183 |
| production_only | 12/38 | 0.316 | 0.279 | 0.396 | 0.100 |

## all_routes By Split

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| development | 5/9 | 0.556 | 0.500 | 0.757 |
| test | 14/19 | 0.737 | 0.682 | 0.743 |
| validation | 7/10 | 0.700 | 0.636 | 0.656 |

## all_routes By Retrieval Class

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| insulation_voltage | 6/8 | 0.750 | 0.750 | 0.864 |
| numeric_parameter_table | 8/12 | 0.667 | 0.667 | 0.767 |
| repeat_routine | 0/2 | 0.000 | 0.000 | 0.556 |
| report_specific | 8/8 | 1.000 | 1.000 | 0.833 |
| rule_formula | 4/8 | 0.500 | 0.333 | 0.543 |

## all_routes Direct Misses

| Case | Split | Class | Evidence type | Requirement | Query routes |
|---|---|---|---|---|---|
| hbjc-5-r1 | development | numeric_parameter_table | table | 负载损耗Pk(kW):≤3.615 | production, semantic, keyword, table_target, section_target |
| hbjc-7-r1 | development | insulation_voltage | mixed | 高压对低压及地试验电压(kV):35 | production, semantic, keyword, table_target, section_target |
| hbjc-5-r3 | development | rule_formula | mixed | 总损耗P总(kW):≤3.985 | production, semantic, keyword, table_target, section_target |
| hbjc-15-5-r1 | development | repeat_routine | table | 负载损耗Pk(kW): ≤3.615 | production, semantic, keyword, table_target, section_target |
| ezc-5-r2 | validation | numeric_parameter_table | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target, section_target |
| ezc-10-r1 | validation | insulation_voltage | mixed | 高压线端全波电压(kV): 75kV | production, semantic, keyword, table_target, section_target |
| ezc-5-r4 | validation | rule_formula | mixed | 总损耗P总(kW): ≤2.40 | production, semantic, keyword, table_target, section_target |
| whc-5-r2 | test | numeric_parameter_table | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target |
| whc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
| whc-14-2-5-r2 | test | repeat_routine | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target, section_target |
| xyc-4-r1 | test | numeric_parameter_table | table | 空载损耗Po(kW): ≤0.215 | production, semantic, keyword, table_target |
| xyc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
