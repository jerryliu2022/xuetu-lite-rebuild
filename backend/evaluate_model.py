from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

from . import db as _db
from . import recommender
from .data_quality import MAJOR_CATALOG, quality_report
from .student_profiles import DEMO_ACCOUNTS
from .train_model import MODEL_PATH, _build_samples, train_fold_model

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = _db.DB_PATH
EVAL_PATH = ROOT / "data" / "artifacts" / "model_evaluation.json"


def connect() -> sqlite3.Connection:
    return _db.connection()


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
    """推荐结果的平台分散度：5 个平台都覆盖到记 1.0。"""
    con = connect()
    platform_by_video = {row["video_id"]: row["platform"] for row in rows(con, "SELECT video_id, platform FROM video")}
    platform_count = con.execute("SELECT COUNT(DISTINCT platform) FROM video").fetchone()[0] or 1
    scores = []
    for items in recommended_by_student.values():
        platforms = {platform_by_video.get(item) for item in items if item in platform_by_video}
        scores.append(len(platforms) / platform_count)
    return round(sum(scores) / (len(scores) or 1), 4)


MAX_LOO_FOLDS = 2   # 每账号最多做几折：每折一次重训，折数太大会把评估拖到十几分钟

# 测试评估只取前 5 个专业账号（YY01~YY05）：25 个账号全量跑一遍要 2 分钟以上，
# 平时点「重新评估」用 5 个账号出指标足够看趋势，全量口径只在需要时手动跑。
EVAL_ACCOUNT_LIMIT = 5


def _leave_one_out_student(
    con: sqlite3.Connection,
    account: Dict[str, Any],
    base_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """遮住该账号的部分学习记录，用剩余样本重训并测试能否把它们召回来。"""
    student_id = account["student_id"]
    records = rows(
        con,
        "SELECT video_id FROM learning_record WHERE student_id=? AND progress>0 ORDER BY video_id",
        (student_id,),
    )
    all_ids = [record["video_id"] for record in records]
    if len(all_ids) > MAX_LOO_FOLDS:
        step = len(all_ids) / MAX_LOO_FOLDS
        held_out_ids = [all_ids[int(i * step)] for i in range(MAX_LOO_FOLDS)]
    else:
        held_out_ids = all_ids

    folds: List[Dict[str, Any]] = []
    hits_top5 = 0
    hits_top24 = 0
    for held_out in held_out_ids:
        context_ids = [item for item in all_ids if item != held_out]
        fold_model = train_fold_model(base_bundle, skip_pairs={(student_id, held_out)})
        result = recommender.recommend_professional(
            student_id,
            "all",
            model_override=fold_model,
            exclude_video_ids=set(context_ids),
        )
        recs = result["items"] if isinstance(result, dict) else result
        top5 = [item["video_id"] for item in recs[:5]]
        top24 = [item["video_id"] for item in recs[:24]]
        hit5 = held_out in top5
        hit24 = held_out in top24
        hits_top5 += int(hit5)
        hits_top24 += int(hit24)
        folds.append(
            {
                "held_out_video_id": held_out,
                "context_count": len(context_ids),
                "top5_hit": hit5,
                "top24_hit": hit24,
                "recommended_ids": top24,
            }
        )
    total = max(1, len(held_out_ids))
    return {
        "student_id": student_id,
        "name": account["name"],
        "major": account["major"],
        "learning_record_count": len(all_ids),
        "folds": folds,
        "hit_rate@5": round(hits_top5 / total, 4),
        "hit_rate@24": round(hits_top24 / total, 4),
        "fold_count": len(held_out_ids),
    }


def leave_one_out_evaluation() -> Dict[str, Any]:
    """留一法评估：默认取前 EVAL_ACCOUNT_LIMIT 个专业账号，训练时去掉被遮住记录。"""
    con = connect()
    # 只构建一次视频内容相似度与课程上下文，所有折都复用同一组向量。
    eval_accounts = DEMO_ACCOUNTS[:EVAL_ACCOUNT_LIMIT]
    con2 = _db.open_connection()
    _, item_similarity, tfidf, context = _build_samples(con2)
    con2.close()
    base_bundle = {
        "item_similarity": item_similarity,
        "tfidf": tfidf,
        "_context": context,
        "report": {},
    }
    per_student: Dict[str, Dict[str, Any]] = {}
    for account in eval_accounts:
        per_student[account["student_id"]] = _leave_one_out_student(con, account, base_bundle)
    hit5_values = [value["hit_rate@5"] for value in per_student.values()]
    hit24_values = [value["hit_rate@24"] for value in per_student.values()]
    con.close()
    return {
        "method": (
            f"留一法验证：{len(eval_accounts)} 个专业账号各取 {MAX_LOO_FOLDS} 条学习记录做遮挡，"
            "每次遮住 1 条，训练时跳过这条曝光、推荐时也不把它当已学，"
            "检查被遮住的那条资源能否被 Top-5 / Top-24 召回。"
        ),
        "averages": {
            "hit_rate@5": round(sum(hit5_values) / max(1, len(hit5_values)), 4),
            "hit_rate@24": round(sum(hit24_values) / max(1, len(hit24_values)), 4),
        },
        "per_student": per_student,
    }


def expected_pool(con: sqlite3.Connection, student: Dict[str, Any]) -> Dict[str, Any]:
    """学生「应该学但还没学」的正样本池。

    原实现拿 progress>=25% 的已学视频当正样本，而推荐链路又刻意排除已学视频，
    两个集合天然不相交，所以 Precision@5 永远是 0。这里的口径改成：

    - 本学期正在上的课
    - 下学期即将上的课
    - 已修完课程的直系后继课（先修关系已满足）

    三类课程的资源里，学生尚未接触过的部分就是"路径上应该被推给他"的正样本，
    这样 Precision/Recall/NDCG 衡量的是**学习路径的完备性**，而不是一个自相矛盾的定义。
    """
    student_id = student["student_id"]
    major = student["major"]
    term = int(student["semester"])
    touched = {
        row["video_id"]
        for row in rows(
            con, "SELECT video_id FROM learning_record WHERE student_id=? AND progress>0", (student_id,)
        )
    }
    completed_courses = {
        row["course_id"]
        for row in rows(
            con,
            """
            SELECT c.course_id FROM learning_record l JOIN video v ON v.video_id=l.video_id
            JOIN course c ON c.course_id=v.course_id
            WHERE l.student_id=? AND l.status='completed'
            """,
            (student_id,),
        )
    }
    target_courses: Dict[str, str] = {}
    for course in rows(con, "SELECT * FROM course WHERE major=? AND semester=?", (major, term)):
        target_courses[course["course_id"]] = f"本学期《{course['name']}》"
    for course in rows(con, "SELECT * FROM course WHERE major=? AND semester=?", (major, term + 1)):
        target_courses.setdefault(course["course_id"], f"下学期《{course['name']}》")
    for course_id in completed_courses:
        for next_course in rows(
            con, "SELECT * FROM course WHERE major=? AND prerequisites LIKE ?", (major, f"%{course_id}%")
        ):
            target_courses.setdefault(next_course["course_id"], f"后继课《{next_course['name']}》")

    positives: Set[str] = set()
    if target_courses:
        marks = ",".join("?" * len(target_courses))
        for row in rows(
            con,
            f"SELECT video_id FROM video WHERE course_id IN ({marks})",
            tuple(target_courses.keys()),
        ):
            if row["video_id"] not in touched:
                positives.add(row["video_id"])
    return {
        "positives": positives,
        "target_course_count": len(target_courses),
        "touched_count": len(touched),
    }


def video_course_index(con: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for row in rows(
        con,
        """
        SELECT v.video_id AS video_id, c.course_id AS course_id, c.name AS course_name,
               c.major AS course_major, c.semester AS semester, c.course_kind AS course_kind
        FROM video v JOIN course c ON v.course_id=c.course_id
        """,
    ):
        index[row["video_id"]] = row
    return index


def evaluate() -> Dict[str, Any]:
    con = connect()
    course_index = video_course_index(con)
    recommended_by_student: Dict[str, List[str]] = {}
    per_student: Dict[str, Any] = {}
    student_accounts = []
    for account in DEMO_ACCOUNTS[:EVAL_ACCOUNT_LIMIT]:
        student_id = account["student_id"]
        student_rows = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))
        if not student_rows:
            continue
        student = student_rows[0]
        result = recommender.recommend_professional(student_id, "all", page_size=10000)
        recs = result["items"] if isinstance(result, dict) else result
        recommended = [item["video_id"] for item in recs]

        pool = expected_pool(con, student)
        positives = pool["positives"]
        metrics = precision_recall_at_k(recommended, positives, 5)
        metrics["ndcg@5"] = ndcg_at_k(recommended, positives, 5)
        # Recall@5 的分母是几百条正样本池，前 5 个位置最多命中 5 条，
        # 数值天然趋近于 0，没有展示价值。改成按"整条推荐链路交付的全部资源"算召回，
        # 衡量的是「该学的资源这一轮铺开覆盖了多少」，这才是有意义的召回口径。
        full_hits = sum(1 for item in recommended if item in positives)
        metrics["recall_full"] = round(full_hits / (len(positives) or 1), 4)
        metrics["positive_count"] = len(positives)
        metrics["full_hits"] = full_hits

        # 培养方案对齐度：推荐是否都落在本专业、且落在培养方案该学期段上
        term = int(student["semester"])
        in_major = current_term = next_term = advanced = 0
        covered_courses: Set[str] = set()
        for video_id in recommended:
            info = course_index.get(video_id)
            if not info:
                continue
            covered_courses.add(info["course_id"])
            if info["course_major"] == student["major"]:
                in_major += 1
                if info["semester"] == term:
                    current_term += 1
                elif info["semester"] == term + 1:
                    next_term += 1
                elif info["semester"] < term:
                    advanced += 1
        total = len(recommended) or 1
        major_course_total = con.execute(
            "SELECT COUNT(*) FROM course WHERE major=?", (student["major"],)
        ).fetchone()[0] or 1
        metrics.update(
            {
                "major_fit": round(in_major / total, 4),
                "current_term_fit": round(current_term / total, 4),
                "next_term_fit": round(next_term / total, 4),
                "advanced_fit": round(advanced / total, 4),
                "covered_course_count": len(covered_courses),
                "course_coverage_rate": round(len(covered_courses) / major_course_total, 4),
            }
        )
        student_accounts.append(
            {
                "student_id": student_id,
                "name": account["name"],
                "major": account["major"],
                "college": account.get("intro", ""),
                "grade": student["grade"],
                "semester": student["semester"],
                "positive_count": len(positives),
                "recommended_count": len(recommended),
                "password": "123456",
            }
        )
        recommended_by_student[student_id] = recommended
        per_student[student_id] = {
            "name": account["name"],
            "major": account["major"],
            "grade": student["grade"],
            **metrics,
        }

    averages = defaultdict(float)
    metric_keys = [
        key
        for key in next(iter(per_student.values())).keys()
        if key.endswith("@5")
        or key in (
            "major_fit", "current_term_fit", "next_term_fit", "advanced_fit",
            "recall_full", "course_coverage_rate",
        )
    ]
    for metrics in per_student.values():
        for key in metric_keys:
            averages[key] += metrics[key]
    averages = {key: round(value / (len(per_student) or 1), 4) for key, value in averages.items()}
    averages["catalog_coverage"] = catalog_coverage(recommended_by_student)
    averages["platform_diversity"] = diversity(recommended_by_student)
    averages["covered_course_count"] = round(
        sum(m["covered_course_count"] for m in per_student.values()) / (len(per_student) or 1), 1
    )

    train_report = json.loads(MODEL_PATH.read_text(encoding="utf-8"))["report"]
    report = {
        "offline_train_report": train_report,
        "leave_one_out": leave_one_out_evaluation(),
        "ranking_eval": {
            "method": (
                "正样本 = 培养方案里「本学期 / 下学期 / 已修课程的直系后继课」三类课程的资源中，"
                "该生尚未接触过的部分，衡量学习路径的完备性。"
                "precision@5 / ndcg@5 / hit_rate@5 看前 5 个位置的排序质量；"
                "recall_full 看整条推荐链路把正样本池覆盖了多少；"
                "major_fit 是推荐落在本专业的比例；course_coverage_rate 是一次推荐覆盖到"
                "本专业培养方案课程门数的比例；covered_course_count 是覆盖到的课程门数。"
            ),
            "averages": averages,
            "per_student": per_student,
        },
        "student_accounts": student_accounts,
        "effect_improvement_plan": [
            "扩大真实曝光日志，按 impression-click-complete 多目标训练，替代当前基于培养方案的弱标注。",
            "按专业、年级、学期分桶评估，避免只优化热门课程（当前已按专业分别出指标）。",
            "对同一门课的资源做视频级去重，同一门课最多占 3 个推荐位。",
            "上线前做 A/B 测试：完成率、点击率、收藏率、学习时长、人均覆盖课程数。",
            "对考研数据按年份做版本评估，避免过期科目误推荐。",
        ],
        "data_quality": quality_report(),
        "major_catalog_size": len(MAJOR_CATALOG),
    }
    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：评估可能跑几分钟，期间 GET /api/admin/model-evaluation 仍在读
    # 这个文件；直接 write_text 覆盖有概率被读到半截 JSON。
    tmp_path = EVAL_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(EVAL_PATH)
    return report


def main() -> None:
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 后台评估
# evaluate() 全程 CPU 密集：每账号 page_size=10000 全量推荐 +
# 留一法每折重训 + 全量质量报告，实测 2 分钟以上且单核跑满。
#
# 关键约束：必须跑在【独立子进程】里，不能只是后台线程。
# 纯 Python 计算会占死 GIL——即使是后台线程，评估期间线程池里
# 所有请求 handler 都会被饿到秒级响应（实测 /api/stats/resources
# 从 20ms 恶化到 3 秒+），前端表现就是"滚动 / 切页全弹『等待响应』"。
# 子进程方案：evaluate() 本身就会把报告原子写入 EVAL_PATH，
# 主服务只需查 EVAL_PATH 的更新时间 + 子进程存活状态。

import subprocess
import sys

_EVAL_LOCK = threading.Lock()
_EVAL_PROC: Any = None
_EVAL_STATE: Dict[str, Any] = {
    "status": "idle",       # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "duration_ms": None,
    "error": None,
}

LOG_PATH = ROOT / "data" / "artifacts" / "evaluate_last.log"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def evaluate_status() -> Dict[str, Any]:
    global _EVAL_STATE, _EVAL_PROC
    with _EVAL_LOCK:
        if _EVAL_STATE["status"] == "running" and _EVAL_PROC is not None:
            code = _EVAL_PROC.poll()
            if code is not None:
                finished = _now()
                duration = round((time.time() - _EVAL_PROC.started_ts) * 1000)
                if code == 0:
                    _EVAL_STATE.update({"status": "done", "finished_at": finished,
                                        "duration_ms": duration, "error": None})
                else:
                    tail = ""
                    try:
                        tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-300:]
                    except Exception:
                        pass
                    _EVAL_STATE.update({"status": "error", "finished_at": finished,
                                        "duration_ms": duration,
                                        "error": f"评估进程退出码 {code}；{tail}"})
                _EVAL_PROC = None
        return dict(_EVAL_STATE)


def evaluate_async() -> Dict[str, Any]:
    """把全量评估丢到独立子进程，立即返回。

    报告由子进程内的 evaluate() 原子写入 EVAL_PATH；
    主服务通过 /api/admin/evaluate-status 轮询子进程状态。
    """
    global _EVAL_PROC, _EVAL_STATE
    with _EVAL_LOCK:
        # 兜底：状态卡在 running 但句柄已丢（主服务重启后），重新放行
        if _EVAL_STATE["status"] == "running" and _EVAL_PROC is not None:
            return dict(_EVAL_STATE, status="already_running")
        root = Path(__file__).resolve().parents[1]
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_PATH, "w", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-m", "backend.evaluate_model"],
            cwd=str(root), stdout=log, stderr=subprocess.STDOUT)
        proc.started_ts = time.time()
        _EVAL_PROC = proc
        _EVAL_STATE.update({
            "status": "running",
            "started_at": _now(),
            "finished_at": None,
            "duration_ms": None,
            "error": None,
        })
    return dict(_EVAL_STATE, status="started")


if __name__ == "__main__":
    main()
