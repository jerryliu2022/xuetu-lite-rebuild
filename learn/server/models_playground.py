# -*- coding: utf-8 -*-
"""
推荐算法演练场：把同一个「给学生推荐 9 门课」的任务，用从简单到复杂的 7 种方案各做一遍。

为什么要由易到难？
    直接看项目里最终那套「多路召回 + TF-IDF + ItemCF + LR 排序 + MMR 重排」
    会被一堆术语砸晕。正确学法是先看最笨的办法有多差，再一步步看每个改进解决了什么问题。

本文件的每个模型都返回同样的三样东西，方便横向对比：
    1. items    —— 推荐出来的课程列表（带推荐理由和打分）
    2. metrics  —— Precision@5 / Recall@5 / NDCG@5 / HitRate@5 / 覆盖率 / 多样性
    3. explain  —— 这个模型在干什么、为什么比上一个好、对应源码哪一段

数据源只读正式库，任何模型都不会写库。

关于建模粒度（重要）：
    库里有 898 门课、44,997 条视频。TF-IDF 与相似度一律建在**课程级**——
    一门课一篇文档、一门课一个向量，898 × 898 的规模用倒排索引秒级算完。
    视频级的相似度表只给「真正需要的那批视频」现场派生（同课资源 + 相似课程
    头部资源），与 backend/train_model.py:89-118 一致。
    如果按视频建向量再两两比较，就是 44,997² ≈ 20 亿次点积，演练场必然卡死。
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_DB = PROJECT_ROOT / "data" / "processed" / "xuetu_lite.db"

FEATURE_NAMES = [
    "log_popularity",       # 热度取对数：压缩量级差异，避免百万级播放一支独大
    "rating",               # 评分
    "free_access",          # 是否免费（学生党很在意）
    "curriculum_graph",     # 与培养方案/先修关系的契合度
    "itemcf_similarity",    # 与学生看过的课的相似度
    "platform_preference",  # 学生过去的平台偏好
    "course_downstream_value",  # 这门课是几门课的先修（越基础分越高）
    "major_alignment",      # 与专业的匹配度
]

# 模型 6 的负样本采样上限。全库 4.5 万条视频里正样本（真的学过）只有几十条，
# 逐条算完要 70 秒；AUC 只看正负样本的相对排序，等距采样不改变这个能力。
NEGATIVE_SAMPLE = 4000

# 模型 7 的召回限流，对应 backend/recommender.py:274-339 的两道闸：
# ① 每门课按课程类型给配额（专业核心 8 / 学科基础 5 / 通识必修 3）
# ② 总量到 260 就停（`if len(reasons) >= 260: break`），实际候选稳定在 114 个左右
MAX_RECALL = 260
MAX_PER_COURSE = 6


# ---------------------------------------------------------------- 数据加载
def load_data() -> Dict[str, Any]:
    """以只读方式加载正式库的全部建模所需数据。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        videos = [dict(r) for r in con.execute("SELECT * FROM video")]
        courses = [dict(r) for r in con.execute("SELECT * FROM course")]
        students = [dict(r) for r in con.execute("SELECT * FROM student")]
        learning = [dict(r) for r in con.execute("SELECT * FROM learning_record")]
        behavior = [dict(r) for r in con.execute("SELECT * FROM behavior_log")]
    finally:
        con.close()
    # 按课程把视频分好组，并按热度降序 —— 与 backend/train_model.py:324-328 完全一致。
    # 课程级 TF-IDF 只取每门课头部若干条标题，课程相似度派生到视频时也按这个顺序取，
    # 所以这里必须先排好序，否则两边的"头部视频"会是两批不同的东西。
    videos_by_course: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for video in videos:
        videos_by_course[video["course_id"]].append(video)
    for course_id in videos_by_course:
        videos_by_course[course_id].sort(key=lambda item: item["popularity"] or 0, reverse=True)

    return {
        "videos": videos,
        "courses": courses,
        "students": students,
        "learning": learning,
        "behavior": behavior,
        "videos_by_id": {v["video_id"]: v for v in videos},
        "courses_by_id": {c["course_id"]: c for c in courses},
        "videos_by_course": dict(videos_by_course),
    }


# ---------------------------------------------------------------- 工具函数（与项目源码保持一致）
def tokenize(text: str) -> List[str]:
    """中文分词：项目没用 jieba，而是「单字 + 二元组」的土办法，足够轻量。

    出处：backend/train_model.py:23-28
    """
    text = (text or "").lower()
    ascii_terms = re.findall(r"[a-z0-9+#.]+", text)
    zh = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(pair) for pair in zip(zh, zh[1:])]
    return ascii_terms + zh + bigrams


def build_tfidf(courses_by_id: Dict[str, Any],
                videos_by_course: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """TF-IDF：把**每门课**的文字描述变成向量。

    出处：backend/train_model.py:31-59

    粒度是「课程」而不是「视频」，这一点是整个模块能不能跑起来的关键。
    如果按视频建向量（44,997 条），后面的两两相似度就是 44997² ≈ 20 亿次点积，
    演练场直接卡死；按课程建（898 门）只有 898² ≈ 80 万次，而且用倒排索引
    还能再省掉绝大部分。判断「高数和线代像不像」本来就该在课程层面做，
    拿「高数第3讲_洛必达法则」去比「线代第7讲_特征值」没有实际意义。
    """
    docs: Dict[str, Counter] = {}
    df: Counter = Counter()
    for course_id, course in courses_by_id.items():
        # 一门课一篇文档：课名 + 概念 + 该课头部 6 条视频标题
        parts = [course.get("name") or "", course.get("concepts") or ""]
        for video in videos_by_course.get(course_id, [])[:6]:
            parts.append(video.get("title") or "")
        counts = Counter(tokenize(" ".join(parts)))
        docs[course_id] = counts
        for term in counts:
            df[term] += 1

    n_docs = len(docs) or 1
    idf = {t: math.log((n_docs + 1) / (f + 1)) + 1 for t, f in df.items()}
    vectors: Dict[str, Dict[str, float]] = {}
    for course_id, counts in docs.items():
        raw = {t: (1 + math.log(c)) * idf[t] for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in raw.values())) or 1.0
        vectors[course_id] = {t: round(v / norm, 6) for t, v in raw.items()}
    return {"idf": idf, "vectors": vectors}


def dot(a: Dict[str, float], b: Dict[str, float]) -> float:
    """稀疏向量点积（余弦相似度，因为已归一化）。出处：backend/train_model.py:62-65"""
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def build_course_similarity(vectors: Dict[str, Dict[str, float]],
                            top_k: int = 10) -> Dict[str, List[Dict[str, Any]]]:
    """课程 × 课程的相似度表，用**倒排索引**算，不做全量两两。

    出处：backend/train_model.py:68-86

    做法：先记下「每个词出现在哪些课里、各自权重多少」（postings），然后对每门课，
    只沿着自己有的词去累加共享那段贡献 —— 两门课要是没有共同词，
    压根不会碰到一起。这就是它比 `for a in X: for b in X` 快几个数量级的原因。

    实现上再压一层：课程先映射成 0..N-1 的下标，postings 里直接存好权重，
    累加用 list 而不是 dict。898 门课 × 64 个词项 × 平均 224 个共现课
    ≈ 1300 万次内层累加，省掉哈希查找和二次查表后快约 2.5 倍。
    """
    ids = list(vectors)
    n = len(ids)
    postings: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
    for index, course_id in enumerate(ids):
        for term, weight in vectors[course_id].items():
            postings[term].append((index, weight))

    out: Dict[str, List[Dict[str, Any]]] = {}
    for index, course_id in enumerate(ids):
        acc = [0.0] * n
        for term, weight in vectors[course_id].items():
            for other_index, other_weight in postings[term]:
                if other_index != index:
                    acc[other_index] += weight * other_weight
        ranked = sorted(
            ((ids[j], score) for j, score in enumerate(acc) if score),
            key=lambda item: item[1],
            reverse=True,
        )[:top_k]
        out[course_id] = [
            {"course_id": key, "score": round(value, 6)}
            for key, value in ranked if value > 0.03
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
    """视频级相似度表：同课资源 + 相似课程的头部资源。

    出处：backend/train_model.py:89-118

    注意这里**只给 watched_ids 建表**（一个学生看过的也就几百条），
    而不是全部 4.5 万条。这是第二层性能保障：课程级把 20 亿降到 80 万，
    这里再把它压到「实际用得上的那几百条」。
    """
    out: Dict[str, List[Dict[str, float]]] = {}
    for video_id in watched_ids:
        video = videos_by_id.get(video_id)
        if not video:
            continue
        course_id = video["course_id"]
        sims: Dict[str, float] = {}
        # 同课资源：最相关，给 0.92
        for other in videos_by_course.get(course_id, []):
            if other["video_id"] != video_id:
                sims[other["video_id"]] = 0.92
        # 相似课程的头部资源：按课程相似度打折，名次越靠后折扣越大
        for rank, item in enumerate(course_similarity.get(course_id, [])):
            for other in videos_by_course.get(item["course_id"], [])[:per_course]:
                score = round(item["score"] * 0.85 - rank * 0.01, 6)
                if score > sims.get(other["video_id"], 0.0):
                    sims[other["video_id"]] = score
        ranked = sorted(sims.items(), key=lambda item: item[1], reverse=True)[:top_k]
        out[video_id] = [{"video_id": key, "score": value} for key, value in ranked]
    return out


# ---------------------------------------------------------------- 相似度缓存
# 分两级，因为两级的「失效条件」不同：
#   ① 课程级（TF-IDF 向量 + 课程相似度）只依赖库里的课表，跟谁在看无关 → 全局缓存一份；
#   ② 视频级表依赖「这个人看过什么」，但每个 video_id 的结果**互相独立**，
#      所以按 video_id 逐条缓存。留一法会反复调用模型，这样绝大多数条目直接命中，
#      只有新出现的 video_id 才需要补算 —— 这是演练场能在线跑出结果的主要原因。
_COURSE_CACHE: Dict[str, Dict[str, Any]] = {}
_ITEM_CACHE: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}


def _library_stamp(data: Dict[str, Any]) -> str:
    """库指纹：用来判断缓存是否还有效。"""
    return f"{len(data['videos'])}:{len(data['courses'])}"


def get_course_index(data: Dict[str, Any]) -> Dict[str, Any]:
    """返回 {"tfidf": ..., "course_similarity": ...}，全局缓存一份。"""
    key = _library_stamp(data)
    hit = _COURSE_CACHE.get(key)
    if hit is None:
        tfidf = build_tfidf(data["courses_by_id"], data["videos_by_course"])
        hit = {
            "tfidf": tfidf,
            "course_similarity": build_course_similarity(tfidf["vectors"]),
        }
        _COURSE_CACHE[key] = hit
    return hit


def get_item_similarity(data: Dict[str, Any],
                        watched_ids: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    """返回 {video_id: [相似视频...]}，按 video_id 逐条缓存。"""
    stamp = _library_stamp(data)
    wanted = sorted(set(watched_ids))
    missing = [vid for vid in wanted if (stamp, vid) not in _ITEM_CACHE]
    if missing:
        course_similarity = get_course_index(data)["course_similarity"]
        fresh = build_item_similarity(
            missing, data["videos_by_id"], data["videos_by_course"], course_similarity
        )
        for vid, entries in fresh.items():
            _ITEM_CACHE[(stamp, vid)] = entries
    return {vid: _ITEM_CACHE.get((stamp, vid), []) for vid in wanted}


def split_csv(value: str) -> List[str]:
    """逗号分隔字符串切列表（兼容中文逗号）。出处：backend/recommender.py:37-38"""
    return [p.strip() for p in (value or "").replace("，", ",").split(",") if p.strip()]


def sigmoid(x: float) -> float:
    """把任意实数压到 (0,1)，用于逻辑回归输出概率。出处：backend/train_model.py:159-164"""
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1 / (1 + math.exp(-x))


# ---------------------------------------------------------------- 样本的「标准答案」
def seen_ids(data: Dict[str, Any], student_id: str,
             override: Optional[Set[str]] = None) -> Set[str]:
    """取该学生「已经看过」的视频集合，推荐时要排除它们。

    override 是留一法评估专用的开关：
        评估某条记录时，要假装那门课还没看过，把它放回候选池，
        其余看过的仍然排除 —— 这样才能检验模型能不能把它重新捞回来。
    """
    if override is not None:
        return set(override)
    return {
        r["video_id"]
        for r in data["learning"]
        if r["student_id"] == student_id and (r["progress"] or 0) > 0
    }


def ground_truth(data: Dict[str, Any], student_id: str) -> Set[str]:
    """弱正样本：学习进度 >= 25% 的视频。

    出处：backend/evaluate_model.py:168（README 第 73 行也有说明）

    注意：直接拿它和「排除已看过之后的推荐结果」比，命中率必然是 0 ——
    因为正样本恰恰就是被排除掉的那批。正确的比对方式是下面的留一法。
    """
    return {
        r["video_id"]
        for r in data["learning"]
        if r["student_id"] == student_id and (r["progress"] or 0) >= 0.25
    }


# 每个账号最多做几折。出处：backend/evaluate_model.py:68 MAX_LOO_FOLDS = 2 ——
# 注释写得很清楚：「25 个账号 × 每折一次重训，折数太大会把评估拖到十几分钟」。
# 演练场没有离线评估缓存、每次请求现算，这个上限只会更必要。
MAX_LOO_FOLDS = 2


def leave_one_out(fn, data: Dict[str, Any], student_id: str, topn: int = 5) -> Dict[str, Any]:
    """留一法评估 —— 与 backend/evaluate_model.py:67-116 _leave_one_out_student 同一口径。

    做法：把学生看过的每一条记录轮流「遮住」，
          其余 visibility 作为历史上下文，看被遮住的那门课能不能被推荐出来。
          最后统计命中率。

    这样算出来的指标才有区分度：好模型能把学生真正在学的课重新捞回来。

    简化说明（两处，都是为了响应速度）：
      ① 项目真实实现每折还会用 train_fold_model 重训一次模型（evaluate_model.py:85），
         这里省略了该步 —— 模型 6 的 LR 训练结果各折复用（见 _train_lr），
         因此指标与 /api/admin/model-evaluation 的绝对值会有差异，但相对排序一致。
      ② 折数上限 MAX_LOO_FOLDS，与 evaluate_model.py:68 取同一个值。
    """
    all_seen = sorted(seen_ids(data, student_id))
    if not all_seen:
        return {"hit_rate@5": 0.0, "hit_rate@9": 0.0, "folds": 0, "fold_count": 0, "details": []}

    # 折数超上限时按等距抽折，而不是取前 N 个 —— 避免只测到排序靠前的那批记录
    folds_held = all_seen
    if len(all_seen) > MAX_LOO_FOLDS:
        step = len(all_seen) / MAX_LOO_FOLDS
        folds_held = [all_seen[int(i * step)] for i in range(MAX_LOO_FOLDS)]

    hits5 = hits9 = 0
    ndcg_sum = 0.0
    details: List[Dict[str, Any]] = []
    for held in folds_held:
        context = set(all_seen) - {held}
        # 模型函数返回的是完整结果字典，这里只取推荐列表那部分
        result = fn(data, student_id, topn, seen_override=context)
        items = result.get("items", []) if isinstance(result, dict) else result
        top5 = [it["video_id"] for it in items[:5]]
        top9 = [it["video_id"] for it in items[:9]]
        hit5 = held in top5
        hit9 = held in top9
        hits5 += int(hit5)
        hits9 += int(hit9)

        # NDCG：每折只有一个正样本，理想情况它排第 1（DCG=1/log2(2)=1）
        # 所以这折的 NDCG 就是 1/log2(rank+1)，没命中算 0
        rank = top9.index(held) + 1 if hit9 else 0
        if rank:
            ndcg_sum += 1.0 / math.log2(rank + 1)

        details.append({
            "held_out": held,
            "held_title": (data["videos_by_id"].get(held) or {}).get("title", ""),
            "rank": rank if rank else None,
            "top5_hit": hit5,
            "top9_hit": hit9,
        })

    return {
        "hit_rate@5": round(hits5 / len(folds_held), 4),
        "hit_rate@9": round(hits9 / len(folds_held), 4),
        "ndcg@5": round(ndcg_sum / len(folds_held), 4),
        "folds": len(folds_held),
        "fold_count": len(folds_held),
        # 保留「一共看过多少条」这个原始信息，便于和 fold_count 对照看抽样比例
        "learning_record_count": len(all_seen),
        "details": details,
    }


def build_metrics(fn, items: List[Dict[str, Any]], data: Dict[str, Any],
                  student_id: str) -> Dict[str, Any]:
    """汇总一个模型的全部评估指标。

    命中类指标（precision/hit_rate/ndcg）走留一法：
        把学生看过的课逐门遮住，看模型能否把它重新捞回前 5 —— 这才是推荐系统
        真实的评估口径（backend/evaluate_model.py:67 同款做法）。
    覆盖率 / 多样性基于正常推荐结果的前 9 条。
    """
    loo = leave_one_out(fn, data, student_id)
    truth = ground_truth(data, student_id)

    top = items[:9]
    # 多样性：推荐结果覆盖了多少个不同平台（同平台霸屏说明推荐太窄）
    platforms = {
        it.get("platform")
        or (data["videos_by_id"].get(it["video_id"]) or {}).get("platform")
        for it in top
    }
    platforms.discard(None)

    # 覆盖率：这 9 条推荐覆盖了多少门不同的课程
    #（有的模型会推 9 条同门课的重复视频，这个值就低）
    courses = {
        (data["videos_by_id"].get(it["video_id"]) or {}).get("course_id")
        for it in top
    }
    courses.discard(None)

    return {
        "precision@5": loo["hit_rate@5"],
        "recall@5": loo["hit_rate@9"],
        "hit_rate@5": loo["hit_rate@5"],
        "hit_rate@9": loo["hit_rate@9"],
        "ndcg@5": loo["ndcg@5"],
        "coverage": round(len(courses) / max(len(data["courses"]), 1), 4),
        "course_coverage_count": len(courses),
        "diversity": round(len(platforms) / max(len(top), 1), 4),
        "hit_count": int(round(loo["hit_rate@5"] * loo["fold_count"])),
        "truth_size": len(truth),
        "fold_count": loo["fold_count"],
        "eval_method": "leave_one_out",
        "folds": loo["details"],
    }


def realize(items: List[Dict[str, Any]], data: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """给候选列表补齐展示字段（播放量文案等），出处：backend/recommender.py:243-247"""
    out = []
    for it in items[:limit]:
        v = data["videos_by_id"][it["video_id"]]
        item = dict(it)
        item["title"] = v["title"]
        item["platform"] = v["platform"]
        item["is_paid"] = bool(v["is_paid"])
        item["plays"] = f"{v['popularity'] / 10000:.1f}万播放"
        item["score"] = round(float(it.get("score", 0.0)), 4)
        out.append(item)
    return out


# ---------------------------------------------------------------- 模型 1：热度榜
def model_popularity(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """最朴素的 baseline：全网最火的前 N 门课，所有人都看到一样的东西。

    缺点显而易见：完全没考虑「你是谁」，所有人的推荐页一模一样。
    它的价值是作为一个底线 —— 后面任何模型都应该比它强。
    """
    student = next((s for s in data["students"] if s["student_id"] == student_id), None)
    seen = seen_ids(data, student_id, seen_override)

    items = []
    for v in sorted(data["videos"], key=lambda x: -(x["popularity"] or 0)):
        if v["video_id"] in seen:
            continue
        items.append({
            "video_id": v["video_id"],
            "score": round(math.log1p(v["popularity"] or 0) / 14.0, 6),
            "reason": "全网热门，所有人看到的都一样",
        })

    truth = ground_truth(data, student_id)
    return {
        "items": realize(items, data, topn),
        "explain": {
            "idea": "按 popularity（播放量）倒序取前 N。排序依据只有一个数字。",
            "formula": "score = log1p(popularity) / 14   # 取对数是为了压缩量级",
            "pros": "实现最简单、结果稳定、不依赖任何个人数据（适合冷启动）",
            "cons": "没有个性化 —— 你和朋友看到的页面完全一样；看不到『接下来该学什么』",
            "source": "backend/recommender.py:114-123（项目把它当『热门兜底召回』的其中一路）",
        },
        "features_used": ["popularity"],
        "student_major": student["major"] if student else "",
    }


# ---------------------------------------------------------------- 模型 2：评分榜
def model_rating(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """第二朴素：按评分排。比热度榜多了『质量』维度，但依然没有个性化。"""
    seen = seen_ids(data, student_id, seen_override)
    items = []
    for v in sorted(data["videos"], key=lambda x: (-(x["rating"] or 0), -(x["popularity"] or 0))):
        if v["video_id"] in seen:
            continue
        items.append({
            "video_id": v["video_id"],
            "score": round((v["rating"] or 0) / 5.0, 6),
            "reason": "评分最高",
        })
    truth = ground_truth(data, student_id)
    return {
        "items": realize(items, data, topn),
        "explain": {
            "idea": "按 rating 倒序，评分相同再看热度。",
            "formula": "ORDER BY rating DESC, popularity DESC",
            "pros": "比纯热度更关注教学质量",
            "cons": "评分高的往往是老牌通识课，未必是『你下一门该学的课』；仍然零个性化",
            "source": "知识点延伸（项目里 rating 只作为特征之一，见 train_model.py:149）",
        },
        "features_used": ["rating", "popularity"],
        "student_major": "",
    }


# ---------------------------------------------------------------- 模型 3：课程图谱规则（第一次有了「你」）
def model_curriculum(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """利用培养方案：正在学的课 → 同课视频；学过的课 → 后继课；下学期要学的课 → 预习。

    这是本项目最有价值的一层：学生选课有强先后顺序（先学 C 语言才能学数据结构），
    纯数据驱动的推荐反而容易推荐出「跳级」的课。
    """
    student = next((s for s in data["students"] if s["student_id"] == student_id), None)
    seen = seen_ids(data, student_id, seen_override)
    completed_courses = set()
    for r in data["learning"]:
        if r["student_id"] == student_id and r["status"] == "completed":
            v = data["videos_by_id"].get(r["video_id"])
            if v:
                completed_courses.add(v["course_id"])

    reasons: Dict[str, str] = {}
    scores: Dict[str, float] = {}

    studying = [c for c in data["courses"] if c["status"] == "studying"]
    planned = [c for c in data["courses"] if c["semester"] == (student["semester"] + 1 if student else 0)]

    # 小常识：setdefault 意味着「先到先得」，所以先跑的那一路优先级更高。
    # 这里刻意让「基于该学生自己学过课程」的那一路排在全局规则之前，
    # 因为它是唯一真正因人而异的信号 —— 否则 5 个不同专业的学生会得到一模一样的推荐。
    # 取「某门课的视频」一律走 videos_by_course 字典，不要写成
    # `for v in data["videos"]: if v["course_id"] == ...` —— 后者每门课都要
    # 扫一遍全库 4.5 万条（乘上课程数就是几千万次比较），模型 3 因此要跑 2 秒。
    # 一律用 sorted() 遍历 set：下面靠 setdefault / 严格大于做「先到先得」，
    # 而 set 的迭代顺序受字符串哈希随机化影响，不排序会导致同一个学生
    # 每次请求拿到不同的推荐结果。
    for done_id in sorted(completed_courses):
        for c in data["courses"]:
            if done_id not in split_csv(c.get("prerequisites") or ""):
                continue
            for v in data["videos_by_course"].get(c["course_id"], []):
                if v["video_id"] not in seen:
                    reasons.setdefault(v["video_id"], f"已学过先修课，推荐后继《{c['name']}》")
                    scores.setdefault(v["video_id"], 1.0)

    for c in studying:
        for v in data["videos_by_course"].get(c["course_id"], []):
            if v["video_id"] not in seen:
                reasons.setdefault(v["video_id"], f"正在学习《{c['name']}》，补同主题视频")
                scores.setdefault(v["video_id"], 0.92)
    for c in planned:
        for v in data["videos_by_course"].get(c["course_id"], []):
            if v["video_id"] not in seen:
                reasons.setdefault(v["video_id"], f"下学期要学《{c['name']}》，适合预习")
                scores.setdefault(v["video_id"], 0.74)

    # 专业匹配加成：同分时优先推本专业方向的课
    if student:
        for vid in list(scores):
            v = data["videos_by_id"].get(vid) or {}
            if (v.get("major") or "").strip() == student["major"]:
                scores[vid] = round(scores[vid] + 0.06, 6)

    # 补齐 + 兜底
    for v in sorted(data["videos"], key=lambda x: -(x["popularity"] or 0)):
        if v["video_id"] in seen or v["video_id"] in reasons:
            continue
        reasons.setdefault(v["video_id"], "热门兜底")
        scores.setdefault(v["video_id"], 0.52)

    items = [{"video_id": k, "score": scores[k], "reason": reasons[k]}
             for k in sorted(scores, key=lambda k: -scores[k])]
    truth = ground_truth(data, student_id)
    return {
        "items": realize(items, data, topn),
        "explain": {
            "idea": "把『学生当前处在哪一学期』翻译成候选课：正在学 → 同课；学过 → 后继；下学期 → 预习。",
            "formula": "graph_score: 正在学=1.0, 后继=0.86, 预习=0.74, 兜底=0.52",
            "pros": "第一次出现个性化；推荐结果有『学习路径』感，解释性极强",
            "cons": "写死的规则不够灵活，遇到规则没覆盖的情况只能兜底",
            "source": "backend/recommender.py:67-126 professional_candidates() 多路召回",
        },
        "features_used": ["curriculum_graph"],
        "student_major": student["major"] if student else "",
    }


# ---------------------------------------------------------------- 模型 4：TF-IDF 内容相似度
def model_tfidf(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """看你学过什么，就推内容相似的课。

    做法：把每门课的课名/概念/头部视频标题拼成一段文本 → 切词 → TF-IDF 向量
    → 余弦相似度。好处是新课也能立刻被推荐（不需要有人学过它），
    这就是「冷启动」的解法之一。

    注意粒度是**课程**：你学过「高等数学」，就去找和「高等数学」最像的课，
    再取那些课的资源推给你。按视频逐条算相似度（4.5 万 × 4.5 万）在浏览器
    里是跑不出来的，而且「高数第 3 讲」和「线代第 7 讲」像不像没有意义。
    """
    seen = seen_ids(data, student_id, seen_override)
    course_similarity = get_course_index(data)["course_similarity"]

    # 看过的视频 → 反查所属课程（同一门课看多条只算一次）
    watched_courses: Dict[str, str] = {}
    # sorted 保证遍历顺序确定：下面的 scores 是「取最大值」，同分时先到者胜，
    # 不排序会让同一个学生每次拿到不同的结果（set 迭代顺序受哈希随机化影响）。
    for vid in sorted(seen):
        video = data["videos_by_id"].get(vid)
        if video:
            course = data["courses_by_id"].get(video["course_id"]) or {}
            watched_courses[video["course_id"]] = (course.get("name") or "")[:16]

    scores: Dict[str, float] = {}
    best_src: Dict[str, str] = {}
    for course_id, src_name in watched_courses.items():
        for item in course_similarity.get(course_id, []):
            # 相似课程的头部资源（头部 = 该课播放量最高的几条）
            for v in data["videos_by_course"].get(item["course_id"], [])[:8]:
                if v["video_id"] in seen:
                    continue
                if item["score"] > scores.get(v["video_id"], 0.0):
                    scores[v["video_id"]] = item["score"]
                    best_src[v["video_id"]] = f"与你学过的《{src_name}》内容相似"

    for v in sorted(data["videos"], key=lambda x: -(x["popularity"] or 0)):
        if v["video_id"] in seen or v["video_id"] in scores:
            continue
        scores[v["video_id"]] = 0.05
        best_src[v["video_id"]] = "兜底热门（你的学习记录太少，相似度失效）"

    items = [{"video_id": k, "score": scores[k], "reason": best_src.get(k, "")}
             for k in sorted(scores, key=lambda k: -scores[k])]
    truth = ground_truth(data, student_id)
    return {
        "items": realize(items, data, topn),
        "explain": {
            "idea": "文本 → 向量 → 余弦相似度。学过「高等数学」，就找和它最像的课。",
            "formula": "TF-IDF(t,d) = (1 + log tf) * idf ；sim(A,B) = cos = 向量点积（已归一化）",
            "pros": "新课零冷启动：只要它有文字介绍就能被推荐；不依赖别人行为",
            "cons": "只看『像不像』，不看『难不难』『该不该学』；容易推同一主题的重复课",
            "source": "backend/train_model.py:23-86 tokenize / build_tfidf / build_course_similarity",
        },
        "features_used": ["course.name", "course.concepts", "video.title"],
        "student_major": "",
        "extra": {
            "vocab_size": len(get_course_index(data)["tfidf"]["idf"]),
            "course_count": len(data["courses"]),
            "vector_dim_note": "稀疏向量，维度=词表大小；一门课一个向量",
        },
    }


# ---------------------------------------------------------------- 模型 5：ItemCF 协同过滤
def model_itemcf(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """ItemCF：和你看过的课「被同一批人喜欢」的课，推荐给你。

    和上一个模型的区别：TF-IDF 看的是「内容像不像」，ItemCF 看的是「行为上像不像」。
    项目里两者都用了 —— 前者解决新课冷启动，后者捕捉真实的共同偏好。

    实现上是「离线相似度表 + 在线查表」：表由
    ① 同课资源（0.92）+ ② 相似课程的头部资源（按课程相似度 × 0.85 打折）拼成，
    并且只给这个学生看过的视频建表 —— 这正是 backend/train_model.py:89-118 的做法。
    """
    seen = seen_ids(data, student_id, seen_override)
    sim_index = get_item_similarity(data, seen)

    scores: Dict[str, float] = {}
    reasons: Dict[str, str] = {}
    # sorted：同分时「先到先得」决定了推荐理由写的是哪一门课，
    # 不排序的话同一份输入每次跑出来的理由都不一样。
    for watched in sorted(seen):
        for item in sim_index.get(watched, []):
            vid = item["video_id"]
            if vid in seen:
                continue
            if item["score"] > scores.get(vid, 0.0):
                scores[vid] = item["score"]
                src = data["videos_by_id"].get(watched, {})
                reasons[vid] = f"学过《{src.get('title','')[:14]}》的人也在看"

    for v in sorted(data["videos"], key=lambda x: -(x["popularity"] or 0)):
        if v["video_id"] in seen or v["video_id"] in scores:
            continue
        scores[v["video_id"]] = 0.05
        reasons[v["video_id"]] = "兜底热门"

    items = [{"video_id": k, "score": scores[k], "reason": reasons.get(k, "")}
             for k in sorted(scores, key=lambda k: -scores[k])]
    truth = ground_truth(data, student_id)
    return {
        "items": realize(items, data, topn),
        "explain": {
            "idea": "先离线算出「课与课」的相似度表，再根据你的历史做实时查询。",
            "formula": "score = Σ sim(watched_i, candidate)（项目里取最大值而非求和）",
            "pros": "能发现内容不像但行为相关的课（跨领域关联）；响应快，相似度可离线算好",
            "cons": "新冷启动差（没人看过就算不出相似度）；容易越推越窄",
            "source": "backend/train_model.py:89-118 build_item_similarity；backend/recommender.py:316-320 在线查表召回",
        },
        "features_used": ["behavior", "learning_record"],
        "student_major": "",
        "extra": {"similarity_index_size": len(sim_index)},
    }


# ---------------------------------------------------------------- 模型 6：逻辑回归排序
# ---------------------------------------------------------------- 特征缓存
# 出处：backend/train_model.py:154-239 FeatureCache
#
# 为什么需要它：下面这几样东西**只跟学生有关，跟候选视频无关** ——
#   ① 平台偏好（要遍历整张 behavior_log）
#   ② 已学完的课程（要遍历 learning_record）
#   ③ 本专业本学期 / 下学期的课程集合（要遍历全部 898 门课）
#   ④ 每门课是几门课的先修（要再遍历 898 门课并逐条解析先修串）
# 不缓存的话，4.5 万条视频每条都要把这四件事重算一遍：实测单次 1.39 ms
#（其中 ④ 占 0.82 ms、① 占 0.33 ms、③ 占 0.22 ms），光造一遍训练样本就要 63 秒。
# 生产版的注释说得很直白：「原实现里每造一个样本就要查 4 次数据库，
# 样本一多训练时间就被数据库往返拖死」—— 这里正是那个优化。
class FeatureCache:
    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data
        self._students: Dict[str, Dict[str, Any]] = {}
        self._descendants: Dict[str, int] = {}

    def student_state(self, student: Dict[str, Any]) -> Dict[str, Any]:
        student_id = student["student_id"]
        if student_id in self._students:
            return self._students[student_id]

        platform_rows: Counter = Counter()
        for b in self.data["behavior"]:
            if b["student_id"] == student_id and b["event_type"] in ("play", "complete", "like", "favorite"):
                v = self.data["videos_by_id"].get(b["video_id"])
                if v:
                    platform_rows[v["platform"]] += 1
        total = sum(platform_rows.values()) or 1

        completed_courses: Set[str] = set()
        for r in self.data["learning"]:
            if r["student_id"] == student_id and r["status"] == "completed":
                v = self.data["videos_by_id"].get(r["video_id"])
                if v:
                    completed_courses.add(v["course_id"])

        major = student["major"]
        term = int(student["semester"])
        state = {
            # 与召回口径一致：按「学生自己的专业 + 学期」判断，
            # 而不是全库 course.status（那样所有学生共用同一份「正在学」集合）
            "current_courses": {
                c["course_id"] for c in self.data["courses"]
                if c.get("major") == major and c.get("semester") == term
            },
            "next_courses": {
                c["course_id"] for c in self.data["courses"]
                if c.get("major") == major and c.get("semester") == term + 1
            },
            "completed_courses": completed_courses,
            "platform_pref": {p: n / total for p, n in platform_rows.items()},
        }
        self._students[student_id] = state
        return state

    def descendants(self, course_id: str) -> int:
        """这门课是几门课的先修。只有 898 门课，缓存住就不用每条视频重扫课程表。"""
        if course_id not in self._descendants:
            count = 0
            for c in self.data["courses"]:
                if course_id in split_csv(c.get("prerequisites") or ""):
                    count += 1
            self._descendants[course_id] = count
        return self._descendants[course_id]

    def similarity(self, history: Iterable[str], video_id: str,
                   sim_index: Dict[str, List[Dict[str, float]]]) -> float:
        """第 5 维：与该学生「学过的视频」的最大相似度。

        history 必须真的传进来，否则这一维恒为 0，模型就学不到协同信号了。
        """
        best = 0.0
        for watched in history:
            for item in sim_index.get(watched, []):
                if item["video_id"] == video_id:
                    best = max(best, item["score"])
                    break  # 同一 video_id 在表里只出现一次（build_item_similarity 已按 dict 去重）
        return min(best, 1.0)


def curriculum_graph_score(state: Dict[str, Any], course: Dict[str, Any]) -> float:
    """培养方案契合度，与 backend/train_model.py:229-239 用同一套分档。"""
    course_id = course.get("course_id")
    if course_id in state["current_courses"]:
        return 1.0
    prereqs = split_csv(course.get("prerequisites") or "")
    if any(prereq in state["completed_courses"] for prereq in prereqs):
        return 0.82
    if course_id in state["next_courses"]:
        return 0.66
    return 0.4


def major_alignment(student: Dict[str, Any], video: Dict[str, Any], course: Dict[str, Any]) -> float:
    """专业匹配度。出处：backend/train_model.py:130-140"""
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


def _features(cache: FeatureCache, student: Dict[str, Any], video: Dict[str, Any],
              sim_index: Dict[str, List[Dict[str, float]]],
              history: Optional[Iterable[str]] = None) -> List[float]:
    """8 维特征，与 backend/train_model.py:242-260 feature_vector 一一对应。

    所有「按学生」的统计量都从 cache 取，不再每条视频重算一遍 ——
    这是模型 6/7 从 220 秒降到几秒的关键。
    """
    course = cache.data["courses_by_id"].get(video["course_id"], {})
    state = cache.student_state(student)
    return [
        math.log1p(video["popularity"] or 0) / 14.0,
        float(video["rating"] or 0) / 5.0,
        1.0 - float(video["is_paid"] or 0) * 0.22,
        curriculum_graph_score(state, course),
        cache.similarity(history or [], video["video_id"], sim_index),
        state["platform_pref"].get(video["platform"], 0.0),
        min(cache.descendants(course.get("course_id") or "") / 4.0, 1.0),
        major_alignment(student, video, course),
    ]


def train_logistic(samples: List[Tuple[List[float], int]], epochs: int = 420, lr: float = 0.18,
                   l2: float = 0.015) -> Dict[str, Any]:
    """从零实现逻辑回归（梯度下降）。出处：backend/train_model.py:167-191

    没有用 sklearn，纯手写 —— 这样每一步都看得见，也免了重依赖。

    与生产版唯一的实现差异：标准化挪到训练循环外面。
    原写法每轮都要对每个样本重做一遍 `(x - mean) / std`（220 轮 × N 条样本次
    列表推导），提前算好一次即可 —— 梯度下降的每一步和最终结果完全一致，
    但省掉了绝大部分常数开销。
    """
    dims = len(samples[0][0])
    n = len(samples)
    means = [sum(s[0][i] for s in samples) / n for i in range(dims)]
    stdevs = []
    for i in range(dims):
        var = sum((s[0][i] - means[i]) ** 2 for s in samples) / n
        stdevs.append(math.sqrt(var) or 1.0)

    # 预标准化：整轮训练只算一次
    xs = [[(row[i] - means[i]) / stdevs[i] for i in range(dims)] for row, _ in samples]
    labels = [label for _, label in samples]

    weights = [0.0] * dims
    bias = 0.0
    for _ in range(epochs):
        grad_w = [0.0] * dims
        grad_b = 0.0
        for x, label in zip(xs, labels):
            pred = sigmoid(sum(w * xi for w, xi in zip(weights, x)) + bias)
            err = pred - label
            for i in range(dims):
                grad_w[i] += err * x[i] + l2 * weights[i]  # l2 是正则项，防过拟合
            grad_b += err
        scale = 1 / n
        weights = [w - lr * g * scale for w, g in zip(weights, grad_w)]
        bias -= lr * grad_b * scale
    return {"weights": weights, "bias": bias, "means": means, "stdevs": stdevs}


def auc(samples: List[Tuple[List[float], int]], model: Dict[str, Any]) -> float:
    """AUC：随机挑一个正样本和一个负样本，模型给正样本打分更高的概率。

    出处：backend/train_model.py:194-210
    """
    scored = []
    for features, label in samples:
        x = [(features[i] - model["means"][i]) / model["stdevs"][i] for i in range(len(features))]
        score = sigmoid(sum(model["weights"][i] * x[i] for i in range(len(x))) + model["bias"])
        scored.append((score, label))
    pos = [s for s, l in scored if l == 1]
    neg = [s for s, l in scored if l == 0]
    if not pos or not neg:
        return 0.5
    wins = ties = 0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1
            elif p == q:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def _lr_predict(model: Dict[str, Any], features: List[float]) -> float:
    """用训练好的 LR 算「会点开」的概率。出处：backend/train_model.py:375-379"""
    x = [(features[i] - model["means"][i]) / model["stdevs"][i] for i in range(len(features))]
    return sigmoid(sum(model["weights"][i] * x[i] for i in range(len(x))) + model["bias"])


# 模型 6 的训练结果缓存。
# 留一法会把这个模型反复调用（每折一次），但**训练的样本不该随折变**：
# 正样本来自 learning_record 的进度，跟这一折遮住谁无关；8 个特征里也只有
# 第 5 维会受一点影响。逐折重训的代价是每次好几秒，折数一多就把评估拖垮 ——
# 生产版也正是为此在 evaluate_model.py:68 限制 MAX_LOO_FOLDS = 2，
# 注释写明「折数太大会把评估拖到十几分钟」。这里进一步把训练结果缓存住。
_LR_CACHE: Dict[str, Dict[str, Any]] = {}


def _train_lr(data: Dict[str, Any], student: Dict[str, Any],
              sim_index: Dict[str, List[Dict[str, float]]]) -> Dict[str, Any]:
    """训练这个学生的逻辑回归模型（带缓存，各折复用）。

    训练用的历史取「该学生的完整学习记录」，与留一法遮住谁无关，
    所以各折可以共用同一个模型 —— 这才是模块开头那句
    「为了响应速度省略逐折重训」应该有的样子。
    """
    key = "%s:%d" % (student["student_id"], len(data["videos"]))
    hit = _LR_CACHE.get(key)
    if hit is not None:
        return hit

    cache = FeatureCache(data)
    truth = ground_truth(data, student["student_id"])
    history = sorted(seen_ids(data, student["student_id"]))

    positives = [v for v in data["videos"] if v["video_id"] in truth]
    negatives = [v for v in data["videos"] if v["video_id"] not in truth]
    # 负样本等距下采样：4.5 万条里正样本只有几十条，逐条算完要 70 秒。
    # AUC 只关心正负样本的相对排序，等距采样不改变这个能力，也不引入随机性
    #（同一个学生每次训练得到完全一样的模型，指标可复现）。
    if len(negatives) > NEGATIVE_SAMPLE:
        step = len(negatives) / NEGATIVE_SAMPLE
        negatives = [negatives[int(i * step)] for i in range(NEGATIVE_SAMPLE)]

    samples: List[Tuple[List[float], int]] = [
        (_features(cache, student, v, sim_index, history), label)
        for label, group in ((1, positives), (0, negatives))
        for v in group
    ]

    # 轮数取够用即可（220 轮在这个数据量下已收敛）
    model = train_logistic(samples, epochs=220)
    model["train_auc"] = round(auc(samples, model), 4)
    model["sample_count"] = len(samples)
    model["positive_rate"] = round(sum(l for _, l in samples) / len(samples), 4)
    model["cache"] = cache
    _LR_CACHE[key] = model
    return model


def model_logistic(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """把 8 个特征喂给逻辑回归，让它自己学「什么样的课值得推」。

    这一步是真正的「机器学习」：不再是人写规则，而是从历史行为里学权重。
    """
    student = next((s for s in data["students"] if s["student_id"] == student_id), None)
    seen = seen_ids(data, student_id, seen_override)
    # 相似度表要同时覆盖「这一折的上下文」和「完整历史」：
    # 前者用于预测时排除已看，后者用于训练时的第 5 维特征（训练不随折变）
    sim_index = get_item_similarity(data, set(seen) | set(seen_ids(data, student_id)))

    # ---- 训练（带缓存，各折复用），详见 _train_lr ----
    model = _train_lr(data, student, sim_index)
    cache = model["cache"]

    # ---- 预测：用这一折的 seen 排除已看，逐条算 8 维特征 ----
    history = sorted(seen)
    items = []
    for v in data["videos"]:
        if v["video_id"] in seen:
            continue
        feats = _features(cache, student, v, sim_index, history=history)
        p = _lr_predict(model, feats)
        items.append({
            "video_id": v["video_id"],
            "score": p,
            "reason": "逻辑回归模型预测你会点开的概率",
            "feature_trace": dict(zip(FEATURE_NAMES, [round(f, 4) for f in feats])),
        })
    items.sort(key=lambda x: -x["score"])

    return {
        "items": realize(items, data, topn),
        "explain": {
            "idea": "把 8 个特征拼成向量喂给逻辑回归，输出「你会不会学这门课」的概率，按概率排序。",
            "formula": "p = sigmoid(w·x + b)，x 先做 z-score 标准化，损失含 L2 正则",
            "pros": "自动学习各特征权重（比如你会发现『先修关系』权重远高于『热度』）；可解释",
            "cons": "线性模型，表达不了特征交叉（比如『免费』×『正需要』的联合效果）",
            "source": "backend/train_model.py:242-295 feature_vector / train_logistic",
        },
        "features_used": FEATURE_NAMES,
        "student_major": student["major"] if student else "",
        "extra": {
            "weights": dict(zip(FEATURE_NAMES, [round(w, 4) for w in model["weights"]])),
            "bias": round(model["bias"], 4),
            "train_auc": model["train_auc"],
            "sample_count": model["sample_count"],
            "positive_rate": model["positive_rate"],
        },
    }


# ---------------------------------------------------------------- 模型 7：项目完整方案
def model_full(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """多路召回 + LR 打分 + 加权融合 + MMR 多样性重排 —— 项目线上真正在用的方案。

    相比上一个模型多了三件事：
      1. 先用「多路召回」把候选限流到几百条以内（不必给全库 4.5 万条打分）
      2. 打分不是纯用模型，而是把模型分和「召回理由强度」「课程质量」「是否免费」加权融合
      3. 最后做 MMR 重排，避免 9 条全是同一个平台的课、或同一门课霸屏
    """
    # 子模型也要用同一份 seen，否则留一法评估时口径不一致
    base = model_curriculum(data, student_id, len(data["videos"]), seen_override=seen_override)
    student = next((s for s in data["students"] if s["student_id"] == student_id), None)
    seen = seen_ids(data, student_id, seen_override)

    # ---- 召回限流：每门课最多 MAX_PER_COURSE 条，总量最多 MAX_RECALL 条 ----
    # 出处：backend/recommender.py:274-339 professional_candidates()
    # 生产版召回有两道闸 —— 每门课按课程类型给配额，以及总量到 260 就 break，
    # 实际候选量稳定在 114 个左右。教学版的 model_curriculum 不设限，
    # 会把几千个候选一股脑塞进来，而 MMR 是 O(n²)，直接把它拖到十几秒。
    per_course: Dict[str, int] = defaultdict(int)
    pool: List[Dict[str, Any]] = []
    for it in base["items"]:
        if it["video_id"] in seen:
            continue
        course_id = data["videos_by_id"][it["video_id"]]["course_id"]
        if per_course[course_id] >= MAX_PER_COURSE:
            continue
        per_course[course_id] += 1
        pool.append(it)
        if len(pool) >= MAX_RECALL:
            break

    # ---- 模型分：复用模型 6 训练好的 LR，但只给这批候选打分 ----
    # 不必再调 model_logistic 让它给全库 4.5 万条打分 —— 打分是逐视频独立的，
    # 最终用得到的只有候选这几百条的分数。
    sim_index = get_item_similarity(data, set(seen) | {it["video_id"] for it in pool})
    lr_model = _train_lr(data, student, sim_index)
    lr_cache = lr_model["cache"]
    history = sorted(seen)

    def recall_strength(reason: str) -> float:
        if "正在学习" in reason:
            return 1.0
        if "后继" in reason or "先修" in reason:
            return 0.86
        if "专业方向" in reason:
            return 0.78
        if "下学期" in reason or "预习" in reason:
            return 0.74
        if "相似" in reason or "看你" in reason or "也在看" in reason:
            return 0.68
        return 0.52

    scored = []
    for it in pool:
        vid = it["video_id"]
        v = data["videos_by_id"][vid]
        feats = _features(lr_cache, student, v, sim_index, history=history)
        ctr = _lr_predict(lr_model, feats)
        popularity = math.log1p(v["popularity"] or 0) / 14.0
        rating = float(v["rating"] or 0) / 5.0
        freshness = min(1.0, 0.58 * popularity + 0.42 * rating)
        free = 1.0 - float(v["is_paid"] or 0) * 0.22
        blended = 0.62 * ctr + 0.20 * recall_strength(it["reason"]) + 0.12 * freshness + 0.06 * free
        scored.append({
            "video_id": vid,
            # MMR 重排阶段要按平台打散、还要限制同一门课的条数，
            # 这两个字段必须提前带上（此时还没经过 realize() 补字段）
            "platform": v["platform"],
            "course_id": v["course_id"],
            "score": blended,
            "ctr": round(ctr, 4),
            "reason": it["reason"],
            "feature_trace": dict(zip(FEATURE_NAMES, [round(f, 4) for f in feats])),
        })

    scored.sort(key=lambda x: -x["score"])

    # ---- MMR 多样性重排：出处 backend/recommender.py:404-455 ----
    # 冗余度要在「候选之间」比较，相似度表的覆盖范围上面已经备好了（看过的 + 候选）。
    # 冗余度用「对已选项的历史最大值」增量维护，而不是每轮把全部已选项重新求一遍 max。
    # max 满足结合律，两者结果完全一致，但把每轮 O(已选数) 的扫描摊掉，
    # 整体从 O(n³) 降到 O(n²)（生产版实测 114 个候选由 128 ms 降到约 5 ms）。
    def similarity_between(left: str, right: str) -> float:
        for item in sim_index.get(left, []):
            if item["video_id"] == right:
                return float(item["score"])
        for item in sim_index.get(right, []):
            if item["video_id"] == left:
                return float(item["score"])
        return 0.0

    selected: List[Dict[str, Any]] = []
    pool = scored[:]
    platform_seen: Dict[str, int] = defaultdict(int)
    course_seen: Dict[str, int] = defaultdict(int)
    # None = 还没有任何已选项参与过冗余度计算，等价于原实现的 0.0 初值
    redundancy: List[Optional[float]] = [None] * len(pool)
    while pool and len(selected) < topn:
        best_i, best_score = 0, -1.0
        for i, item in enumerate(pool):
            current = redundancy[i]
            redundancy_value = 0.0 if current is None else current
            platform_penalty = 0.015 * platform_seen[item["platform"]]
            # 同一门课最多占 3 个位置：第 4 条起额外罚 0.25
            course_penalty = 0.06 * course_seen[item["course_id"]]
            course_penalty += 0.25 if course_seen[item["course_id"]] >= 3 else 0.0
            rs = 0.86 * item["score"] - 0.08 * redundancy_value - platform_penalty - course_penalty
            if rs > best_score:
                best_i, best_score = i, rs
        chosen = pool.pop(best_i)
        redundancy.pop(best_i)
        chosen["rerank_score"] = round(best_score, 4)
        platform_seen[chosen["platform"]] += 1
        course_seen[chosen["course_id"]] += 1
        selected.append(chosen)
        if pool:
            chosen_id = chosen["video_id"]
            for i, item in enumerate(pool):
                similarity = similarity_between(item["video_id"], chosen_id)
                if redundancy[i] is None or similarity > redundancy[i]:
                    redundancy[i] = similarity

    return {
        "items": realize(selected, data, topn),
        "explain": {
            "idea": "多路召回 → 特征工程 → LR 打分 → 多目标融合 → MMR 重排，五步流水线。",
            "formula": "最终分 = 0.62*模型CTR + 0.20*召回理由 + 0.12*课程质量 + 0.06*免费加成，再做 MMR",
            "pros": "既有个性化又有学习路径合理性，还能保证结果多样性（不会同平台霸屏）",
            "cons": "环节多、超参靠经验调；工程复杂度明显上升",
            "tradeoff": "和自己比一比：相比模型 6，这条链路的多样性通常明显上升、NDCG 却可能略降。"
                        "这是推荐系统里经典的「精度 vs 多样性」权衡 —— MMR 故意把最相似的结果往下压，"
                        "换来内容覆盖面。业务到底要「更准」还是「更丰富」，决定了这个天平往哪边倾。",
            "source": "backend/recommender.py:470-562 recommend_professional() 全链路；MMR 见同文件 404-455",
        },
        "features_used": FEATURE_NAMES + ["recall_reason", "mmr_rerank"],
        "student_major": student["major"] if student else "",
        "extra": {
            "pipeline": ["多路召回", "特征工程", "LR 排序", "多目标融合", "MMR 重排"],
            "recall_count": len(pool),
            "recall_before_limit": len(base["items"]),
            "train_auc": lr_model["train_auc"],
        },
    }


# ---------------------------------------------------------------- 注册表
MODELS: List[Dict[str, Any]] = [
    {"key": "popularity", "level": 1, "name": "模型 1：热度榜", "tag": "Baseline",
     "desc": "不看人，只看全网热度", "fn": model_popularity},
    {"key": "rating", "level": 2, "name": "模型 2：评分榜", "tag": "Baseline",
     "desc": "加了一个「质量」维度", "fn": model_rating},
    {"key": "curriculum", "level": 3, "name": "模型 3：课程图谱规则", "tag": "规则驱动",
     "desc": "第一次有了「你」的信息", "fn": model_curriculum},
    {"key": "tfidf", "level": 4, "name": "模型 4：TF-IDF 内容相似", "tag": "内容召回",
     "desc": "文本转向量，解决新课冷启动", "fn": model_tfidf},
    {"key": "itemcf", "level": 5, "name": "模型 5：ItemCF 协同过滤", "tag": "行为召回",
     "desc": "看过的课，找出相似课", "fn": model_itemcf},
    {"key": "logistic", "level": 6, "name": "模型 6：逻辑回归排序", "tag": "机器学习",
     "desc": "8 维特征自动学权重", "fn": model_logistic},
    {"key": "full", "level": 7, "name": "模型 7：完整推荐链路", "tag": "项目现网方案",
     "desc": "召回+排序+融合+MMR", "fn": model_full},
]


def run_model(key: str, student_id: str = "YY08", topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """运行指定模型。"""
    entry = next((m for m in MODELS if m["key"] == key), None)
    if entry is None:
        raise ValueError(f"未知模型：{key}")
    data = load_data()
    out = entry["fn"](data, student_id, topn)
    out["key"] = entry["key"]
    out["level"] = entry["level"]
    out["name"] = entry["name"]
    out["tag"] = entry["tag"]

    # 指标统一在外层计算。
    # 不能放进模型函数内部：leave_one_out 会反复调用模型函数，那样会无限递归。
    out["metrics"] = build_metrics(entry["fn"], out["items"], data, student_id)
    return out


def compare_all(student_id: str = "YY08") -> List[Dict[str, Any]]:
    """把所有模型跑一遍，返回指标对比表。"""
    data = load_data()
    rows = []
    for entry in MODELS:
        try:
            out = entry["fn"](data, student_id, 9)
            rows.append({
                "key": entry["key"],
                "level": entry["level"],
                "name": entry["name"],
                "tag": entry["tag"],
                "top1": out["items"][0]["title"][:26] if out["items"] else "",
                "metrics": build_metrics(entry["fn"], out["items"], data, student_id),
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"key": entry["key"], "level": entry["level"], "name": entry["name"],
                         "tag": entry["tag"], "error": str(exc)})
    return rows
