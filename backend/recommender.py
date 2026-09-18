from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "xuetu_lite.db"
MODEL_PATH = ROOT / "data" / "artifacts" / "ranker_model.json"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def rows(con: sqlite3.Connection, sql: str, params=()) -> List[Dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def load_model() -> Dict[str, Any]:
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))


def sigmoid(value: float) -> float:
    if value < -30:
        return 0.0
    if value > 30:
        return 1.0
    return 1 / (1 + math.exp(-value))


def split_csv(value: str) -> List[str]:
    return [part.strip() for part in (value or "").replace("，", ",").split(",") if part.strip()]


def major_alignment(student: Dict[str, Any], video: Dict[str, Any], course: Dict[str, Any]) -> float:
    video_major = (video.get("major") or "").strip()
    course_major = (course.get("major") or "").strip()
    if video_major:
        return 1.0 if video_major == student["major"] else 0.0
    if course_major:
        return 1.0 if course_major == student["major"] else 0.55
    text = " ".join([video.get("title") or "", video.get("tags") or "", course.get("name") or ""])
    if student["major"] in text:
        return 0.9
    return 0.55


def _touched_video_ids(con: sqlite3.Connection, student_id: str, override: Set[str] | None = None) -> Set[str]:
    if override is not None:
        return set(override)
    return {
        row["video_id"]
        for row in rows(
            con,
            "SELECT video_id FROM learning_record WHERE student_id=? AND progress>0",
            (student_id,),
        )
    }


def professional_candidates(
    con: sqlite3.Connection,
    student: Dict[str, Any],
    model: Dict[str, Any],
    exclude_video_ids: Set[str] | None = None,
    context_video_ids: Set[str] | None = None,
) -> Dict[str, str]:
    reasons: Dict[str, str] = {}
    touched = _touched_video_ids(con, student["student_id"], exclude_video_ids)
    context = _touched_video_ids(con, student["student_id"], context_video_ids)
    studying = rows(con, "SELECT * FROM course WHERE status='studying'")
    completed = rows(con, "SELECT * FROM course WHERE status='completed'")
    planned = rows(con, "SELECT * FROM course WHERE semester=?", (student["semester"] + 1,))
    for course in studying:
        for video in rows(con, "SELECT video_id FROM video WHERE course_id=?", (course["course_id"],)):
            if video["video_id"] not in touched:
                reasons.setdefault(video["video_id"], f"正在学习《{course['name']}》，召回同主题视频")
    for done in completed:
        for next_course in rows(con, "SELECT * FROM course WHERE prerequisites LIKE ?", (f"%{done['course_id']}%",)):
            for video in rows(con, "SELECT video_id FROM video WHERE course_id=?", (next_course["course_id"],)):
                if video["video_id"] not in touched:
                    reasons.setdefault(video["video_id"], f"已学《{done['name']}》，推荐后继进阶课")
    for course in planned:
        for video in rows(con, "SELECT video_id FROM video WHERE course_id=?", (course["course_id"],)):
            if video["video_id"] not in touched:
                reasons.setdefault(video["video_id"], f"下学期将学《{course['name']}》，适合假期预习")
    for watched_id in context:
        for item in model["item_similarity"].get(watched_id, [])[:4]:
            if item["video_id"] not in touched:
                reasons.setdefault(item["video_id"], "基于你的学习记录做 ItemCF 相似召回")
    for video in rows(
        con,
        "SELECT video_id FROM video WHERE major=? ORDER BY popularity DESC LIMIT 16",
        (student["major"],),
    ):
        if video["video_id"] not in touched:
            reasons.setdefault(video["video_id"], f"匹配{student['major']}专业方向，优先补齐核心课程")
    incompatible = rows(
        con,
        """
        SELECT DISTINCT video_id FROM video
        WHERE major IS NOT NULL AND major<>?
        """,
        (student["major"],),
    )
    for item in incompatible:
        reasons.pop(item["video_id"], None)
    for video in rows(
        con,
        """
        SELECT video_id FROM video
        WHERE major=? OR major IS NULL OR major=''
        ORDER BY popularity DESC
        LIMIT 10
        """,
        (student["major"],),
    ):
        if video["video_id"] not in touched:
            reasons.setdefault(video["video_id"], "热门优质课程兜底召回")
    return reasons


def feature_vector(
    con: sqlite3.Connection,
    student: Dict[str, Any],
    video: Dict[str, Any],
    reasons: Dict[str, str],
    model: Dict[str, Any],
    history: List[str] | None = None,
) -> List[float]:
    course = rows(con, "SELECT * FROM course WHERE course_id=?", (video["course_id"],))[0]
    if history is None:
        history = [row["video_id"] for row in rows(con, "SELECT video_id FROM learning_record WHERE student_id=? AND progress>0", (student["student_id"],))]
    sim_score = 0.0
    for watched in history:
        for item in model["item_similarity"].get(watched, []):
            if item["video_id"] == video["video_id"]:
                sim_score = max(sim_score, item["score"])
    platform_rows = rows(
        con,
        """
        SELECT v.platform, COUNT(*) AS c
        FROM behavior_log b JOIN video v ON b.video_id=v.video_id
        WHERE b.student_id=? AND b.event_type IN ('play','complete','like','favorite')
        GROUP BY v.platform
        """,
        (student["student_id"],),
    )
    total_platform = sum(row["c"] for row in platform_rows) or 1
    platform_pref = sum(row["c"] for row in platform_rows if row["platform"] == video["platform"]) / total_platform
    reason = reasons.get(video["video_id"], "")
    graph_score = (
        1.0
        if "正在学习" in reason
        else 0.82
        if "后继" in reason
        else 0.78
        if "专业方向" in reason
        else 0.66
        if "下学期" in reason
        else 0.4
    )
    downstream = len(rows(con, "SELECT course_id FROM course WHERE prerequisites LIKE ?", (f"%{course['course_id']}%",))) / 4
    return [
        math.log1p(video["popularity"]) / 14.0,
        float(video["rating"]) / 5.0,
        1.0 - float(video["is_paid"]) * 0.22,
        graph_score,
        min(sim_score, 1.0),
        platform_pref,
        min(downstream, 1.0),
        major_alignment(student, video, course),
    ]


def score(features: List[float], model: Dict[str, Any]) -> float:
    ranker = model["ranker"]
    x = [(features[i] - ranker["means"][i]) / ranker["stdevs"][i] for i in range(len(features))]
    return sigmoid(sum(ranker["weights"][i] * x[i] for i in range(len(x))) + ranker["bias"])


def recall_strength(reason: str) -> float:
    if "正在学习" in reason:
        return 1.0
    if "后继" in reason:
        return 0.86
    if "下学期" in reason:
        return 0.74
    if "ItemCF" in reason:
        return 0.68
    if "专业方向" in reason:
        return 0.78
    return 0.52


def freshness_bonus(video: Dict[str, Any]) -> float:
    # In the seed data, popularity and rating are the most reliable quality
    # fields. The bounded bonus keeps the ranker CPU-light and explainable.
    popularity = math.log1p(video["popularity"]) / 14.0
    rating = float(video["rating"]) / 5.0
    return min(1.0, 0.58 * popularity + 0.42 * rating)


def similarity_between(model: Dict[str, Any], left: str, right: str) -> float:
    for item in model["item_similarity"].get(left, []):
        if item["video_id"] == right:
            return float(item["score"])
    for item in model["item_similarity"].get(right, []):
        if item["video_id"] == left:
            return float(item["score"])
    return 0.0


def mmr_rerank(items: List[Dict[str, Any]], model: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    pool = items[:]
    platform_seen: Dict[str, int] = defaultdict(int)
    while pool and len(selected) < limit:
        best_index = 0
        best_score = -1.0
        for index, item in enumerate(pool):
            redundancy = max(
                [similarity_between(model, item["video_id"], chosen["video_id"]) for chosen in selected] or [0.0]
            )
            platform_penalty = 0.015 * platform_seen[item["platform"]]
            rerank_score = 0.86 * item["score"] - 0.08 * redundancy - platform_penalty
            if rerank_score > best_score:
                best_index = index
                best_score = rerank_score
        chosen = pool.pop(best_index)
        chosen["rerank_score"] = round(best_score, 4)
        platform_seen[chosen["platform"]] += 1
        selected.append(chosen)
    return selected


def video_payload(con: sqlite3.Connection, video: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(video)
    record["is_paid"] = bool(record["is_paid"])
    record["plays"] = f"{record['popularity'] / 10000:.1f}万播放"
    return record


def recommend_professional(
    student_id: str,
    filt: str = "all",
    model_override: Dict[str, Any] | None = None,
    exclude_video_ids: Set[str] | None = None,
    context_video_ids: Set[str] | None = None,
) -> List[Dict[str, Any]]:
    con = connect()
    model = model_override or load_model()
    student = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))[0]
    touched = _touched_video_ids(con, student_id, exclude_video_ids)
    context = _touched_video_ids(con, student_id, context_video_ids)
    reasons = professional_candidates(
        con,
        student,
        model,
        exclude_video_ids=touched,
        context_video_ids=context,
    )
    done_videos = touched
    history = sorted(context)
    scored = []
    for video_id, reason in reasons.items():
        video = rows(con, "SELECT * FROM video WHERE video_id=?", (video_id,))[0]
        if filt == "studying" and "正在学习" not in reason:
            continue
        if filt == "advanced" and "后继" not in reason:
            continue
        if filt == "vacation" and "下学期" not in reason:
            continue
        if video_id in done_videos:
            continue
        features = feature_vector(con, student, video, reasons, model, history=history)
        predicted_ctr = score(features, model)
        blended_score = (
            0.62 * predicted_ctr
            + 0.2 * recall_strength(reason)
            + 0.12 * freshness_bonus(video)
            + 0.06 * features[2]
        )
        item = video_payload(con, video)
        item["predicted_ctr"] = round(predicted_ctr, 4)
        item["score"] = round(blended_score, 4)
        item["reason"] = reason
        item["feature_trace"] = dict(zip(model["report"]["feature_names"], [round(v, 4) for v in features]))
        scored.append(item)
    return mmr_rerank(sorted(scored, key=lambda item: item["score"], reverse=True), model, 9)


def user_profile(student_id: str) -> Dict[str, Any]:
    con = connect()
    student = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))[0]
    courses = rows(con, "SELECT * FROM course ORDER BY semester, course_id")
    achievements = rows(
        con,
        """
        SELECT v.* FROM achievement a JOIN video v ON a.video_id=v.video_id
        WHERE a.student_id=? ORDER BY a.completed_at DESC
        """,
        (student_id,),
    )
    records = rows(con, "SELECT * FROM learning_record WHERE student_id=?", (student_id,))
    return {
        "student": student,
        "courses": courses,
        "achievements": [video_payload(con, item) for item in achievements],
        "records": records,
    }


def jobs_for_student(student_id: str, selected_job_id: str | None = None) -> Dict[str, Any]:
    con = connect()
    student = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))[0]
    jobs = rows(con, "SELECT * FROM job")
    def match_weight(job: Dict[str, Any]) -> int:
        if student["major"] in (job["required_major"] or ""):
            return 3
        if job["major_match"] == "高度匹配":
            return 2
        return 1
    jobs = sorted(jobs, key=match_weight, reverse=True)
    selected = next((job for job in jobs if job["job_id"] == selected_job_id), jobs[0])
    path = path_for_skills(split_csv(selected["skills"]))
    return {"jobs": jobs, "selected": selected, "path": path}


def path_for_skills(skills: List[str]) -> List[Dict[str, Any]]:
    con = connect()
    mappings = rows(con, "SELECT * FROM skill_course_map")
    by_skill = defaultdict(list)
    prereq_graph: Dict[str, Set[str]] = defaultdict(set)
    for row in mappings:
        by_skill[row["skill"]].append(row["video_id"])
        for pre in split_csv(row["prerequisite_skill"]):
            prereq_graph[row["skill"]].add(pre)
    needed: Set[str] = set(skills)
    queue = deque(skills)
    while queue:
        skill = queue.popleft()
        for pre in prereq_graph.get(skill, set()):
            if pre not in needed:
                needed.add(pre)
                queue.append(pre)
    indegree = {skill: 0 for skill in needed}
    children: Dict[str, Set[str]] = defaultdict(set)
    for skill in needed:
        for pre in prereq_graph.get(skill, set()):
            if pre in needed:
                children[pre].add(skill)
                indegree[skill] += 1
    ready = deque(sorted([skill for skill, deg in indegree.items() if deg == 0]))
    ordered = []
    while ready:
        skill = ready.popleft()
        ordered.append(skill)
        for child in sorted(children.get(skill, set())):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    seen_videos = set()
    result = []
    for skill in ordered:
        for video_id in by_skill.get(skill, []):
            if video_id in seen_videos:
                continue
            seen_videos.add(video_id)
            video = rows(con, "SELECT * FROM video WHERE video_id=?", (video_id,))[0]
            item = video_payload(con, video)
            item["skill"] = skill
            result.append(item)
    return result


def exam_recommendations(student_id: str, school: str | None = None, major: str | None = None) -> Dict[str, Any]:
    con = connect()
    student = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))[0]
    school = (school or "默认").strip() or "默认"
    major = (major or student["major"]).strip() or student["major"]
    subject_rows = rows(con, "SELECT * FROM exam_subject WHERE school=? AND major=?", (school, major))
    if not subject_rows:
        subject_rows = rows(
            con,
            "SELECT * FROM exam_subject WHERE major=? ORDER BY year DESC, school",
            (major,),
        )
    subjects = split_csv(subject_rows[0]["subjects"])
    public_keywords = ["政治", "英语", "数学"]
    public_videos = []
    professional_videos = []
    for subject in subjects:
        terms = subject.replace("408", "考研 408").replace("912", "数据结构 操作系统 计算机网络").split()
        query = " ".join([subject] + terms)
        matches = rows(
            con,
            "SELECT * FROM video WHERE title LIKE ? OR tags LIKE ? ORDER BY popularity DESC LIMIT 3",
            (f"%{subject[:4]}%", f"%{subject.split()[0]}%"),
        )
        if not matches:
            matches = rows(con, "SELECT * FROM video WHERE tags LIKE ? ORDER BY popularity DESC LIMIT 3", (f"%{query[:2]}%",))
        for video in matches[:2]:
            item = video_payload(con, video)
            item["subject"] = subject
            if any(key in subject for key in public_keywords):
                public_videos.append(item)
            else:
                professional_videos.append(item)
    # Stable fallback for 408/912 style professional subjects.
    if not professional_videos:
        for vid in ("V015", "V016", "V017"):
            professional_videos.append(video_payload(con, rows(con, "SELECT * FROM video WHERE video_id=?", (vid,))[0]))
    return {
        "school": subject_rows[0]["school"],
        "major": subject_rows[0]["major"],
        "subjects": subjects,
        "public": dedupe(public_videos),
        "professional": dedupe(professional_videos),
    }


def dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        if item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        out.append(item)
    return out


def video_detail(video_id: str, student_id: str = "20240101") -> Dict[str, Any]:
    con = connect()
    video = rows(con, "SELECT * FROM video WHERE video_id=?", (video_id,))[0]
    episodes = rows(con, "SELECT * FROM video_episode WHERE video_id=? ORDER BY episode_no", (video_id,))
    record = rows(con, "SELECT * FROM learning_record WHERE student_id=? AND video_id=?", (student_id, video_id))
    payload = video_payload(con, video)
    payload["episodes_list"] = episodes
    payload["record"] = record[0] if record else {"watched_episodes": 0, "progress": 0, "status": "new"}
    return payload
