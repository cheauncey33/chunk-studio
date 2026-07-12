from __future__ import annotations

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_retrieval_candidates import _apply_table_metadata_bonus


def test_table_metadata_bonus_records_title_and_column_matches() -> None:
    case = {
        "queries": {
            "keyword": "GB/T 1094.3 外施耐压试验 AV 试验电压 10 kV 35 kV 60 s",
        }
    }
    candidate = {
        "rrf_score": 0.02,
        "business_metadata": {
            "table_title": "绕组的试验电压水平",
            "table_columns": ["设备最高电压 Um", "外施耐压或线端交流耐压(AV)kV"],
        },
    }

    _apply_table_metadata_bonus(case, candidate)

    rerank = candidate["metadata_rerank"]
    assert rerank["title_bonus"] > 0
    assert rerank["columns_bonus"] > 0
    assert any(match["term"] == "外施耐压试验" for match in rerank["column_matches"])
    assert candidate["rerank_score"] > candidate["rrf_score"]
