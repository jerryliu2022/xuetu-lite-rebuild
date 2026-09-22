from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .student_profiles import DEMO_ACCOUNTS

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "xuetu_lite.db"
MODEL_PATH = ROOT / "data" / "artifacts" / "ranker_model.json"


def rows(con: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    ascii_terms = re.findall(r"[a-z0-9+#.]+", text)
    zh = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(pair) for pair in zip(zh, zh[1:])]
    return ascii_terms + zh + bigrams


def build_tfidf(
    courses_by_id: Dict[str, Dict[str, Any]],
    videos_by_course: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """课程级 TF-IDF。

    原先是对每个视频建向量再做两两相似度，在 4.5 万条资源上就是 20 亿次点积，
    训练直接跑不完。改成课程粒度：课程名 + 概念 + 该课头部视频标题，
    898 门课的规模下用倒排索引比较，秒级完成，且相似课程本来就该在课程层面判断。
    """
    docs: Dict[str, Counter] = {}
    df: Counter = Counter()
    for course_id, course in courses_by_id.items():
        parts = [course.get("name") or "", course.get("concepts") or ""]
        for video in videos_by_course.get(course_id, [])[:6]:
            parts.append(video.get("title") or "")
        counts = Counter(tokenize(" ".join(parts)))
        docs[course_id] = counts
        for term in counts:
            df[term] += 1

    n_docs = len(docs) or 1
    idf = {term: math.log((n_docs + 1) / (freq + 1)) + 1 for term, freq in df.items()}
    vectors: Dict[str, Dict[str, float]] = {}
    for course_id, counts in docs.items():
        raw = {term: (1 + math.log(count)) * idf[term] for term, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in raw.values())) or 1.0
        vectors[course_id] = {term: round(value / norm, 6) for term, value in raw.items()}
    return {"idf": idf, "vectors": vectors}


def dot(a: Dict[str, float], b: Dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(term, 0.0) for term, value in a.items())


def build_course_similarity(vectors: Dict[str, Dict[str, float]], top_k: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """用倒排索引找相似课程，只累加共享词上的贡献，避免全量两两点积。"""
    postings: Dict[str, List[str]] = defaultdict(list)
    for course_id, vector in vectors.items():
        for term in vector:
            postings[term].append(course_id)

    out: Dict[str, List[Dict[str, Any]]] = {}
    for course_id, vector in vectors.items():
        acc: Dict[str, float] = defaultdict(float)
        for term, weight in vector.items():
            for other in postings[term]:
                if other != course_id:
                    acc[other] += weight * vectors[other][term]
        ranked = sorted(acc.items(), key=lambda item: item[1], reverse=True)[:top_k]
        out[course_id] = [
            {"course_id": key, "score": round(value, 6)} for key, value in ranked if value > 0.03
        ]
    return out


def build_item_similarity(
    watched_ids: Iterable[str],
    videos_by_id: Dict[str, Dict[str, Any]],
    videos_by_course: Dict[str, List[Dict[str, Any]]],
    course_similarity: Dict[str, List[Dict[str, Any]]],
    top_k: int = 8,
    per_course: int = 6,
) -> Dict[str, List[Dict[str, float]]]:
    """视频级相似度：同课资源 + 相似课程头部资源。

    只给学生实际看过的视频建表（几百条），而不是全部 4.5 万条。
    """
    out: Dict[str, List[Dict[str, float]]] = {}
    for video_id in watched_ids:
        video = videos_by_id.get(video_id)
        if not video:
            continue
        course_id = video["course_id"]
        sims: Dict[str, float] = {}
        for other in videos_by_course.get(course_id, []):
            if other["video_id"] != video_id:
                sims[other["video_id"]] = 0.92
        for rank, item in enumerate(course_similarity.get(course_id, [])):
            for other in videos_by_course.get(item["course_id"], [])[:per_course]:
                score = round(item["score"] * 0.85 - rank * 0.01, 6)
                if score > sims.get(other["video_id"], 0.0):
                    sims[other["video_id"]] = score
        ranked = sorted(sims.items(), key=lambda item: item[1], reverse=True)[:top_k]
        out[video_id] = [{"video_id": key, "score": value} for key, value in ranked]
    return out


def descendants(courses: Dict[str, Dict[str, Any]], course_id: str) -> int:
    count = 0
    for course in courses.values():
        prereqs = [part.strip() for part in (course.get("prerequisites") or "").split(",") if part.strip()]
        if course_id in prereqs:
            count += 1
    return count


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


def user_history(con: sqlite3.Connection, student_id: str) -> List[str]:
    return [
        row["video_id"]
        for row in rows(
            con,
            "SELECT video_id FROM learning_record WHERE student_id=? AND progress>0",
            (student_id,),
        )
    ]


class FeatureCache:
    """训练期按学生缓存画像，避免为每个样本重复查库。

    原实现里每造一个样本就要查 4 次数据库（学习记录、全球课程状态、行为偏好），
    样本一多训练时间就被数据库往返拖死。
    """

    def __init__(self, con: sqlite3.Connection, videos_by_id: Dict[str, Dict[str, Any]],
                 courses: Dict[str, Dict[str, Any]]) -> None:
        self.con = con
        self.videos_by_id = videos_by_id
        self.courses = courses
        self._students: Dict[str, Dict[str, Any]] = {}
        self._descendants: Dict[str, int] = {}

    def student_state(self, student: Dict[str, Any]) -> Dict[str, Any]:
        student_id = student["student_id"]
        if student_id in self._students:
            return self._students[student_id]
        major = student["major"]
        term = int(student["semester"])
        history = user_history(self.con, student_id)
        completed_courses = {
            self.videos_by_id[row["video_id"]]["course_id"]
            for row in rows(
                self.con,
                "SELECT video_id FROM learning_record WHERE student_id=? AND status='completed'",
                (student_id,),
            )
            if row["video_id"] in self.videos_by_id
        }
        platform_rows = rows(
            self.con,
            """
            SELECT v.platform AS platform, COUNT(*) AS c
            FROM behavior_log b JOIN video v ON b.video_id=v.video_id
            WHERE b.student_id=? AND b.event_type IN ('play','complete','like','favorite')
            GROUP BY v.platform
            """,
            (student_id,),
        )
        total = sum(row["c"] for row in platform_rows) or 1
        state = {
            "history": history,
            "completed_courses": completed_courses,
            # 与 recommender 的召回口径一致：按「学生自己的专业 + 学期」判断，
            # 而不是全库 course.status（那样所有学生共用同一份"正在学"集合）
            "current_courses": {
                course_id for course_id, course in self.courses.items()
                if course.get("major") == major and course.get("semester") == term
            },
            "next_courses": {
                course_id for course_id, course in self.courses.items()
                if course.get("major") == major and course.get("semester") == term + 1
            },
            "platform_pref": {row["platform"]: row["c"] / total for row in platform_rows},
        }
        self._students[student_id] = state
        return state

    def descendants(self, course_id: str) -> int:
        if course_id not in self._descendants:
            self._descendants[course_id] = descendants(self.courses, course_id)
        return self._descendants[course_id]

    def similarity(self, student_state: Dict[str, Any], video_id: str,
                   item_similarity: Dict[str, List[Dict[str, float]]]) -> float:
        best = 0.0
        for watched in student_state["history"]:
            for item in item_similarity.get(watched, []):
                if item["video_id"] == video_id:
                    best = max(best, item["score"])
        return min(best, 1.0)


def curriculum_graph_score(state: Dict[str, Any], course: Dict[str, Any]) -> float:
    """课程图谱特征，与 recommender.graph_score 用同一套分档。"""
    course_id = course["course_id"]
    if course_id in state["current_courses"]:
        return 1.0
    prereqs = [part.strip() for part in (course.get("prerequisites") or "").split(",") if part.strip()]
    if any(prereq in state["completed_courses"] for prereq in prereqs):
        return 0.82
    if course_id in state["next_courses"]:
        return 0.66
    return 0.4


def feature_vector(
    cache: FeatureCache,
    student: Dict[str, Any],
    video: Dict[str, Any],
    course: Dict[str, Any],
    item_similarity: Dict[str, List[Dict[str, float]]],
) -> List[float]:
    state = cache.student_state(student)
    platform_pref = state["platform_pref"].get(video["platform"], 0.0)
    return [
        math.log1p(video["popularity"]) / 14.0,
        float(video["rating"]) / 5.0,
        1.0 - float(video["is_paid"]) * 0.22,
        curriculum_graph_score(state, course),
        cache.similarity(state, video["video_id"], item_similarity),
        platform_pref,
        min(cache.descendants(course["course_id"]) / 4.0, 1.0),
        major_alignment(student, video, course),
    ]


def sigmoid(value: float) -> float:
    if value < -30:
        return 0.0
    if value > 30:
        return 1.0
    return 1 / (1 + math.exp(-value))


def train_logistic(samples: List[Tuple[List[float], int]]) -> Dict[str, Any]:
    dims = len(samples[0][0])
    means = [sum(sample[0][i] for sample in samples) / len(samples) for i in range(dims)]
    stdevs = []
    for i in range(dims):
        var = sum((sample[0][i] - means[i]) ** 2 for sample in samples) / len(samples)
        stdevs.append(math.sqrt(var) or 1.0)
    weights = [0.0 for _ in range(dims)]
    bias = 0.0
    lr = 0.18
    l2 = 0.015
    for _ in range(480):
        grad_w = [0.0 for _ in range(dims)]
        grad_b = 0.0
        for features, label in samples:
            x = [(features[i] - means[i]) / stdevs[i] for i in range(dims)]
            pred = sigmoid(sum(weights[i] * x[i] for i in range(dims)) + bias)
            err = pred - label
            for i in range(dims):
                grad_w[i] += err * x[i] + l2 * weights[i]
            grad_b += err
        scale = 1 / len(samples)
        weights = [weights[i] - lr * grad_w[i] * scale for i in range(dims)]
        bias -= lr * grad_b * scale
    return {"weights": weights, "bias": bias, "means": means, "stdevs": stdevs}


def auc(samples: List[Tuple[List[float], int]], model: Dict[str, Any]) -> float:
    scored = []
    for features, label in samples:
        x = [(features[i] - model["means"][i]) / model["stdevs"][i] for i in range(len(features))]
        score = sigmoid(sum(model["weights"][i] * x[i] for i in range(len(x))) + model["bias"])
        scored.append((score, label))
    positives = [score for score, label in scored if label == 1]
    negatives = [score for score, label in scored if label == 0]
    wins = ties = 0
    for pos in positives:
        for neg in negatives:
            if pos > neg:
                wins += 1
            elif pos == neg:
                ties += 1
    denom = len(positives) * len(negatives) or 1
    return (wins + 0.5 * ties) / denom


def _build_samples(con: sqlite3.Connection, skip_pairs=None) -> Tuple[List[Tuple[List[float], int]], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    student_ids = [account["student_id"] for account in DEMO_ACCOUNTS]
    placeholders = ",".join("?" * len(student_ids))
    videos = rows(con, "SELECT * FROM video")
    courses = {row["course_id"]: row for row in rows(con, "SELECT * FROM course")}
    videos_by_id = {row["video_id"]: row for row in videos}

    videos_by_course: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for video in videos:
        videos_by_course[video["course_id"]].append(video)
    for course_id in videos_by_course:
        videos_by_course[course_id].sort(key=lambda item: item["popularity"], reverse=True)

    tfidf = build_tfidf(courses, videos_by_course)
    course_similarity = build_course_similarity(tfidf["vectors"])
    log_rows = rows(
        con,
        """
        SELECT * FROM recommend_log
        WHERE student_id IN (%s)
        ORDER BY student_id, candidate_id
        """ % placeholders,
        student_ids,
    )
    watched_ids = {row["candidate_id"] for row in log_rows}
    watched_ids |= {
        row["video_id"] for row in rows(con, "SELECT video_id FROM learning_record")
    }
    item_similarity = build_item_similarity(
        watched_ids, videos_by_id, videos_by_course, course_similarity
    )

    cache = FeatureCache(con, videos_by_id, courses)
    students = {
        row["student_id"]: row
        for row in rows(con, "SELECT * FROM student")
    }
    samples: List[Tuple[List[float], int]] = []
    for log in log_rows:
        if skip_pairs and (log["student_id"], log["candidate_id"]) in skip_pairs:
            continue
        student = students.get(log["student_id"])
        video = videos_by_id.get(log["candidate_id"])
        if not student or not video:
            continue
        course = courses.get(video["course_id"])
        if not course:
            continue
        samples.append(
            (
                feature_vector(cache, student, video, course, item_similarity),
                int(log["click"]),
            )
        )
    context = {
        "videos": videos,
        "courses": courses,
        "videos_by_id": videos_by_id,
        "videos_by_course": videos_by_course,
        "cache": cache,
        "students": students,
    }
    return samples, item_similarity, tfidf, context


def train_fold_model(base_bundle=None, skip_pairs=None) -> Dict[str, Any]:
    """复用一次相似度/IDF 向量，只对样本子集重训，供留一法逐折使用。"""
    con = sqlite3.connect(DB_PATH)
    if base_bundle is None:
        item_similarity = None
        tfidf = None
        context = None
    else:
        item_similarity = base_bundle["item_similarity"]
        tfidf = base_bundle["tfidf"]
        context = base_bundle.get("_context")
    if context is None:
        samples, item_similarity, tfidf, context = _build_samples(con, skip_pairs)
    else:
        courses = context["courses"]
        videos_by_id = context["videos_by_id"]
        cache = context["cache"]
        students = context["students"]
        student_ids = [account["student_id"] for account in DEMO_ACCOUNTS]
        placeholders = ",".join("?" * len(student_ids))
        samples = []
        for log in rows(
            con,
            """
            SELECT * FROM recommend_log
            WHERE student_id IN (%s)
            ORDER BY student_id, candidate_id
            """ % placeholders,
            student_ids,
        ):
            if skip_pairs and (log["student_id"], log["candidate_id"]) in skip_pairs:
                continue
            student = students.get(log["student_id"])
            video = videos_by_id.get(log["candidate_id"])
            if not student or not video:
                continue
            course = courses.get(video["course_id"])
            if not course:
                continue
            samples.append(
                (
                    feature_vector(cache, student, video, course, item_similarity),
                    int(log["click"]),
                )
            )
    con.close()
    if not samples:
        raise RuntimeError("推荐日志为空，无法训练排序模型")
    model = train_logistic(samples)
    report = {
        "sample_count": len(samples),
        "positive_rate": round(sum(label for _, label in samples) / len(samples), 4),
        "auc": round(auc(samples, model), 4),
        "feature_names": [
            "log_popularity",
            "rating",
            "free_access",
            "curriculum_graph",
            "itemcf_similarity",
            "platform_preference",
            "course_downstream_value",
            "major_alignment",
        ],
        "choice": "Hybrid recall + TF-IDF ItemCF + lightweight logistic ranker; CPU-only, no heavy GPU/vector DB dependency.",
    }
    return {
        "ranker": model,
        "item_similarity": item_similarity,
        "tfidf": tfidf,
        "_context": context,
        "report": report,
    }


def main() -> None:
    bundle = train_fold_model()
    # _context 是「全量视频/课程快照」的训练中间态，只给留一法评估在内存里复用同一组
    # 向量用（evaluate_model.py:122-131 会自己重建一份），不属于模型产物。
    # 不剔除的话，模型文件会从 960 KB 涨到 1252 KB（多出 281 KB，约 +30%）。
    bundle.pop("_context", None)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(bundle["report"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
