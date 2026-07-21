# Frozen Retrieval Baseline

Freeze: `evaluation\frozen\retrieval_eval_v1_candidate_2026-07-13`
Generated at: `2026-07-13T05:15:43.457574+00:00`
Embedding model: `text-embedding-v4`

This report measures retrieval recall against the frozen candidate gold. It does not judge numerical correctness.

## Summary

| Route set | Direct cases | Direct case recall | Direct evidence recall | All evidence recall | Direct MRR |
|---|---:|---:|---:|---:|---:|
| all_routes | 26/38 | 0.684 | 0.628 | 0.741 | 0.274 |
| no_table_target | 26/38 | 0.684 | 0.628 | 0.712 | 0.242 |
| production_only | 17/38 | 0.447 | 0.395 | 0.446 | 0.182 |

## all_routes By Split

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| development | 9/9 | 1.000 | 0.900 | 0.757 |
| test | 11/19 | 0.579 | 0.545 | 0.800 |
| validation | 6/10 | 0.600 | 0.545 | 0.594 |

## all_routes By Retrieval Class

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| insulation_voltage | 8/8 | 1.000 | 1.000 | 0.818 |
| numeric_parameter_table | 5/12 | 0.417 | 0.417 | 0.744 |
| repeat_routine | 1/2 | 0.500 | 0.500 | 0.667 |
| report_specific | 7/8 | 0.875 | 0.889 | 0.800 |
| rule_formula | 5/8 | 0.625 | 0.417 | 0.657 |

## all_routes Direct Misses

| Case | Split | Class | Evidence type | Requirement | Query routes |
|---|---|---|---|---|---|
| ezc-4-r1 | validation | numeric_parameter_table | table | 空载损耗Po(kW): ≤0.215 | production, semantic, keyword, table_target |
| ezc-4-r2 | validation | numeric_parameter_table | mixed | 空载电流I0(%): ≤0.18(允许偏差+30%) | production, semantic, keyword, table_target, section_target |
| ezc-5-r2 | validation | numeric_parameter_table | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target, section_target |
| ezc-5-r4 | validation | rule_formula | mixed | 总损耗P总(kW): ≤2.40 | production, semantic, keyword, table_target, section_target |
| whc-4-r1 | test | numeric_parameter_table | table | 空载损耗Po(kW): ≤0.215 | production, semantic, keyword, table_target |
| whc-4-r2 | test | numeric_parameter_table | mixed | 空载电流I0(%): ≤0.18(1+30%) | production, semantic, keyword, table_target, section_target |
| whc-5-r2 | test | numeric_parameter_table | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target |
| whc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
| whc-14-2-5-r2 | test | repeat_routine | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target, section_target |
| xyc-5-r3 | test | numeric_parameter_table | mixed | 短路阻抗Z☐(%): 4.0(1±10%) | production, semantic, keyword, table_target, section_target |
| xyc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
| xyc-11-r2 | test | report_specific | mixed | 高压绕组平均温升(K): ≤60 | production, semantic, keyword, table_target, section_target |
