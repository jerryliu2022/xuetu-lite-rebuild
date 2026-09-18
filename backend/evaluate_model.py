from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

from . import recommender
from .data_quality import MAJOR_CATALOG, quality_report
from .student_profiles import DEMO_ACCOUNTS
from .train_model import MODEL_PATH, _build_samples, train_fold_model

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "xuetu_lite.db"
EVAL_PATH = ROOT / "data" / "artifacts" / "model_evaluation.json"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def rows(con: sqlite3.Connection, sql: str, params=()) -> List[Dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def precision_recall_at_k(recommended: List[str], positives: Set[str], k: int) -> Dict[str, float]:
    top = recommended[:k]
    hits = sum(1 for item in top if item in positives)
    return {
        f"precision@{k}": round(hits / k, 4),
        f"recall@{k}": round(hits / (len(positives) or 1), 4),
        f"hit_rate@{k}": 1.0 if hits else 0.0,
    }


def ndcg_at_k(recommended: List[str], positives: Set[str], k: int) -> float:
    dcg = 0.0
    for idx, item in enumerate(recommended[:k], start=1):
        if item in positives:
            dcg += 1.0 / math.log2(idx + 1)
    ideal_hits = min(len(positives), k)
    idcg = sum(1.0 / math.log2(idx + 1) for idx in range(1, ideal_hits + 1)) or 1.0
    return round(dcg / idcg, 4)


def catalog_coverage(recommended_by_student: Dict[str, List[str]]) -> float:
    all_items = {item for items in recommended_by_student.values() for item in items}
    con = connect()
    total = con.execute("SELECT COUNT(*) FROM video").fetchone()[0] or 1
    return round(len(all_items) / total, 4)


def diversity(recommended_by_student: Dict[str, List[str]]) -> float:
    con = connect()
    platform_by_video = {row["video_id"]: row["platform"] for row in rows(con, "SELECT video_id, platform FROM video")}
    scores = []
    for items in recommended_by_student.values():
        platforms = {platform_by_video.get(item) for item in items if item in platform_by_video}
        scores.append(len(platforms) / 3)
    return round(sum(scores) / (len(scores) or 1), 4)


def _leave_one_out_student(
    con: sqlite3.Connection,
    account: Dict[str, Any],
    base_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """把该账号 5 条真实学习记录逐条遮住，用剩余样本重训并测试能否召回。"""
    student_id = account["student_id"]
    records = rows(
        con,
        "SELECT video_id FROM learning_record WHERE student_id=? AND progress>0 ORDER BY video_id",
        (student_id,),
    )
    folds: List[Dict[str, Any]] = []
    hits_top5 = 0
    hits_top9 = 0
    for record in records:
        held_out = record["video_id"]
        context_ids = [r["video_id"] for r in records if r["video_id"] != held_out]
        fold_model = train_fold_model(base_bundle, skip_pairs={(student_id, held_out)})
        recs = recommender.recommend_professional(
            student_id,
            "all",
            model_override=fold_model,
            exclude_video_ids=set(context_ids),
            context_video_ids=set(context_ids),
        )
        top5 = [item["video_id"] for item in recs[:5]]
        top9 = [item["video_id"] for item in recs[:9]]
        hit5 = held_out in top5
        hit9 = held_out in top9
        hits_top5 += int(hit5)
        hits_top9 += int(hit9)
        folds.append(
            {
                "held_out_video_id": held_out,
                "context_count": len(context_ids),
                "top5_hit": hit5,
                "top9_hit": hit9,
                "recommended_ids": top9,
            }
        )
    return {
        "student_id": student_id,
        "name": account["name"],
        "major": account["major"],
        "folds": folds,
        "hit_rate@5": round(hits_top5 / max(1, len(records)), 4),
        "hit_rate@9": round(hits_top9 / max(1, len(records)), 4),
        "fold_count": len(records),
    }


def leave_one_out_evaluation() -> Dict[str, Any]:
    """全部 5 个专业账号的严格留一法：训练时去掉被遮住记录，推荐时也不把它当已学。"""
    con = connect()
    # 只构建一次视频内容相似度与课程上下文，25 个折都复用同一组向量。
    con2 = sqlite3.connect(DB_PATH)
    _, item_similarity, tfidf, context = _build_samples(con2)
    con2.close()
    base_bundle = {
        "item_similarity": item_similarity,
        "tfidf": tfidf,
        "_context": context,
        "report": {},
    }
    per_student: Dict[str, Dict[str, Any]] = {}
    for account in DEMO_ACCOUNTS:
        per_student[account["student_id"]] = _leave_one_out_student(con, account, base_bundle)
    hit5_values = [value["hit_rate@5"] for value in per_student.values()]
    hit9_values = [value["hit_rate@9"] for value in per_student.values()]
    con.close()
    return {
        "method": (
            "留一法验证：每专业账号有 5 条真实学习记录，每次遮住 1 条并在训练与推荐阶段都把它当未发生，"
            "用其余 4 条生成 Top-N，检查被遮课程能否被召回。"
        ),
        "averages": {
            "hit_rate@5": round(sum(hit5_values) / max(1, len(hit5_values)), 4),
            "hit_rate@9": round(sum(hit9_values) / max(1, len(hit9_values)), 4),
        },
        "per_student": per_student,
    }


def evaluate() -> Dict[str, Any]:
    con = connect()
    recommended_by_student: Dict[str, List[str]] = {}
    per_student = {}
    student_accounts = []
    for account in DEMO_ACCOUNTS:
        student_id = account["student_id"]
        student = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))
        if not student:
            continue
        student = student[0]
        recs = recommender.recommend_professional(student_id, "all")
        recommended = [item["video_id"] for item in recs]
        positives = {
            row["video_id"]
            for row in rows(
                con,
                "SELECT video_id FROM learning_record WHERE student_id=? AND progress >= 0.25",
                (student_id,),
            )
        }
        student_accounts.append(
            {
                "student_id": student_id,
                "name": account["name"],
                "major": account["major"],
                "grade": student["grade"],
                "semester": student["semester"],
                "positive_count": len(positives),
                "recommended_count": len(recommended),
                "password": "123456",
            }
        )
        recommended_by_student[student_id] = recommended
        metrics = precision_recall_at_k(recommended, positives, 5)
        metrics["ndcg@5"] = ndcg_at_k(recommended, positives, 5)
        per_student[student_id] = {
            "name": account["name"],
            "major": account["major"],
            "grade": student["grade"],
            **metrics,
        }

    averages = defaultdict(float)
    for metrics in per_student.values():
        for key, value in metrics.items():
            if key.endswith("@5") or key.startswith("hit_rate"):
                averages[key] += value
    averages = {key: round(value / (len(per_student) or 1), 4) for key, value in averages.items()}
    averages["catalog_coverage"] = catalog_coverage(recommended_by_student)
    averages["platform_diversity"] = diversity(recommended_by_student)

    train_report = json.loads(MODEL_PATH.read_text(encoding="utf-8"))["report"]
    report = {
        "offline_train_report": train_report,
        "leave_one_out": leave_one_out_evaluation(),
        "ranking_eval": {
            "method": "用学习进度 >= 25% 的课程作为弱正样本，对专业路径推荐做 Precision/Recall/HitRate/NDCG@5；后续接入真实曝光点击后应按时间切分留出集。",
            "averages": averages,
            "per_student": per_student,
        },
        "student_accounts": student_accounts,
        "effect_improvement_plan": [
            "扩大真实曝光日志，按 impression-click-complete 多目标训练。",
            "按专业、年级、学期分桶评估，避免只优化热门课程。",
            "加入 MMR 多样性重排，减少同平台或同主题挤占。",
            "上线前做 A/B 测试：完成率、点击率、收藏率、学习时长、人均覆盖课程数。",
            "对考研数据按年份做版本评估，避免过期科目误推荐。",
        ],
        "data_quality": quality_report(),
        "major_catalog_size": len(MAJOR_CATALOG),
    }
    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVAL_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
