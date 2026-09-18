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


def build_tfidf(videos: List[Dict[str, Any]], courses_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    docs: Dict[str, Counter] = {}
    df: Counter = Counter()
    for video in videos:
        course = courses_by_id[video["course_id"]]
        text = " ".join([video["title"], video["tags"], video["summary"], course["name"], course["concepts"]])
        counts = Counter(tokenize(text))
        docs[video["video_id"]] = counts
        for term in counts:
            df[term] += 1

    n_docs = len(videos)
    idf = {term: math.log((n_docs + 1) / (freq + 1)) + 1 for term, freq in df.items()}
    vectors: Dict[str, Dict[str, float]] = {}
    for video_id, counts in docs.items():
        raw = {term: (1 + math.log(count)) * idf[term] for term, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in raw.values())) or 1.0
        vectors[video_id] = {term: round(value / norm, 6) for term, value in raw.items()}
    return {"idf": idf, "vectors": vectors}


def dot(a: Dict[str, float], b: Dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(term, 0.0) for term, value in a.items())


def build_item_similarity(vectors: Dict[str, Dict[str, float]]) -> Dict[str, List[Dict[str, float]]]:
    out: Dict[str, List[Dict[str, float]]] = {}
    for video_id, vector in vectors.items():
        sims = []
        for other_id, other in vectors.items():
            if other_id == video_id:
                continue
            score = dot(vector, other)
            if score > 0:
                sims.append({"video_id": other_id, "score": round(score, 6)})
        out[video_id] = sorted(sims, key=lambda item: item["score"], reverse=True)[:8]
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


def feature_vector(
    con: sqlite3.Connection,
    student: Dict[str, Any],
    video: Dict[str, Any],
    course: Dict[str, Any],
    courses: Dict[str, Dict[str, Any]],
    videos_by_id: Dict[str, Dict[str, Any]],
    item_similarity: Dict[str, List[Dict[str, float]]],
) -> List[float]:
    completed_courses = {
        videos_by_id[row["video_id"]]["course_id"]
        for row in rows(con, "SELECT video_id FROM learning_record WHERE student_id=? AND status='completed'", (student["student_id"],))
        if row["video_id"] in videos_by_id
    }
    studying_courses = {row["course_id"] for row in rows(con, "SELECT course_id FROM course WHERE status='studying'")}
    planned_courses = {row["course_id"] for row in rows(con, "SELECT course_id FROM course WHERE semester=?", (student["semester"] + 1,))}
    history = user_history(con, student["student_id"])
    sim_score = 0.0
    for watched in history:
        for item in item_similarity.get(watched, []):
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
    prereq_hit = 0.0
    prereqs = [part.strip() for part in (course.get("prerequisites") or "").split(",") if part.strip()]
    if course["course_id"] in studying_courses:
        prereq_hit = 1.0
    elif any(prereq in completed_courses for prereq in prereqs):
        prereq_hit = 0.8
    elif course["course_id"] in planned_courses:
        prereq_hit = 0.65
    return [
        math.log1p(video["popularity"]) / 14.0,
        float(video["rating"]) / 5.0,
        1.0 - float(video["is_paid"]) * 0.22,
        prereq_hit,
        min(sim_score, 1.0),
        platform_pref,
        min(descendants(courses, course["course_id"]) / 4.0, 1.0),
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
    tfidf = build_tfidf(videos, courses)
    item_similarity = build_item_similarity(tfidf["vectors"])
    samples = []
    logs = rows(
        con,
        """
        SELECT * FROM recommend_log
        WHERE student_id IN (%s)
        ORDER BY student_id, candidate_id
        """ % placeholders,
        student_ids,
    )
    for log in logs:
        if skip_pairs and (log["student_id"], log["candidate_id"]) in skip_pairs:
            continue
        student = rows(con, "SELECT * FROM student WHERE student_id=?", (log["student_id"],))[0]
        video = videos_by_id[log["candidate_id"]]
        samples.append(
            (
                feature_vector(con, student, video, courses[video["course_id"]], courses, videos_by_id, item_similarity),
                int(log["click"]),
            )
        )
    return samples, item_similarity, tfidf, {"videos": videos, "courses": courses, "videos_by_id": videos_by_id}


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
        videos = context["videos"]
        courses = context["courses"]
        videos_by_id = context["videos_by_id"]
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
            student = rows(con, "SELECT * FROM student WHERE student_id=?", (log["student_id"],))[0]
            video = videos_by_id[log["candidate_id"]]
            samples.append(
                (
                    feature_vector(con, student, video, courses[video["course_id"]], courses, videos_by_id, item_similarity),
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
    bundle = train_rank_model()
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(bundle["report"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
