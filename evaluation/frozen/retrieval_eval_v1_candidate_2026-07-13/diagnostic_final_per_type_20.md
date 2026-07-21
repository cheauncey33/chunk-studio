# Frozen Retrieval Baseline

Freeze: `evaluation\frozen\retrieval_eval_v1_candidate_2026-07-13`
Generated at: `2026-07-13T05:08:10.413545+00:00`
Embedding model: `text-embedding-v4`

This report measures retrieval recall against the frozen candidate gold. It does not judge numerical correctness.

## Summary

| Route set | Direct cases | Direct case recall | Direct evidence recall | All evidence recall | Direct MRR |
|---|---:|---:|---:|---:|---:|
| all_routes | 31/38 | 0.816 | 0.744 | 0.871 | 0.196 |
| no_table_target | 29/38 | 0.763 | 0.698 | 0.813 | 0.189 |
| production_only | 23/38 | 0.605 | 0.558 | 0.705 | 0.111 |

## all_routes By Split

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| development | 6/9 | 0.667 | 0.600 | 0.811 |
| test | 16/19 | 0.842 | 0.773 | 0.900 |
| validation | 9/10 | 0.900 | 0.818 | 0.875 |

## all_routes By Retrieval Class

| Group | Direct cases | Direct case recall | Direct evidence recall | All evidence recall |
|---|---:|---:|---:|---:|
| insulation_voltage | 8/8 | 1.000 | 1.000 | 1.000 |
| numeric_parameter_table | 10/12 | 0.833 | 0.833 | 0.907 |
| repeat_routine | 1/2 | 0.500 | 0.500 | 0.778 |
| report_specific | 8/8 | 1.000 | 1.000 | 0.967 |
| rule_formula | 4/8 | 0.500 | 0.333 | 0.686 |

## all_routes Direct Misses

| Case | Split | Class | Evidence type | Requirement | Query routes |
|---|---|---|---|---|---|
| hbjc-5-r1 | development | numeric_parameter_table | table | 负载损耗Pk(kW):≤3.615 | production, semantic, keyword, table_target, section_target |
| hbjc-5-r3 | development | rule_formula | mixed | 总损耗P总(kW):≤3.985 | production, semantic, keyword, table_target, section_target |
| hbjc-15-5-r1 | development | repeat_routine | table | 负载损耗Pk(kW): ≤3.615 | production, semantic, keyword, table_target, section_target |
| ezc-5-r4 | validation | rule_formula | mixed | 总损耗P总(kW): ≤2.40 | production, semantic, keyword, table_target, section_target |
| whc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
| xyc-4-r1 | test | numeric_parameter_table | table | 空载损耗Po(kW): ≤0.215 | production, semantic, keyword, table_target |
| xyc-5-r4 | test | rule_formula | mixed | 总损耗P总(kW): ≤2.400 | production, semantic, keyword, table_target, section_target |
