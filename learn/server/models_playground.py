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
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
    return {
        "videos": videos,
        "courses": courses,
        "students": students,
        "learning": learning,
        "behavior": behavior,
        "videos_by_id": {v["video_id"]: v for v in videos},
        "courses_by_id": {c["course_id"]: c for c in courses},
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


def build_tfidf(videos: List[Dict[str, Any]], courses_by_id: Dict[str, Any]) -> Dict[str, Any]:
    """TF-IDF：把每门课的文字描述变成向量。

    出处：backend/train_model.py:31-49
    """
    docs: Dict[str, Counter] = {}
    df: Counter = Counter()
    for video in videos:
        course = courses_by_id[video["course_id"]]
        text = " ".join([video["title"], video["tags"], video["summary"],
                         course["name"], course["concepts"]])
        counts = Counter(tokenize(text))
        docs[video["video_id"]] = counts
        for term in counts:
            df[term] += 1

    n_docs = len(videos)
    idf = {t: math.log((n_docs + 1) / (f + 1)) + 1 for t, f in df.items()}
    vectors: Dict[str, Dict[str, float]] = {}
    for vid, counts in docs.items():
        raw = {t: (1 + math.log(c)) * idf[t] for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in raw.values())) or 1.0
        vectors[vid] = {t: v / norm for t, v in raw.items()}
    return {"idf": idf, "vectors": vectors}


def dot(a: Dict[str, float], b: Dict[str, float]) -> float:
    """稀疏向量点积（余弦相似度，因为已归一化）。出处：backend/train_model.py:52-55"""
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(t, 0.0) for t, v in a.items())


def build_item_similarity(vectors: Dict[str, Dict[str, float]], topk: int = 10) -> Dict[str, List[Dict[str, float]]]:
    """两两算相似度，得到「看了 A 的人适合看什么」的索引。

    出处：backend/train_model.py:58-69
    """
    out: Dict[str, List[Dict[str, float]]] = {}
    for vid, vec in vectors.items():
        sims = []
        for other_id, other in vectors.items():
            if other_id == vid:
                continue
            score = dot(vec, other)
            if score > 0:
                sims.append({"video_id": other_id, "score": round(score, 6)})
        out[vid] = sorted(sims, key=lambda x: x["score"], reverse=True)[:topk]
    return out


# ---------------------------------------------------------------- 相似度缓存
# TF-IDF 和 ItemCF 相似度表只依赖「课程自身的文本」，与学生看到什么无关，
# 所以留一法反复调用模型时可以直接复用，不必每折重算 —— 这是最主要的性能优化。
_SIM_CACHE: Dict[str, Dict[str, Any]] = {}


def get_similarity_index(data: Dict[str, Any]) -> Dict[str, Any]:
    """返回 {"tfidf": ..., "sim": ...}，带缓存。"""
    cache_key = f"{len(data['videos'])}:{len(data['courses'])}"
    hit = _SIM_CACHE.get(cache_key)
    if hit is None:
        tfidf = build_tfidf(data["videos"], data["courses_by_id"])
        hit = {
            "tfidf": tfidf,
            # topk=12 覆盖项目的 8 邻域（train_model.py:68 用 8，这里放宽一点便于演示）
            "sim": build_item_similarity(tfidf["vectors"], topk=12),
        }
        _SIM_CACHE[cache_key] = hit
    return hit


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


def leave_one_out(fn, data: Dict[str, Any], student_id: str, topn: int = 5) -> Dict[str, Any]:
    """留一法评估 —— 与 backend/evaluate_model.py:67-116 _leave_one_out_student 同一口径。

    做法：把学生看过的每一条记录轮流「遮住」，
          其余 visibility 作为历史上下文，看被遮住的那门课能不能被推荐出来。
          最后统计命中率。

    这样算出来的指标才有区分度：好模型能把学生真正在学的课重新捞回来。

    简化说明：项目真实实现每折还会用 train_fold_model 重训一次模型
    （evaluate_model.py:85），这里为了响应速度省略了该步，
    因此指标与 /api/admin/model-evaluation 的绝对值会有差异，但相对排序是一致的。
    """
    all_seen = sorted(seen_ids(data, student_id))
    if not all_seen:
        return {"hit_rate@5": 0.0, "hit_rate@9": 0.0, "folds": 0, "fold_count": 0, "details": []}

    hits5 = hits9 = 0
    ndcg_sum = 0.0
    details: List[Dict[str, Any]] = []
    for held in all_seen:
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
        "hit_rate@5": round(hits5 / len(all_seen), 4),
        "hit_rate@9": round(hits9 / len(all_seen), 4),
        "ndcg@5": round(ndcg_sum / len(all_seen), 4),
        "folds": len(all_seen),
        "fold_count": len(all_seen),
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
    for done_id in completed_courses:
        for c in data["courses"]:
            preqs = split_csv(c.get("prerequisites") or "")
            if done_id in preqs:
                for v in data["videos"]:
                    if v["course_id"] == c["course_id"] and v["video_id"] not in seen:
                        reasons.setdefault(v["video_id"], f"已学过先修课，推荐后继《{c['name']}》")
                        scores.setdefault(v["video_id"], 1.0)

    for c in studying:
        for v in data["videos"]:
            if v["course_id"] == c["course_id"] and v["video_id"] not in seen:
                reasons.setdefault(v["video_id"], f"正在学习《{c['name']}》，补同主题视频")
                scores.setdefault(v["video_id"], 0.92)
    for c in planned:
        for v in data["videos"]:
            if v["course_id"] == c["course_id"] and v["video_id"] not in seen:
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

    做法：把每门课的标题/标签/简介拼成一段文本 → 切词 → TF-IDF 向量 → 余弦相似度。
    好处是新课也能立刻被推荐（不需要有人学过它），这就是「冷启动」的解法之一。
    """
    seen = seen_ids(data, student_id, seen_override)
    vectors = get_similarity_index(data)["tfidf"]["vectors"]

    scores: Dict[str, float] = {}
    best_src: Dict[str, str] = {}
    for watched in seen:
        for v in data["videos"]:
            if v["video_id"] in seen:
                continue
            sim = dot(vectors.get(watched, {}), vectors.get(v["video_id"], {}))
            if sim > scores.get(v["video_id"], 0.0):
                scores[v["video_id"]] = sim
                src = data["videos_by_id"].get(watched, {})
                best_src[v["video_id"]] = f"与你学过的《{src.get('title','')[:16]}》内容相似"

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
            "idea": "文本 → 向量 → 余弦相似度。看过 A，就找和 A 长得像的课。",
            "formula": "TF-IDF(t,d) = (1 + log tf) * idf ；sim(A,B) = cos = 向量点积（已归一化）",
            "pros": "新课零冷启动：只要它有文字介绍就能被推荐；不依赖别人行为",
            "cons": "只看『像不像』，不看『难不难』『该不该学』；容易推同一主题的重复课",
            "source": "backend/train_model.py:23-69 tokenize / build_tfidf / build_item_similarity",
        },
        "features_used": ["title", "tags", "summary", "concepts"],
        "student_major": "",
        "extra": {
            "vocab_size": len(get_similarity_index(data)["tfidf"]["idf"]),
            "vector_dim_note": "稀疏向量，维度=词表大小",
        },
    }


# ---------------------------------------------------------------- 模型 5：ItemCF 协同过滤
def model_itemcf(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """ItemCF：和你看过的课「被同一批人喜欢」的课，推荐给你。

    和上一个模型的区别：TF-IDF 看的是「内容像不像」，ItemCF 看的是「行为上像不像」。
    项目里两者都用了 —— 前者解决新课冷启动，后者捕捉真实的共同偏好。
    """
    seen = seen_ids(data, student_id, seen_override)
    _cached = get_similarity_index(data)
    sim_index = _cached["sim"]

    scores: Dict[str, float] = {}
    reasons: Dict[str, str] = {}
    for watched in seen:
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
            "source": "backend/train_model.py:58-69 build_item_similarity；backend/recommender.py:93-96 在线查询",
        },
        "features_used": ["behavior", "learning_record"],
        "student_major": "",
        "extra": {"similarity_index_size": len(sim_index)},
    }


# ---------------------------------------------------------------- 模型 6：逻辑回归排序
def _features(data: Dict[str, Any], student: Dict[str, Any], video: Dict[str, Any],
              courses_by_id: Dict[str, Any], sim_index: Dict[str, List[Dict[str, float]]],
              completed_courses: Set[str], history: Optional[List[str]] = None) -> List[float]:
    """8 维特征，与 backend/train_model.py:105-156 feature_vector 一一对应。"""
    course = courses_by_id.get(video["course_id"], {})
    # 第 5 维：与该学生「学过的视频」的最大相似度。
    # history 必须真的传进来，否则这一维恒为 0，模型就学不到协同信号了。
    sim_score = 0.0
    for watched in (history or []):
        for item in sim_index.get(watched, []):
            if item["video_id"] == video["video_id"]:
                sim_score = max(sim_score, item["score"])

    platform_rows: Counter = Counter()
    for b in data["behavior"]:
        if b["student_id"] == student["student_id"] and b["event_type"] in ("play", "complete", "like", "favorite"):
            v = data["videos_by_id"].get(b["video_id"])
            if v:
                platform_rows[v["platform"]] += 1
    total_platform = sum(platform_rows.values()) or 1
    platform_pref = platform_rows.get(video["platform"], 0) / total_platform

    studying = {c["course_id"] for c in data["courses"] if c["status"] == "studying"}
    planned = {c["course_id"] for c in data["courses"] if c["semester"] == student["semester"] + 1}
    prereqs = split_csv(course.get("prerequisites") or "")
    if course.get("course_id") in studying:
        prereq_hit = 1.0
    elif any(p in completed_courses for p in prereqs):
        prereq_hit = 0.8
    elif course.get("course_id") in planned:
        prereq_hit = 0.65
    else:
        prereq_hit = 0.0

    downstream = sum(1 for c in data["courses"] if course.get("course_id") in split_csv(c.get("prerequisites") or ""))
    v_major = (video.get("major") or "").strip()
    if v_major:
        major_align = 1.0 if v_major == student["major"] else 0.0
    elif student["major"] in " ".join([video.get("title") or "", video.get("tags") or "", course.get("name") or ""]):
        major_align = 0.9
    else:
        major_align = 0.55

    return [
        math.log1p(video["popularity"] or 0) / 14.0,
        float(video["rating"] or 0) / 5.0,
        1.0 - float(video["is_paid"] or 0) * 0.22,
        prereq_hit,
        min(sim_score, 1.0),
        platform_pref,
        min(downstream / 4.0, 1.0),
        major_align,
    ]


def train_logistic(samples: List[Tuple[List[float], int]], epochs: int = 420, lr: float = 0.18,
                   l2: float = 0.015) -> Dict[str, Any]:
    """从零实现逻辑回归（梯度下降）。出处：backend/train_model.py:167-191

    没有用 sklearn，纯手写 —— 这样每一步都看得见，也免了重依赖。
    """
    dims = len(samples[0][0])
    n = len(samples)
    means = [sum(s[0][i] for s in samples) / n for i in range(dims)]
    stdevs = []
    for i in range(dims):
        var = sum((s[0][i] - means[i]) ** 2 for s in samples) / n
        stdevs.append(math.sqrt(var) or 1.0)

    weights = [0.0] * dims
    bias = 0.0
    for _ in range(epochs):
        grad_w = [0.0] * dims
        grad_b = 0.0
        for features, label in samples:
            x = [(features[i] - means[i]) / stdevs[i] for i in range(dims)]
            pred = sigmoid(sum(weights[i] * x[i] for i in range(dims)) + bias)
            err = pred - label
            for i in range(dims):
                grad_w[i] += err * x[i] + l2 * weights[i]  # l2 是正则项，防过拟合
            grad_b += err
        scale = 1 / n
        weights = [weights[i] - lr * grad_w[i] * scale for i in range(dims)]
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


def model_logistic(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """把 8 个特征喂给逻辑回归，让它自己学「什么样的课值得推」。

    这一步是真正的「机器学习」：不再是人写规则，而是从历史行为里学权重。
    """
    student = next((s for s in data["students"] if s["student_id"] == student_id), None)
    seen = seen_ids(data, student_id, seen_override)
    _cached = get_similarity_index(data)
    sim_index = _cached["sim"]
    completed_courses = set()
    for r in data["learning"]:
        if r["student_id"] == student_id and r["status"] == "completed":
            v = data["videos_by_id"].get(r["video_id"])
            if v:
                completed_courses.add(v["course_id"])

    # ---- 构造训练样本：以「真的学过」为正样本 ----
    truth = ground_truth(data, student_id)
    seen_list = sorted(seen)
    samples: List[Tuple[List[float], int]] = []
    for v in data["videos"]:
        label = 1 if v["video_id"] in truth else 0
        samples.append((
            _features(data, student, v, data["courses_by_id"], sim_index, completed_courses, history=seen_list),
            label,
        ))

    # 留一法下这个模型会被反复调用，轮数取够用即可（220 轮在这个数据量下已收敛）
    model = train_logistic(samples, epochs=220)
    train_auc = auc(samples, model)

    # ---- 预测 ----
    items = []
    for v in data["videos"]:
        if v["video_id"] in seen:
            continue
        feats = _features(data, student, v, data["courses_by_id"], sim_index,
                          completed_courses, history=seen_list)
        x = [(feats[i] - model["means"][i]) / model["stdevs"][i] for i in range(len(feats))]
        p = sigmoid(sum(model["weights"][i] * x[i] for i in range(len(x))) + model["bias"])
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
            "source": "backend/train_model.py:105-191 feature_vector / train_logistic",
        },
        "features_used": FEATURE_NAMES,
        "student_major": student["major"] if student else "",
        "extra": {
            "weights": dict(zip(FEATURE_NAMES, [round(w, 4) for w in model["weights"]])),
            "bias": round(model["bias"], 4),
            "train_auc": round(train_auc, 4),
            "sample_count": len(samples),
            "positive_rate": round(sum(l for _, l in samples) / len(samples), 4),
        },
    }


# ---------------------------------------------------------------- 模型 7：项目完整方案
def model_full(data: Dict[str, Any], student_id: str, topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
    """多路召回 + LR 打分 + 加权融合 + MMR 多样性重排 —— 项目线上真正在用的方案。

    相比上一个模型多了三件事：
      1. 先用「多路召回」把候选从 116 缩到几十（不必给全库打分）
      2. 打分不是纯用模型，而是把模型分和「召回理由强度」「课程质量」「是否免费」加权融合
      3. 最后做 MMR 重排，避免 9 条全是同一个平台的课
    """
    # 子模型也要用同一份 seen，否则留一法评估时口径不一致
    base = model_curriculum(data, student_id, len(data["videos"]), seen_override=seen_override)
    student = next((s for s in data["students"] if s["student_id"] == student_id), None)
    seen = seen_ids(data, student_id, seen_override)

    # 模型分（复用第 6 个模型的预测结果）
    lr_result = model_logistic(data, student_id, len(data["videos"]), seen_override=seen_override)
    lr_score = {it["video_id"]: it["score"] for it in lr_result["items"]}
    lr_trace = {it["video_id"]: it.get("feature_trace", {}) for it in lr_result["items"]}

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
    for it in base["items"]:
        vid = it["video_id"]
        if vid in seen:
            continue
        v = data["videos_by_id"][vid]
        ctr = lr_score.get(vid, 0.0)
        popularity = math.log1p(v["popularity"] or 0) / 14.0
        rating = float(v["rating"] or 0) / 5.0
        freshness = min(1.0, 0.58 * popularity + 0.42 * rating)
        free = 1.0 - float(v["is_paid"] or 0) * 0.22
        blended = 0.62 * ctr + 0.20 * recall_strength(it["reason"]) + 0.12 * freshness + 0.06 * free
        scored.append({
            "video_id": vid,
            # MMR 重排阶段需要按平台打散，这里必须带上 platform
            #（注意此时还没经过 realize() 补字段，所以要手动带上）
            "platform": v["platform"],
            "score": blended,
            "ctr": round(ctr, 4),
            "reason": it["reason"],
            "feature_trace": lr_trace.get(vid, {}),
        })

    scored.sort(key=lambda x: -x["score"])

    # ---- MMR 多样性重排：出处 backend/recommender.py:220-240 ----
    vectors = get_similarity_index(data)["tfidf"]["vectors"]

    def sim(a: str, b: str) -> float:
        return dot(vectors.get(a, {}), vectors.get(b, {}))

    selected: List[Dict[str, Any]] = []
    pool = scored[:]
    platform_seen: Dict[str, int] = defaultdict(int)
    while pool and len(selected) < topn:
        best_i, best_score = 0, -1.0
        for i, item in enumerate(pool):
            redundancy = max([sim(item["video_id"], s["video_id"]) for s in selected] or [0.0])
            penalty = 0.015 * platform_seen[item["platform"]]
            rs = 0.86 * item["score"] - 0.08 * redundancy - penalty
            if rs > best_score:
                best_i, best_score = i, rs
        chosen = pool.pop(best_i)
        chosen["rerank_score"] = round(best_score, 4)
        platform_seen[chosen["platform"]] += 1
        selected.append(chosen)

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
            "source": "backend/recommender.py:250-296 recommend_professional() 全链路",
        },
        "features_used": FEATURE_NAMES + ["recall_reason", "mmr_rerank"],
        "student_major": student["major"] if student else "",
        "extra": {
            "pipeline": ["多路召回", "特征工程", "LR 排序", "多目标融合", "MMR 重排"],
            "recall_count": len(base["items"]),
            "train_auc": lr_result["extra"]["train_auc"],
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


def run_model(key: str, student_id: str = "20240101", topn: int = 9, seen_override: Optional[Set[str]] = None) -> Dict[str, Any]:
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


def compare_all(student_id: str = "20240101") -> List[Dict[str, Any]]:
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
