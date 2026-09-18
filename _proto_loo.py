from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "processed" / "xuetu_lite.db"
MODEL = ROOT / "data" / "artifacts" / "ranker_model.json"

sys_path = str(ROOT)
import sys

if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from backend import recommender  # noqa: E402


def rows(con: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def main() -> None:
    con = sqlite3.connect(DB)
    model = json.loads(MODEL.read_text(encoding="utf-8"))
    accounts = [
        {"student_id": "20240101", "major": "计算机科学与技术"},
        {"student_id": "20240102", "major": "软件工程"},
        {"student_id": "20240103", "major": "数据科学与大数据技术"},
        {"student_id": "20240104", "major": "人工智能"},
        {"student_id": "20240105", "major": "网络工程"},
    ]
    print("fold / total top-9 hits for masked learned video (using existing model):")
    for account in accounts:
        sid = account["student_id"]
        records = rows(
            con,
            "SELECT video_id FROM learning_record WHERE student_id=? AND progress>0 ORDER BY video_id",
            (sid,),
        )
        hits = 0
        for record in records:
            vid = record["video_id"]
            other = [r["video_id"] for r in records if r["video_id"] != vid]
            done = set(other)
            recs = recommender.recommend_professional(
                sid,
                "all",
                model_override=model,
                exclude_video_ids=done,
                context_video_ids=done,
            )
            top = [item["video_id"] for item in recs[:9]]
            hit = vid in top
            hits += int(hit)
            print(sid, vid[:14], "top9 hit" if hit else "miss", "| top5:", vid in top[:5])
        print("  ->", sid, "hits/records:", hits, "/", len(records))
    con.close()


if __name__ == "__main__":
    main()
