# Frozen Retrieval Baseline

Freeze: `evaluation\frozen\retrieval_eval_v1_candidate_2026-07-13`
Generated at: `2026-07-13T05:35:22.188420+00:00`
Embedding model: `text-embedding-v4`

This report measures retrieval recall against the frozen candidate gold. It does not judge numerical correctness.

## Summary

| Route set | Direct cases | Direct case recall | Direct evidence recall | All evidence recall | Direct MRR |
|---|---:|---:|---:|---:|---:|
| all_routes | 29/38 | 0.763 | 0.698 | 0.784 | 0.224 |
| no_table_target | 31/38 | 0.816 | 0.744 | 0.835 | 0.242 |
| production_only | 23/38 | 0.605 | 0.558 | 0.705 | 0.111 |

## all_routes By Split

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| development | 9/9 | 1.000 | 0.900 | 0.757 |
| test | 13/19 | 0.684 | 0.636 | 0.800 |
| validation | 7/10 | 0.700 | 0.636 | 0.781 |

## all_routes By Retrieval Class

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| insulation_voltage | 8/8 | 1.000 | 1.000 | 0.955 |
| numeric_parameter_table | 6/12 | 0.500 | 0.500 | 0.767 |
| repeat_routine | 2/2 | 1.000 | 1.000 | 0.667 |
| report_specific | 8/8 | 1.000 | 1.000 | 0.900 |
| rule_formula | 5/8 | 0.625 | 0.417 | 0.629 |

## all_routes Direct Misses

| Case | Split | Class | Evidence type | Requirement | Query routes |
|---|---|---|---|---|---|
| ezc-4-r2 | validation | numeric_parameter_table | mixed | 空载电流I0(%): ≤0.18(允许偏差+30%) | production, semantic, keyword, table_target |
| ezc-5-r2 | validation | numeric_parameter_table | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target |
| ezc-5-r4 | validation | rule_formula | mixed | 总损耗P总(kW): ≤2.40 | production, semantic, keyword, table_target, section_target |
| whc-4-r2 | test | numeric_parameter_table | mixed | 空载电流I0(%): ≤0.18(1+30%) | production, semantic, keyword, table_target |
| whc-5-r2 | test | numeric_parameter_table | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target |
| whc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
| xyc-4-r1 | test | numeric_parameter_table | table | 空载损耗Po(kW): ≤0.215 | production, semantic, keyword, table_target |
| xyc-5-r2 | test | numeric_parameter_table | table | 负载损耗P☐(kW): ≤2.185 | production, semantic, keyword, table_target |
| xyc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
