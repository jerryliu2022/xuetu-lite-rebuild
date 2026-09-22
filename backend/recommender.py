"""推荐引擎：多路召回 -> 逻辑回归排序 -> MMR 多样性重排。

数据底座换成岳阳学院的培养方案之后，召回逻辑做了两处关键修正：

1. **召回按"专业 + 学期"定位**（原实现是全局取 status='studying' 的课程，
   等于所有学生看到同一批课）。现在：
   - 本学期课程  -> 同课程资源召回（学生正在上的课）
   - 往期学期课程 -> 先修后继召回（已经学过的课，推它后面那门）
   - 下学期课程  -> 提前预习召回（寒暑假场景）
   这三路都严格限定在 `course.major = student.major` 之内。

2. **打分过程去掉重复查询**（原实现里平台偏好在每个候选上重复查一次库）。
   现在用一个 `ScoringContext` 把 ItemCF 相似度、平台偏好、课程后继价值
   全部预计算并缓存，候选数从几十涨到两百多，单次请求反而更快。

特征维度严格保持 8 维不变，`ranker_model.json` 里的权重与均值方差继续可用。
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import db as _db

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = _db.DB_PATH
MODEL_PATH = ROOT / "data" / "artifacts" / "ranker_model.json"

FEATURE_NAMES = [
    "log_popularity", "rating", "free_access", "curriculum_graph",
    "itemcf_similarity", "platform_preference", "course_downstream_value",
    "major_alignment",
]

PAGE_SIZE = 24            # 一行 4 个，共 6 行

# 每门课进候选池的资源条数，按课程类型分档。
# 通识必修（思政/英语/体育）资源量很大但和"专业路径"关系弱，
# 不限制的话会靠"本学期正在学"的满分召回强度把专业核心课全挤下去。
RESOURCE_QUOTA_BY_KIND = {"专业核心": 8, "学科基础": 5, "通识必修": 2}

# 召回理由强度上的课程类型系数：专业路径推荐以专业核心课为主轴。
KIND_STRENGTH = {"专业核心": 1.0, "学科基础": 0.90, "通识必修": 0.68}


def connect() -> sqlite3.Connection:
    return _db.connection()


def rows(con: sqlite3.Connection, sql: str, params=()) -> List[Dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def _file_stamp(path: Path) -> Tuple[int, int]:
    """(mtime_ns, size)。任何一次写入都会改变它，用来给进程内缓存做失效判断。"""
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


# ranker_model.json 有 2 MB（其中 tfidf.vectors 占绝大部分），
# 每次请求都重新 json.loads 要 57 ms。模型是只读的，按文件指纹缓存。
# train_model / /api/admin/* 重训会改写文件，指纹随之变化，缓存自动失效。
_MODEL_CACHE: Dict[str, Any] = {"stamp": None, "model": None}

# 课程后继价值只依赖 course 表，全量 898×898 的 LIKE 相关子查询要 179 ms，
# 同样按 (库文件指纹, course 表指纹) 缓存。
_DOWNSTREAM_CACHE: Dict[str, Any] = {"stamp": None, "value": None}


def load_model() -> Dict[str, Any]:
    stamp = _file_stamp(MODEL_PATH)
    if _MODEL_CACHE["stamp"] != stamp or _MODEL_CACHE["model"] is None:
        _MODEL_CACHE["model"] = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        _MODEL_CACHE["stamp"] = stamp
    return _MODEL_CACHE["model"]


def downstream_counts(con: sqlite3.Connection) -> Dict[str, int]:
    """每门课被多少门课写进了 prerequisites，用于 course_downstream_value 特征。

    `prerequisites` 是逗号分隔的课程号（如 "C201,C202"），所以用子串匹配即可。
    结果只跟 course 表有关，按库指纹缓存。
    """
    course_stamp = con.execute(
        """SELECT COUNT(*) AS n,
                  COALESCE(SUM(LENGTH(COALESCE(prerequisites, ''))), 0) AS s
           FROM course"""
    ).fetchone()
    stamp = (_file_stamp(DB_PATH), int(course_stamp[0]), int(course_stamp[1]))
    if _DOWNSTREAM_CACHE["stamp"] != stamp or _DOWNSTREAM_CACHE["value"] is None:
        _DOWNSTREAM_CACHE["value"] = {
            row["course_id"]: row["c"]
            for row in rows(
                con,
                """
                SELECT c.course_id AS course_id,
                       (SELECT COUNT(*) FROM course x WHERE x.prerequisites LIKE '%' || c.course_id || '%') AS c
                FROM course c WHERE c.course_id LIKE 'YY%'
                """,
            )
        }
        _DOWNSTREAM_CACHE["stamp"] = stamp
    return _DOWNSTREAM_CACHE["value"]


def load_videos(con: sqlite3.Connection, video_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """按 id 批量取视频行。

    原实现是 `for vid in candidates: SELECT * FROM video WHERE video_id=?`，
    一次推荐要跑 114 次往返（28.7 ms）；这里按 400 个一批 IN 查询，实测 1.2 ms。
    """
    ids = list(dict.fromkeys(video_ids))
    found: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        marks = ",".join("?" * len(chunk))
        for row in rows(con, f"SELECT * FROM video WHERE video_id IN ({marks})", chunk):
            found[row["video_id"]] = row
    return found


def sigmoid(value: float) -> float:
    if value < -30:
        return 0.0
    if value > 30:
        return 1.0
    return 1 / (1 + math.exp(-value))


def split_csv(value: str) -> List[str]:
    return [part.strip() for part in (value or "").replace("，", ",").split(",") if part.strip()]


def cover_url(video_id: str) -> str:
    return f"/api/cover/{video_id}.svg"


def major_alignment(student: Dict[str, Any], video: Dict[str, Any], course: Dict[str, Any]) -> float:
    student_major = (student.get("major") or "").strip()
    video_major = (video.get("major") or "").strip()
    course_major = (course.get("major") or "").strip()
    if video_major:
        return 1.0 if video_major == student_major else 0.0
    if course_major:
        return 1.0 if course_major == student_major else 0.55
    text = " ".join([video.get("title") or "", video.get("tags") or "", course.get("name") or ""])
    return 0.9 if student_major and student_major in text else 0.5


def _touched_video_ids(con: sqlite3.Connection, student_id: str, override: Optional[Set[str]] = None) -> Set[str]:
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


# ---------------------------------------------------------------- 打分上下文

class ScoringContext:
    """把打分需要的跨候选公共量预计算好，避免每个候选重复查库。"""

    def __init__(
        self,
        con: sqlite3.Connection,
        student: Dict[str, Any],
        model: Dict[str, Any],
        history: Iterable[str],
    ) -> None:
        self.con = con
        self.student = student
        self.model = model
        self.history = list(history)
        self._sim = self._build_similarity()

        # 平台偏好：一次聚合，后面直接查表
        platform_rows = rows(
            con,
            """
            SELECT v.platform AS platform, COUNT(*) AS c
            FROM behavior_log b JOIN video v ON b.video_id=v.video_id
            WHERE b.student_id=? AND b.event_type IN ('play','complete','like','favorite')
            GROUP BY v.platform
            """,
            (student["student_id"],),
        )
        total = sum(row["c"] for row in platform_rows) or 1
        self.platform_pref = {row["platform"]: row["c"] / total for row in platform_rows}
        self.platform_pref_avg = (1 / len(self.platform_pref)) if self.platform_pref else 0.0

        # 课程后继价值：每门课被多少门课当先修。这条 898×898 的 LIKE 相关子查询
        # 原本每次请求都要 179 ms，改成按库指纹缓存的模块级计算（见 downstream_counts）。
        self._downstream = downstream_counts(con)
        self._course_cache: Dict[str, Dict[str, Any]] = {}
        self._stats_cache: Dict[str, Dict[str, Any]] = {}

    def prime_courses(self, course_ids: Iterable[str]) -> None:
        """把候选用到的课程一次 IN 查询预热进缓存，替代逐门课 SELECT。

        原来 distinct 课程各查一次（一次推荐 32 次），现在一次取齐。
        查不到的课程号仍然落成占位记录，保持原来 `course()` 的兜底行为。
        """
        missing = [cid for cid in dict.fromkeys(course_ids) if cid and cid not in self._course_cache]
        for start in range(0, len(missing), 400):
            chunk = missing[start:start + 400]
            marks = ",".join("?" * len(chunk))
            for row in rows(self.con, f"SELECT * FROM course WHERE course_id IN ({marks})", chunk):
                self._course_cache[row["course_id"]] = row
        for cid in missing:
            self._course_cache.setdefault(cid, {
                "course_id": cid, "name": cid, "major": "", "semester": 0,
                "credits": 0, "course_kind": "", "prerequisites": "",
            })

    def _build_similarity(self) -> Dict[str, float]:
        """history 里的课对每个候选的历史最高相似度，只在被打分时惰性填。"""
        return {}

    def similarity(self, candidate_id: str) -> float:
        if candidate_id in self._sim:
            return self._sim[candidate_id]
        best = 0.0
        for watched in self.history:
            for item in self.model["item_similarity"].get(watched, []):
                if item["video_id"] == candidate_id:
                    best = max(best, float(item["score"]))
        self._sim[candidate_id] = best
        return best

    def course(self, course_id: str) -> Dict[str, Any]:
        if course_id not in self._course_cache:
            found = rows(self.con, "SELECT * FROM course WHERE course_id=?", (course_id,))
            self._course_cache[course_id] = found[0] if found else {
                "course_id": course_id, "name": course_id, "major": "", "semester": 0,
                "credits": 0, "course_kind": "", "prerequisites": "",
            }
        return self._course_cache[course_id]

    def downstream_value(self, course_id: str) -> float:
        return min(self._downstream.get(course_id, 0) / 4.0, 1.0)

    def platform_value(self, platform: str) -> float:
        return self.platform_pref.get(platform, self.platform_pref_avg)


# ---------------------------------------------------------------- 召回

def professional_candidates(
    con: sqlite3.Connection,
    student: Dict[str, Any],
    context: ScoringContext,
    exclude_video_ids: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """按"本专业 + 学期"生成候选与召回理由。"""
    student_id = student["student_id"]
    major = student["major"]
    term = int(student["semester"])
    touched = _touched_video_ids(con, student_id, exclude_video_ids)
    reasons: Dict[str, str] = {}

    def quota(course: Dict[str, Any], scale: float = 1.0) -> int:
        base = RESOURCE_QUOTA_BY_KIND.get(course.get("course_kind") or "", 3)
        return max(1, int(round(base * scale)))

    def add_course_videos(course: Dict[str, Any], reason: str, scale: float = 1.0) -> None:
        picked = rows(
            con,
            """
            SELECT video_id FROM video WHERE course_id=?
            ORDER BY popularity DESC LIMIT ?
            """,
            (course["course_id"], quota(course, scale)),
        )
        for video in picked:
            if video["video_id"] not in touched:
                reasons.setdefault(video["video_id"], reason)

    # 路 1：本学期正在学的课（专业核心优先，通识必修只取少量）
    current = rows(
        con, "SELECT * FROM course WHERE major=? AND semester=? ORDER BY credits DESC", (major, term)
    )
    for course in sorted(current, key=lambda c: -KIND_STRENGTH.get(c.get("course_kind") or "", 0.5)):
        add_course_videos(course, f"正在学习《{course['name']}》，补充同课程资源")

    # 路 2：往期已学的专业基础/核心课 -> 后继进阶课
    for done in rows(
        con,
        """SELECT * FROM course WHERE major=? AND semester<?
           AND course_kind IN ('学科基础','专业核心')""",
        (major, term),
    ):
        for next_course in rows(
            con, "SELECT * FROM course WHERE major=? AND prerequisites LIKE ?", (major, f"%{done['course_id']}%")
        ):
            add_course_videos(
                next_course, f"已学《{done['name']}》，推荐后继进阶课《{next_course['name']}》", scale=0.6
            )

    # 路 3：下学期课程 -> 假期预习
    for course in rows(con, "SELECT * FROM course WHERE major=? AND semester=?", (major, term + 1)):
        add_course_videos(course, f"下学期将学《{course['name']}》，适合假期预习", scale=0.85)

    # 路 4：ItemCF 相似召回（基于真实学习记录）
    for watched_id in touched:
        for item in context.model["item_similarity"].get(watched_id, [])[:4]:
            if item["video_id"] not in touched:
                reasons.setdefault(item["video_id"], "基于你的学习记录做 ItemCF 相似召回")

    # 路 5：本专业热门补位。专业核心课优先取样，通识课只在候选取不满时补位，
    # 避免"大学英语"这类全校公共课靠播放量挤占专业路径的推荐位。
    for kind, limit in (("专业核心", 24), ("学科基础", 12), ("通识必修", 4)):
        if len(reasons) >= 260:
            break
        for video in rows(
            con,
            """
            SELECT v.video_id FROM video v JOIN course c ON v.course_id=c.course_id
            WHERE v.major=? AND c.course_kind=?
            ORDER BY v.popularity DESC LIMIT ?
            """,
            (major, kind, limit),
        ):
            if video["video_id"] not in touched:
                reasons.setdefault(video["video_id"], f"匹配{major}培养方案，优先补齐高热度课程资源")

    return reasons


def feature_vector(
    student: Dict[str, Any],
    video: Dict[str, Any],
    course: Dict[str, Any],
    reason: str,
    context: ScoringContext,
) -> List[float]:
    return [
        math.log1p(video["popularity"]) / 14.0,
        float(video["rating"]) / 5.0,
        1.0 - float(video["is_paid"]) * 0.22,
        graph_score(reason),
        context.similarity(video["video_id"]),
        context.platform_value(video["platform"]),
        context.downstream_value(course["course_id"]),
        major_alignment(student, video, course),
    ]


def graph_score(reason: str) -> float:
    if "正在学习" in reason:
        return 1.0
    if "后继" in reason:
        return 0.82
    if "培养方案" in reason:
        return 0.78
    if "下学期" in reason:
        return 0.66
    if "ItemCF" in reason:
        return 0.58
    return 0.4


def score(features: List[float], model: Dict[str, Any]) -> float:
    ranker = model["ranker"]
    x = [(features[i] - ranker["means"][i]) / ranker["stdevs"][i] for i in range(len(features))]
    return sigmoid(sum(ranker["weights"][i] * x[i] for i in range(len(x))) + ranker["bias"])


def recall_strength(reason: str, course_kind: str = "") -> float:
    if "正在学习" in reason:
        base = 1.0
    elif "后继" in reason:
        base = 0.86
    elif "培养方案" in reason:
        base = 0.78
    elif "下学期" in reason:
        base = 0.74
    elif "ItemCF" in reason:
        base = 0.68
    else:
        base = 0.52
    # 专业路径推荐以专业核心课为主轴：同样强度的召回理由，通识课排位更低
    return base * KIND_STRENGTH.get(course_kind, 0.85)


def freshness_bonus(video: Dict[str, Any]) -> float:
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
    """相关性 + 课程内去重 + 平台分散。同一门课最多占 3 个位置。

    冗余度用「对已选项的历史最大值」增量维护，而不是每一轮都把全部已选项重新
    求一遍 max。max 满足结合律，两者结果完全一致（尚未选出任何项时按 0.0 计，
    与原来的 `max([...] or [0.0])` 相同），但把每轮 O(已选数) 的相似度扫描摊掉，
    整体从 O(n^3) 降到 O(n^2)：114 个候选的重排由 128 ms 降到约 5 ms。
    """
    selected: List[Dict[str, Any]] = []
    pool = items[:]
    platform_seen: Dict[str, int] = defaultdict(int)
    course_seen: Dict[str, int] = defaultdict(int)
    # None 表示「还没有任何已选项参与过冗余度计算」，等价于原实现的 0.0 初值
    redundancy: List[Optional[float]] = [None] * len(pool)
    while pool and len(selected) < limit:
        best_index = 0
        best_score = -1.0
        for index, item in enumerate(pool):
            current = redundancy[index]
            redundancy_value = 0.0 if current is None else current
            platform_penalty = 0.015 * platform_seen[item["platform"]]
            course_penalty = 0.06 * course_seen[item["course_id"]]
            course_penalty += 0.25 if course_seen[item["course_id"]] >= 3 else 0.0
            rerank_score = (
                0.86 * item["score"] - 0.08 * redundancy_value - platform_penalty - course_penalty
            )
            if rerank_score > best_score:
                best_index = index
                best_score = rerank_score
        chosen = pool.pop(best_index)
        redundancy.pop(best_index)
        chosen["rerank_score"] = round(best_score, 4)
        platform_seen[chosen["platform"]] += 1
        course_seen[chosen["course_id"]] += 1
        selected.append(chosen)
        if pool:
            chosen_id = chosen["video_id"]
            for index, item in enumerate(pool):
                similarity = similarity_between(model, item["video_id"], chosen_id)
                if redundancy[index] is None or similarity > redundancy[index]:
                    redundancy[index] = similarity
    return selected


def video_payload(con: sqlite3.Connection, video: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(video)
    record["is_paid"] = bool(record["is_paid"])
    popularity = int(record.get("popularity") or 0)
    record["plays"] = (
        f"{popularity / 10000:.1f}万播放" if popularity >= 10000 else f"{popularity}播放"
    )
    # 真实采集的视频带真封面图；没有时回退到生成的 SVG（离线可渲染）
    record["cover_url"] = record.get("cover_url") or cover_url(record["video_id"])
    record["likes"] = f"{int(record.get('like_count') or 0) / 10000:.1f}万点赞"
    record["data_origin"] = record.get("data_origin") or "curriculum"
    return record


# ---------------------------------------------------------------- 专业路径推荐

def recommend_professional(
    student_id: str,
    filt: str = "all",
    model_override: Optional[Dict[str, Any]] = None,
    exclude_video_ids: Optional[Set[str]] = None,
    page_size: int = PAGE_SIZE,
) -> Dict[str, Any]:
    con = connect()
    try:
        model = model_override or load_model()
        found = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))
        if not found:
            return {"items": [], "total": 0, "page_size": page_size, "student": None,
                    "filters": {}, "reason_group_count": {}, "message": "账号不存在"}
        student = found[0]
        touched = _touched_video_ids(con, student_id, exclude_video_ids)
        context = ScoringContext(con, student, model, touched)
        reasons = professional_candidates(con, student, context, exclude_video_ids=touched)

        # 候选视频一次批量取回 + 课程缓存一次预热。
        # 原实现是循环里逐条 `SELECT * FROM video WHERE video_id=?`（114 次）
        # 加逐门课 `SELECT * FROM course WHERE course_id=?`（32 次）。
        candidates = [vid for vid in reasons if vid not in touched]
        video_map = load_videos(con, candidates)
        context.prime_courses(video["course_id"] for video in video_map.values())

        scored = []
        for video_id, reason in reasons.items():
            if video_id in touched:
                continue
            video = video_map.get(video_id)
            if not video:
                continue
            course = context.course(video["course_id"])
            features = feature_vector(student, video, course, reason, context)
            predicted_ctr = score(features, model)
            kind = course.get("course_kind") or ""
            # 课程推荐场景下，培养方案的路径正确性比短期点击偏好更重要，
            # 所以把召回理由强度的权重提到 0.32（原 0.20），模型分降到 0.50。
            # 否则一次"高播放量的通识课外包资源"就能盖过"下学期该学的专业课"。
            blended = (
                0.50 * predicted_ctr
                + 0.32 * recall_strength(reason, kind)
                + 0.12 * freshness_bonus(video)
                + 0.06 * features[2]
            )
            item = video_payload(con, video)
            item["predicted_ctr"] = round(predicted_ctr, 4)
            item["score"] = round(blended, 4)
            item["reason"] = reason
            item["course_name"] = course["name"]
            item["course_semester"] = course["semester"]
            item["course_kind"] = course.get("course_kind") or ""
            item["course_credits"] = course.get("credits")
            item["feature_trace"] = dict(zip(FEATURE_NAMES, [round(v, 4) for v in features]))
            scored.append(item)

        ranked = mmr_rerank(sorted(scored, key=lambda i: i["score"], reverse=True), model, len(scored))

        filter_counts = {
            "all": len(ranked),
            "studying": sum(1 for i in ranked if "正在学习" in i["reason"]),
            "advanced": sum(1 for i in ranked if "后继" in i["reason"]),
            "vacation": sum(1 for i in ranked if "下学期" in i["reason"]),
        }
        if filt and filt != "all":
            key = {"studying": "正在学习", "advanced": "后继", "vacation": "下学期"}.get(filt)
            if key:
                ranked = [i for i in ranked if key in i["reason"]]

        major_total = con.execute(
            "SELECT COUNT(*) FROM video WHERE major=?", (student["major"],)
        ).fetchone()[0]
        return {
            "student": student,
            "items": ranked[:page_size],
            "total": len(ranked),
            "returned": min(page_size, len(ranked)),
            "page_size": page_size,
            "filters": filter_counts,
            "major_resource_total": major_total,
            "reason_group_count": {
                "本学期同课程": filter_counts["studying"],
                "往期课程后继": filter_counts["advanced"],
                "下学期预习": filter_counts["vacation"],
                "其他召回": len(ranked) - filter_counts["studying"] - filter_counts["advanced"] - filter_counts["vacation"],
            },
        }
    finally:
        con.close()


# ---------------------------------------------------------------- 资源统计

def major_resource_stats(student_id: str = "", major: Optional[str] = None) -> Dict[str, Any]:
    """每个专业每门课的课程资源数，以及各平台在每个专业下的课程数。

    `major` 显式指定优先级最高（数据与模型页可切换专业查看），
    否则按 student_id 反查学生专业，最后兜底取第一个专业。
    """
    con = connect()
    try:
        student_major = (major or "").strip()
        if not student_major and student_id:
            found = rows(con, "SELECT major FROM student WHERE student_id=?", (student_id,))
            student_major = found[0]["major"] if found else ""
        if not student_major:
            first = rows(con, "SELECT name FROM major ORDER BY major_id LIMIT 1")
            student_major = first[0]["name"] if first else ""

        # 每门课的资源总数与分平台数：原实现是 6 路相关子查询 × 每门课（本专业 36 门课
        # 共 216 次索引查找），改成一次 (course_id, platform) 分组后按平台摊到课程行上。
        course_rows = rows(
            con,
            """
            SELECT c.course_id, c.name, c.semester, c.credits, c.course_kind
            FROM course c WHERE c.major=? ORDER BY c.semester, c.name
            """,
            (student_major,),
        )
        platform_counts = rows(
            con,
            """
            SELECT v.course_id AS course_id, v.platform AS platform, COUNT(*) AS count
            FROM video v JOIN course c ON v.course_id=c.course_id
            WHERE c.major=?
            GROUP BY v.course_id, v.platform
            """,
            (student_major,),
        )
        platform_key = {
            "B站": "bilibili", "MOOC": "mooc", "极客时间": "geektime",
            "学堂在线": "xuetangx", "网易云课堂": "netease",
        }
        pivot: Dict[str, Dict[str, int]] = {}
        for row in platform_counts:
            slot = pivot.setdefault(row["course_id"], {})
            slot["resource_count"] = slot.get("resource_count", 0) + row["count"]
            key = platform_key.get(row["platform"])
            if key:
                slot[key] = slot.get(key, 0) + row["count"]
        courses = [
            {
                "course_id": course["course_id"],
                "name": course["name"],
                "semester": course["semester"],
                "credits": course["credits"],
                "course_kind": course["course_kind"],
                "resource_count": pivot.get(course["course_id"], {}).get("resource_count", 0),
                "bilibili": pivot.get(course["course_id"], {}).get("bilibili", 0),
                "mooc": pivot.get(course["course_id"], {}).get("mooc", 0),
                "geektime": pivot.get(course["course_id"], {}).get("geektime", 0),
                "xuetangx": pivot.get(course["course_id"], {}).get("xuetangx", 0),
                "netease": pivot.get(course["course_id"], {}).get("netease", 0),
            }
            for course in course_rows
        ]
        by_platform = rows(
            con,
            """SELECT platform, COUNT(*) AS c FROM video WHERE major=?
               GROUP BY platform ORDER BY c DESC""",
            (student_major,),
        )
        # 各专业资源总量：一条 GROUP BY major 顶掉原来的 25 次相关子查询。
        # ⚠️ 口径必须与顶部「切换专业」摘要一致：都按 course 表 join 统计，
        # 不能用 video.major 冗余字段——后者会把挂在公共课编号（C101~C602，
        # course 表无对应课程记录）下的视频也算进来，且 major 表的 course_count
        # 是种子声明的计划门数，与 course 表实际行数（含自动生成的「综合提升」课）差 1，
        # 同页两处数字对不上（36/1880 vs 37/1811）。
        videos_per_major = {
            row["major"]: row["count"]
            for row in rows(
                con,
                """SELECT c.major AS major, COUNT(*) AS count
                   FROM video v JOIN course c ON v.course_id=c.course_id
                   WHERE c.major IS NOT NULL GROUP BY c.major""",
            )
        }
        course_rows_per_major = {
            row["major"]: row["n"]
            for row in rows(con, "SELECT major, COUNT(*) AS n FROM course GROUP BY major")
        }
        majors = rows(
            con,
            """SELECT m.major_id, m.name, m.college, m.category, m.course_count
               FROM major m ORDER BY m.major_id""",
        )
        for item in majors:
            item["course_count"] = course_rows_per_major.get(item["name"], item["course_count"])
            item["resource_count"] = videos_per_major.get(item["name"], 0)
        total_resources = con.execute("SELECT COUNT(*) FROM video").fetchone()[0]
        return {
            "major": student_major,
            "courses": courses,
            "course_count": len(courses),
            "course_resource_total": sum(c["resource_count"] for c in courses),
            "by_platform": by_platform,
            "majors": majors,
            "major_count": len(majors),
            "total_resources": total_resources,
            "avg_resource_per_course": round(
                sum(c["resource_count"] for c in courses) / len(courses), 1
            ) if courses else 0,
        }
    finally:
        con.close()


# ---------------------------------------------------------------- 学生画像

def user_profile(student_id: str) -> Dict[str, Any]:
    con = connect()
    try:
        found = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))
        if not found:
            return {"student": None, "courses": [], "achievements": [], "records": []}
        student = found[0]
        courses = rows(
            con, "SELECT * FROM course WHERE major=? ORDER BY semester, course_id", (student["major"],)
        )
        achievements = rows(
            con,
            """
            SELECT v.* FROM achievement a JOIN video v ON a.video_id=v.video_id
            WHERE a.student_id=? ORDER BY a.completed_at DESC
            """,
            (student_id,),
        )
        records = rows(con, "SELECT * FROM learning_record WHERE student_id=?", (student_id,))
        major_rows = rows(con, "SELECT * FROM major WHERE name=?", (student["major"],))
        term = int(student["semester"])
        return {
            "student": student,
            "major_info": major_rows[0] if major_rows else None,
            "courses": courses,
            "current_term_courses": [c for c in courses if c["semester"] == term],
            "next_term_courses": [c for c in courses if c["semester"] == term + 1],
            "achievements": [video_payload(con, item) for item in achievements],
            "records": records,
        }
    finally:
        con.close()


# ---------------------------------------------------------------- 就业 / 考研

def jobs_for_student(student_id: str, selected_job_id: Optional[str] = None) -> Dict[str, Any]:
    """岗位池按学生专业收敛：专业对口岗位优先，再按匹配档位排序。

    岗位总量有 150 条（25 个专业各 6 条），一次性铺到页面上没有意义，
    所以这里只返回「本专业 + 同簇延伸」的岗位，并把匹配档位动态算出来。
    """
    from .job_market import CLUSTERS, CLUSTER_OF, match_level

    con = connect()
    try:
        found = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))
        student = found[0] if found else {"major": "", "student_id": student_id}
        major = student.get("major") or ""
        total_jobs = con.execute("SELECT COUNT(*) FROM job").fetchone()[0]

        # 本专业 + 同簇专业的岗位都进候选，跨簇的不进（避免给中文系推芯片岗位）
        cluster = CLUSTER_OF.get(major)
        peers = CLUSTERS.get(cluster, []) if cluster else [major]
        pool: List[Dict[str, Any]] = []
        for peer in [major] + [p for p in peers if p != major]:
            pool.extend(rows(con, "SELECT * FROM job WHERE major=? ORDER BY job_id", (peer,)))

        for job in pool:
            job["major_match"] = match_level(major, job.get("major") or "")
            job["matched_major"] = job.get("major") or ""
        rank = {"高度匹配": 0, "一般匹配": 1, "相关延伸": 2}
        pool.sort(key=lambda job: (rank.get(job["major_match"], 3), job["job_id"]))

        selected = next((job for job in pool if job["job_id"] == selected_job_id), pool[0] if pool else None)
        path = path_for_skills(split_csv(selected["skills"]), major) if selected else []
        return {
            "jobs": pool,
            "selected": selected,
            "path": path,
            "student": student,
            "total_jobs": total_jobs,
            "major_job_count": sum(1 for job in pool if job["major_match"] == "高度匹配"),
            "cluster": cluster or major,
        }
    finally:
        con.close()


def path_for_skills(skills: List[str], major: str = "") -> List[Dict[str, Any]]:
    """技能 -> 课程资源，并按技能先修关系做拓扑排序。

    `major` 必须传：skill_course_map 是全局表，同一个技能名在不同专业会映射到
    不同课程的视频，不过滤就会把别的专业的资源混进学习路径里。
    """
    con = connect()
    try:
        if major:
            mappings = rows(
                con,
                """
                SELECT m.* FROM skill_course_map m
                JOIN video v ON m.video_id = v.video_id
                WHERE v.major = ?
                """,
                (major,),
            )
        else:
            mappings = rows(con, "SELECT * FROM skill_course_map")
        by_skill: Dict[str, List[str]] = defaultdict(list)
        prereq_graph: Dict[str, Set[str]] = defaultdict(set)
        for row in mappings:
            by_skill[row["skill"]].append(row["video_id"])
            for pre in split_csv(row["prerequisite_skill"]):
                prereq_graph[row["skill"]].add(pre)
        needed: Set[str] = {skill for skill in skills if skill in by_skill}
        queue = deque(needed)
        while queue:
            skill = queue.popleft()
            for pre in prereq_graph.get(skill, set()):
                if pre not in needed and pre in by_skill:
                    needed.add(pre)
                    queue.append(pre)
        indegree = {skill: 0 for skill in needed}
        children: Dict[str, Set[str]] = defaultdict(set)
        for skill in needed:
            for pre in prereq_graph.get(skill, set()):
                if pre in needed:
                    children[pre].add(skill)
                    indegree[skill] += 1
        ready = deque(sorted(skill for skill, deg in indegree.items() if deg == 0))
        ordered: List[str] = []
        while ready:
            skill = ready.popleft()
            ordered.append(skill)
            for child in sorted(children.get(skill, set())):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        seen_videos: Set[str] = set()
        result: List[Dict[str, Any]] = []
        for skill in ordered:
            for video_id in by_skill.get(skill, []):
                if video_id in seen_videos:
                    continue
                seen_videos.add(video_id)
                video_rows = rows(con, "SELECT * FROM video WHERE video_id=?", (video_id,))
                if not video_rows:
                    continue
                item = video_payload(con, video_rows[0])
                item["skill"] = skill
                result.append(item)
        return result
    finally:
        con.close()


EXAM_SUBJECT_RULES: Dict[str, Dict[str, str]] = {
    "理工类": {"english": "考研英语（一）", "math": "考研数学（一）"},
    "经管类": {"english": "考研英语（二）", "math": "考研数学（三）"},
    "文史类": {"english": "考研英语（一）", "math": ""},
    "艺术体育类": {"english": "考研英语（二）", "math": ""},
}

# 湖南研招单位按专业大类的大致匹配（用于展示"可报考院校"）
HUNAN_UNITS = [
    "湖南理工学院", "湖南大学", "中南大学", "湘潭大学", "长沙理工大学",
    "湖南师范大学", "南华大学", "湖南科技大学", "吉首大学", "湖南工商大学",
    "中南林业科技大学", "湖南农业大学", "湖南工业大学", "长沙学院",
]


def exam_path_for_major(student_id: str, school: Optional[str] = None, major: Optional[str] = None) -> Dict[str, Any]:
    """按专业生成考研路径：公共课 + 专业课，专业课取自培养方案的学位课程。"""
    con = connect()
    try:
        found = rows(con, "SELECT * FROM student WHERE student_id=?", (student_id,))
        student = found[0] if found else {"major": "", "semester": 3, "student_id": student_id}
        target_major = (major or student["major"] or "").strip()
        major_rows = rows(con, "SELECT * FROM major WHERE name=?", (target_major,))
        if not major_rows:
            major_rows = rows(con, "SELECT * FROM major ORDER BY major_id LIMIT 1")
        if not major_rows:
            return {"school": school or "默认", "major": target_major, "subjects": [],
                    "public": [], "professional": [], "schools": [], "note": "暂无专业数据"}
        major_row = major_rows[0]
        category = major_row["category"]
        rule = EXAM_SUBJECT_RULES.get(category, EXAM_SUBJECT_RULES["理工类"])

        subjects = ["考研政治", rule["english"]]
        if rule["math"]:
            subjects.append(rule["math"])

        # 专业课：取培养方案里第 5 学期及以后的专业核心课，作为考研专业课候选
        degree_courses = rows(
            con,
            """SELECT name FROM course WHERE major=? AND course_kind='专业核心' AND semester>=5
               ORDER BY credits DESC, semester LIMIT 3""",
            (major_row["name"],),
        )
        for row in degree_courses:
            subjects.append(f"{row['name']}（专业课）")

        school_name = (school or "").strip() or "湖南理工学院"
        schools = [school_name] + [u for u in HUNAN_UNITS if u != school_name][:7]

        public_videos: List[Dict[str, Any]] = []
        professional_videos: List[Dict[str, Any]] = []
        for subject in subjects:
            keyword = subject.replace("考研", "").replace("（专业课）", "").strip()
            matches = rows(
                con,
                """SELECT * FROM video WHERE major=? AND (title LIKE ? OR tags LIKE ?)
                   ORDER BY popularity DESC LIMIT 2""",
                (major_row["name"], f"%{keyword}%", f"%{keyword}%"),
            )
            if not matches:
                matches = rows(
                    con,
                    "SELECT * FROM video WHERE major=? ORDER BY popularity DESC LIMIT 2",
                    (major_row["name"],),
                )
            target = public_videos if any(k in subject for k in ("政治", "英语", "数学")) else professional_videos
            for video in matches:
                item = video_payload(con, video)
                item["subject"] = subject
                target.append(item)

        return {
            "school": school_name,
            "major": major_row["name"],
            "category": category,
            "subjects": subjects,
            "public": dedupe(public_videos),
            "professional": dedupe(professional_videos),
            "schools": schools,
            "note": "公共课按专业大类统一，专业课按该专业培养方案的学位课程推导。",
        }
    finally:
        con.close()


def exam_recommendations(student_id: str, school: Optional[str] = None, major: Optional[str] = None) -> Dict[str, Any]:
    return exam_path_for_major(student_id, school, major)


def dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        if item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        out.append(item)
    return out


# ---------------------------------------------------------------- 播放页

def video_detail(video_id: str, student_id: str = "YY08") -> Dict[str, Any]:
    con = connect()
    try:
        found = rows(con, "SELECT * FROM video WHERE video_id=?", (video_id,))
        if not found:
            return {}
        video = found[0]
        episodes = rows(con, "SELECT * FROM video_episode WHERE video_id=? ORDER BY episode_no", (video_id,))
        if not episodes:
            # 培养方案扩充的资源池不预存每一集，按需生成，避免数据库里多出几十万行
            episodes = _synthesize_episodes(video)
        record = rows(
            con, "SELECT * FROM learning_record WHERE student_id=? AND video_id=?", (student_id, video_id)
        )
        payload = video_payload(con, video)
        payload["episodes_list"] = episodes
        payload["record"] = record[0] if record else {"watched_episodes": 0, "progress": 0, "status": "new"}
        return payload
    finally:
        con.close()


def _synthesize_episodes(video: Dict[str, Any]) -> List[Dict[str, Any]]:
    total = max(1, min(int(video.get("episodes") or 1), 120))
    source = video.get("source_url") or ""
    listed = total if total <= 24 else 24
    out = []
    for idx in range(1, listed + 1):
        out.append(
            {
                "episode_id": f"{video['video_id']}-S{idx:02d}",
                "video_id": video["video_id"],
                "episode_no": idx,
                "title": f"{video.get('course_name') or video['title']} 第 {idx:02d} 讲",
                "duration": f"{18 + idx % 27}:{(idx * 11) % 60:02d}",
                "play_url": source,
            }
        )
    return out
