"""把岳阳学院真实的专业与培养方案落库，并为每门课扩充 50 个课程资源。

设计要点（答辩时要能讲清楚，避免被质疑"数据是编的"）：

1. 课程体系是真实的：25 个专业、898 门课来自 yueyang_curriculum.py。
   专业清单出自岳阳学院招生信息网 2026 招生专业页，课程清单出自教务处培养方案（简）
   与各学院专业简介。开课学期是按学制规律推断的，已在数据里标注。

2. 资源池与真实采集严格区分：每条 video 都有 data_origin 字段
   - live       —— 联网真实采集（B站公开搜索），带 source_collected_at
   - curriculum —— 按培养方案扩充的课程资源池，不冒充真实采集，
                  source_collected_at 保持为空，数据质量页单列说明
   这样 /api/admin/data-quality 的"真实采集"口径不会被污染。

3. 每门课 50 个资源，按平台真实分布切分：
   B站 20 / 中国大学MOOC 15 / 极客时间 5 / 学堂在线 5 / 网易云课堂 5。
   同一门课的 50 条资源在讲师、机构、集数、播放量、年份上都有区分度。

4. 真实采集的 B 站视频不丢弃：重建课程体系后按标题重新挂到新课程上
   （relink_live_videos），保持"真实数据"这一部分的连续性。
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import db as _db
from .yueyang_curriculum import (
    ADMISSION_SOURCE_URL,
    MAJORS,
    SELECTION_REQUIREMENTS,
    SCHOOL_NAME,
    curriculum_for,
)

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = _db.DB_PATH
ARTIFACT_DIR = ROOT / "data" / "artifacts"

RESOURCES_PER_COURSE = 50

# (平台, 该课分配资源数, 付费比例, 播放量量级)
PLATFORM_PLAN: List[Tuple[str, int, float, int]] = [
    ("B站", 20, 0.0, 260_000),
    ("MOOC", 15, 0.0, 92_000),
    ("极客时间", 5, 1.0, 48_000),
    ("学堂在线", 5, 0.0, 63_000),
    ("网易云课堂", 5, 0.35, 120_000),
]

STYLE_WORDS = [
    "全程精讲", "零基础入门", "考点串讲", "项目实战", "期末冲刺",
    "习题精讲", "图解版", "深度解析", "名师课堂", "速成班",
    "一轮复习", "案例驱动", "手把手教学", "面试高频", "实验指导",
]

SURNAMES = ["王", "李", "张", "刘", "陈", "杨", "黄", "周", "吴", "徐",
            "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗", "郑"]

HUNAN_UNIVERSITIES = [
    "湖南理工学院", "湖南大学", "中南大学", "湘潭大学", "长沙理工大学",
    "湖南师范大学", "南华大学", "湖南科技大学", "吉首大学", "湖南工商大学",
]

ORG_POOL = {
    "MOOC": ["中国大学MOOC 国家精品课", "中国大学MOOC 省级一流课程", "中国大学MOOC 校级精品课"],
    "学堂在线": ["学堂在线 精品课程", "学堂在线 名校公开课", "学堂在线 认证课程"],
    "B站": ["哔哩哔哩 知识区", "哔哩哔哩 学习频道", "哔哩哔哩 校园学习"],
    "极客时间": ["极客时间 专栏", "极客时间 视频课", "极客时间 训练营"],
    "网易云课堂": ["网易云课堂 微专业", "网易云课堂 精品课", "网易云课堂 认证课"],
}

COVER_PALETTE = [
    "#5B8DEF", "#7C5CFF", "#00B8A9", "#F6A623", "#FF6B9D",
    "#43C6AC", "#8E7CFF", "#FF8A5B", "#2EC4B6", "#E4572E",
    "#3A86FF", "#8338EC", "#06D6A0", "#FB5607", "#118AB2",
]

URL_POOL = {
    "B站": "https://search.bilibili.com/all?keyword={kw}",
    "MOOC": "https://www.icourse163.org/search.htm?search={kw}",
    "极客时间": "https://time.geekbang.org/search?q={kw}",
    "学堂在线": "https://www.xuetangx.com/search?query={kw}",
    "网易云课堂": "https://study.163.com/search.htm?keyword={kw}",
}

# 旧版演示数据里的专业名 -> 岳阳学院 2026 招生专业。
# 岳阳学院信息工程学院只有「计算机科学与技术 / 电子信息工程 / 电子科学与技术」三个专业，
# 原先的软件工程、人工智能、数据科学与大数据技术、网络工程均无对应招生专业，
# 它们采集到的公开课按学科归属并入计算机科学与技术。
LEGACY_MAJOR_MAP = {
    "软件工程": "计算机科学与技术",
    "人工智能": "计算机科学与技术",
    "数据科学与大数据技术": "计算机科学与技术",
    "网络工程": "电子信息工程",
    "计算机科学与技术": "计算机科学与技术",
    "电子信息工程": "电子信息工程",
}


# ---------------------------------------------------------------- 基础工具

def connect() -> sqlite3.Connection:
    return _db.connection()


def stable_rand(*parts: Any) -> random.Random:
    """按内容派生的确定性随机源：同样输入永远得到同样的资源池。"""
    digest = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:12], 16))


def course_id_for(major_index: int, course_index: int) -> str:
    return f"YY{major_index:02d}{course_index:02d}"


def major_id_for(index: int) -> str:
    return f"MAJ{index:02d}"


def _keywords(name: str) -> List[str]:
    cleaned = name.replace("（一）", "").replace("（二）", "")
    cleaned = cleaned.replace("（上）", "").replace("（下）", "")
    cleaned = cleaned.replace("(", "").replace(")", "")
    tokens = [cleaned]
    for sep in ("与", "及", "和"):
        if sep in cleaned:
            tokens.extend(part for part in cleaned.split(sep) if part)
    return [t for t in dict.fromkeys(tokens) if t]


# 统计页 / 推荐链路里高频出现的聚合与连接模式，逐条对应一个覆盖索引。
# 加索引前 video_coverage 的 25 次循环 COUNT 与 GROUP BY 全表扫描合计要 600+ ms，
# 加上这些覆盖索引后同一个查询可以在索引内完成，不再回表读 summary/tags 这些大字段。
INDEXES: List[Tuple[str, str]] = [
    ("idx_video_course", "video(course_id)"),
    ("idx_video_major", "video(major)"),
    ("idx_course_major_semester", "course(major, semester)"),
    # 统计页「每门课的平台分布」：6 路相关子查询全部落在 (course_id, platform) 上
    ("idx_video_course_platform", "video(course_id, platform)"),
    # 统计页「每门课取热门资源」ORDER BY popularity DESC LIMIT n
    ("idx_video_course_popularity", "video(course_id, popularity DESC)"),
    # 「各专业 × 平台」矩阵：索引本身已按 (major, platform) 有序，GROUP BY 不再建临时 B 树
    ("idx_video_major_platform", "video(major, platform)"),
    # 培养方案资源量按专业统计
    ("idx_video_major_origin", "video(major, data_origin)"),
    # 平台分布聚合只需要 platform + popularity 两列，窄覆盖索引让全表聚合不必回表读
    # summary / tags 这些大字段（61 ms -> 约 10 ms）
    ("idx_video_platform_popularity", "video(platform, popularity)"),
    # 真实采集样本只有 97 条，局部索引让「只数 live」的查询不去碰 4.5 万行的主表；
    # 把 major 一起放进索引，按专业分组时可以直接在索引内完成
    ("idx_video_live_major",
     "video(source_collected_at, major) WHERE source_collected_at IS NOT NULL"),
    # recommend_log 原本一个索引都没有，sync / 评估都要全表扫
    ("idx_recommend_log_student", "recommend_log(student_id, candidate_id)"),
    ("idx_recommend_log_synthetic", "recommend_log(is_synthetic)"),
]

# 被更合适的索引取代、或者经实测确认没有收益的索引，需要清掉。
# drop 索引只改元数据不碰数据，代价很小。
#
# 后四个是「小表索引」：learning_record 只有 499 行、achievement 353 行、
# job 150 行、exam_subject 35 行，主键索引的前缀本来就能覆盖 student_id / major
# 这类过滤条件。实测它们并不能加快查询，却会让 SQLite 换一条执行路径 ——
# 例如 user_profile 的 records 会从「按 video_id 有序」变成表扫描顺序，
# 属于没有收益、只带来行为漂移的改动，所以统一撤掉。
OBSOLETE_INDEXES: List[str] = [
    "idx_video_live_collected",
    "idx_learning_record_student_progress",
    "idx_achievement_student",
    "idx_job_major",
    "idx_exam_subject_year",
]


def ensure_indexes(con: sqlite3.Connection) -> List[str]:
    """幂等补齐索引。返回本次新建的索引名（已存在的跳过）。"""
    existing = {
        row["name"]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    for name in OBSOLETE_INDEXES:
        if name in existing:
            con.execute(f"DROP INDEX IF EXISTS {name}")
            existing.discard(name)
    created: List[str] = []
    for name, target in INDEXES:
        if name in existing:
            continue
        try:
            con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
            created.append(name)
        except sqlite3.OperationalError:
            # 列还不存在（例如尚未迁移的旧库）时跳过，等对应迁移跑完再来
            continue
    if created:
        con.commit()
    con.commit()
    return created


def ensure_curriculum_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS major (
          major_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          college TEXT NOT NULL,
          category TEXT NOT NULL,
          tuition INTEGER,
          total_credits REAL,
          selection_requirement TEXT,
          admission_source TEXT,
          original_source TEXT,
          course_count INTEGER DEFAULT 0,
          resource_count INTEGER DEFAULT 0
        );
        """
    )
    video_cols = {row["name"] for row in con.execute("PRAGMA table_info(video)").fetchall()}
    for col, ddl in {"data_origin": "TEXT DEFAULT 'curriculum'",
                     "course_name": "TEXT"}.items():
        if col not in video_cols:
            con.execute(f"ALTER TABLE video ADD COLUMN {col} {ddl}")
    course_cols = {row["name"] for row in con.execute("PRAGMA table_info(course)").fetchall()}
    for col in ("course_kind", "major_id", "major_name"):
        if col not in course_cols:
            con.execute(f"ALTER TABLE course ADD COLUMN {col} TEXT")
    ensure_indexes(con)
    con.commit()


# ---------------------------------------------------------------- 课程体系落库

def rebuild_curriculum(con: sqlite3.Connection) -> Dict[str, int]:
    """重建 major / course 两张表（视频由 seed_resources 单独生成）。"""
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("DELETE FROM major")
    con.execute("DELETE FROM video WHERE video_id LIKE 'YY%'")
    con.execute("DELETE FROM course WHERE course_id LIKE 'YY%'")

    course_total = 0
    for major_index, major in enumerate(MAJORS, start=1):
        major_id = major_id_for(major_index)
        con.execute(
            """
            INSERT INTO major (major_id,name,college,category,tuition,total_credits,
                               selection_requirement,admission_source,original_source)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (major_id, major["name"], major["college"], major["category"],
             major["tuition"], major["total_credits"],
             SELECTION_REQUIREMENTS.get(major["name"], ""),
             ADMISSION_SOURCE_URL, major["original_source"]),
        )

        table = curriculum_for(major["name"])
        # 按「课程类型 + 学期」建索引，先修关系只在同类型课程内串联
        by_kind_term: Dict[str, Dict[int, List[str]]] = {}
        for course_index, (name, term, credits, kind) in enumerate(table, start=1):
            course_id = course_id_for(major_index, course_index)
            by_kind_term.setdefault(kind, {}).setdefault(term, []).append(course_id)
            con.execute(
                """
                INSERT INTO course (course_id,name,semester,credits,status,concepts,
                                    prerequisites,major,major_id,major_name,course_kind)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (course_id, name, term, credits, "planned",
                 " ".join([name] + _keywords(name)),
                 "", major["name"], major_id, major["name"], kind),
            )
            course_total += 1

        # 先修关系：只给「学科基础」和「专业核心」建立同类型学期链。
        # 通识必修（思政、英语、体育等）之间没有先后依赖，强行串联会造出
        # "军事理论与训练 -> 中国近现代史纲要" 这类无意义的"后继课"。
        for kind in ("学科基础", "专业核心"):
            term_map = by_kind_term.get(kind, {})
            terms = sorted(term_map)
            for pos, term in enumerate(terms):
                if pos == 0:
                    continue
                prereq = ",".join(term_map[terms[pos - 1]][:2])
                for course_id in term_map[term]:
                    con.execute("UPDATE course SET prerequisites=? WHERE course_id=?",
                                (prereq, course_id))

        con.execute("UPDATE major SET course_count=? WHERE major_id=?",
                    (len(table), major_id))
        con.execute(
            """
            INSERT OR REPLACE INTO student
              (student_id,name,school,major,grade,semester,level,xp,next_xp)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (f"YY{major_index:02d}", f"{major['name'][:2]}同学", SCHOOL_NAME,
             major["name"], "大二", 3, 5, 980, 1400),
        )
    con.execute(
        "INSERT OR REPLACE INTO collector_state VALUES (?,?,?)",
        ("target_majors", json.dumps([m["name"] for m in MAJORS], ensure_ascii=False), now),
    )
    con.commit()
    return {"majors": len(MAJORS), "courses": course_total}


# ---------------------------------------------------------------- 资源池生成

def _resource_title(platform: str, course_name: str, rng: random.Random, seq: int) -> Tuple[str, str, str]:
    style = rng.choice(STYLE_WORDS)
    surname = rng.choice(SURNAMES)
    school = rng.choice(HUNAN_UNIVERSITIES)
    org = rng.choice(ORG_POOL[platform])
    if platform == "MOOC":
        return f"{school}《{course_name}》{style}", f"{surname}老师", org
    if platform == "B站":
        teacher = f"{surname}学长" if rng.random() < 0.45 else f"{surname}老师"
        return f"{course_name}｜{style}（{seq:02d}）", teacher, org
    if platform == "极客时间":
        return f"{course_name}·{style}实战专栏", f"{surname}老师", org
    if platform == "学堂在线":
        return f"《{course_name}》{school}{style}", f"{surname}副教授", org
    return f"{course_name}{style}训练营", f"{surname}老师", org


def _resource_url(platform: str, course_name: str) -> str:
    from urllib.parse import quote
    return URL_POOL[platform].format(kw=quote(course_name))


def seed_resources(con: sqlite3.Connection, per_course: int = RESOURCES_PER_COURSE) -> int:
    """为每门课生成 per_course 条课程资源。"""
    courses = [
        dict(row)
        for row in con.execute(
            """
            SELECT c.course_id, c.name, c.semester, c.major, c.major_id, c.course_kind
            FROM course c
            WHERE c.course_id LIKE 'YY%'
            ORDER BY c.course_id
            """
        ).fetchall()
    ]
    inserted = 0
    for course in courses:
        rng = stable_rand("course", course["course_id"])
        plan: List[Tuple[str, int, float, int]] = []
        for platform, count, paid_ratio, pop_base in PLATFORM_PLAN:
            plan.append((platform, max(1, count + rng.choice([-2, -1, 0, 0, 1, 2])),
                         paid_ratio, pop_base))
        diff = per_course - sum(item[1] for item in plan)
        head = plan[0]
        plan[0] = (head[0], max(1, head[1] + diff), head[2], head[3])

        seq = 0
        for platform, count, paid_ratio, pop_base in plan:
            for _ in range(count):
                seq += 1
                title, teacher, org = _resource_title(platform, course["name"], rng, seq)
                is_paid = 1 if rng.random() < paid_ratio else 0
                popularity = int(pop_base * (0.35 + rng.random() ** 2 * 2.6))
                rating = round(min(4.9, max(4.1, 4.35 + rng.random() * 0.6 - (0.12 if is_paid else 0))), 1)
                episodes = rng.choice([12, 16, 20, 24, 28, 32, 36, 42, 48, 56, 64, 80, 96])
                total_minutes = episodes * rng.randint(14, 46)
                duration = f"{total_minutes // 60}:{total_minutes % 60:02d}"
                days_ago = rng.randint(30, 1900)
                upload = (datetime.now() - timedelta(days=days_ago)).date().isoformat()
                like = int(popularity * (0.03 + rng.random() * 0.07))
                fav = int(like * (0.25 + rng.random() * 0.5))
                video_id = f"{course['course_id']}-R{seq:02d}"
                color = COVER_PALETTE[(seq + int(course["course_id"][-2:])) % len(COVER_PALETTE)]
                tags = " ".join(dict.fromkeys([
                    course["name"], course["major"], platform, course["course_kind"],
                    rng.choice(STYLE_WORDS),
                ]))
                summary = (
                    f"面向{course['major']}专业《{course['name']}》"
                    f"（第{course['semester']}学期 · {course['course_kind']}），"
                    f"{platform}平台共 {episodes} 讲。"
                )
                con.execute(
                    """
                    INSERT OR REPLACE INTO video (
                      video_id,course_id,title,platform,is_paid,teacher,org,popularity,rating,
                      episodes,duration,tags,summary,source_url,cover_color,upload_date,
                      like_count,favorite_count,major,source_raw_id,source_collected_at,
                      data_origin,course_name
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (video_id, course["course_id"], title, platform, is_paid, teacher, org,
                     popularity, rating, episodes, duration, tags, summary,
                     _resource_url(platform, course["name"]), color, upload,
                     like, fav, course["major"], None, None,
                     "curriculum", course["name"]),
                )
                inserted += 1
    con.commit()
    return inserted


# ---------------------------------------------------------------- 真实采集视频重新挂课

def _overlap(text: str, name: str) -> float:
    """标题与课程名的字符重叠度，用于匹配不上关键词时的兜底挂课。"""
    left = {ch for ch in name if "\u4e00" <= ch <= "\u9fff"}
    right = {ch for ch in text if "\u4e00" <= ch <= "\u9fff"}
    if not left:
        return 0.0
    return len(left & right) / len(left)


def relink_live_videos(con: sqlite3.Connection) -> Dict[str, int]:
    """把联网采集的 BILI_* 视频按标题匹配到新课程，并归一到岳阳学院的招生专业。"""
    courses = [
        dict(row)
        for row in con.execute(
            """SELECT course_id, name, major, semester, course_kind
               FROM course WHERE course_id LIKE 'YY%'"""
        ).fetchall()
    ]
    index: List[Tuple[str, str, str]] = []
    for course in courses:
        for token in _keywords(course["name"]):
            if len(token) >= 2:
                index.append((token, course["course_id"], course["major"]))
    index.sort(key=lambda item: len(item[0]), reverse=True)

    linked = matched = fallback = 0
    for video in con.execute(
        """SELECT video_id, title, tags, major FROM video
           WHERE video_id LIKE 'BILI_%' OR video_id LIKE 'MOOC_%' OR video_id LIKE 'GEEK_%'"""
    ).fetchall():
        raw_major = (video["major"] or "").strip()
        new_major = LEGACY_MAJOR_MAP.get(raw_major, "计算机科学与技术" if not raw_major else raw_major)
        text = f"{video['title'] or ''} {video['tags'] or ''}"

        best_course, best_len = "", 0
        for token, course_id, course_major in index:
            if course_major != new_major:
                continue
            if token in text and len(token) > best_len:
                best_course, best_len = course_id, len(token)

        if best_course:
            matched += 1
        else:
            # 兜底：在该专业核心课里挑标题字符重叠度最高的
            candidates = [
                c for c in courses
                if c["major"] == new_major and c["course_kind"] == "专业核心"
            ]
            if not candidates:
                candidates = [c for c in courses if c["major"] == new_major]
            if candidates:
                best = max(candidates, key=lambda c: _overlap(text, c["name"]))
                best_course = best["course_id"]
                fallback += 1

        if best_course:
            con.execute(
                """UPDATE video SET course_id=?, major=?, data_origin='live',
                   course_name=(SELECT name FROM course WHERE course_id=?)
                   WHERE video_id=?""",
                (best_course, new_major, best_course, video["video_id"]),
            )
            linked += 1
    con.commit()
    return {"linked": linked, "matched_by_title": matched, "fallback": fallback}


def purge_legacy_curriculum(con: sqlite3.Connection) -> Dict[str, int]:
    """清掉旧版演示数据残留：非 YY 课程、非 YY 学生账号及其关联记录。"""
    legacy_courses = [
        row["course_id"]
        for row in con.execute(
            "SELECT course_id FROM course WHERE course_id NOT LIKE 'YY%'"
        ).fetchall()
    ]
    legacy_students = [
        row["student_id"]
        for row in con.execute(
            "SELECT student_id FROM student WHERE student_id NOT LIKE 'YY%'"
        ).fetchall()
    ]
    removed = {"courses": 0, "students": 0, "videos_dropped": 0}
    if legacy_courses:
        marks = ",".join("?" * len(legacy_courses))
        removed["videos_dropped"] = con.execute(
            f"SELECT COUNT(*) FROM video WHERE course_id IN ({marks})", legacy_courses
        ).fetchone()[0]
        con.execute(f"DELETE FROM video WHERE course_id IN ({marks})", legacy_courses)
        con.execute(f"DELETE FROM course WHERE course_id IN ({marks})", legacy_courses)
        removed["courses"] = len(legacy_courses)
    if legacy_students:
        marks = ",".join("?" * len(legacy_students))
        for table in ("achievement", "learning_record", "behavior_log", "recommend_log"):
            con.execute(f"DELETE FROM {table} WHERE student_id IN ({marks})", legacy_students)
        con.execute(f"DELETE FROM student WHERE student_id IN ({marks})", legacy_students)
        removed["students"] = len(legacy_students)
    con.commit()
    return removed


# ---------------------------------------------------------------- 学习记录与曝光样本

def seed_learning_records_for(
    con: sqlite3.Connection, student_id: str, major: str, semester: int
) -> Dict[str, int]:
    """按培养方案给单个学生造学习行为：
    往期学期课程基本修完，本学期课程正在学（15%~85% 进度）。

    这样"本学期的课 + 下学期的课"才有真实依据 —— 推荐直接由培养方案的学期进度驱动。
    """
    now = datetime.now()
    rng = stable_rand("learn", student_id)
    rows = con.execute(
        """
        SELECT c.course_id, c.semester, c.name, c.course_kind,
               (SELECT video_id FROM video v WHERE v.course_id=c.course_id
                ORDER BY v.popularity DESC LIMIT 1) AS top_video
        FROM course c WHERE c.major=? AND c.semester<=? ORDER BY c.semester
        """,
        (major, semester),
    ).fetchall()
    counts = {"completed": 0, "learning": 0}
    for row in rows:
        if not row["top_video"]:
            continue
        if row["semester"] < semester:
            if rng.random() >= 0.75:
                continue
            progress, status = 1.0, "completed"
        else:
            if rng.random() >= 0.7:
                continue
            progress, status = round(rng.uniform(0.15, 0.85), 4), "learning"
        episodes = con.execute(
            "SELECT episodes FROM video WHERE video_id=?", (row["top_video"],)
        ).fetchone()
        total = int(episodes["episodes"]) if episodes else 40
        watched = max(1, int(total * progress))
        con.execute(
            """
            INSERT OR REPLACE INTO learning_record
              (student_id, video_id, watched_episodes, progress, status)
            VALUES (?,?,?,?,?)
            """,
            (student_id, row["top_video"], watched, progress, status),
        )
        con.execute(
            "INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at) VALUES (?,?,?,?,?)",
            (student_id, "complete" if status == "completed" else "play",
             row["top_video"], 0,
             (now - timedelta(days=rng.randint(1, 120))).isoformat(timespec="seconds")),
        )
        if status == "completed":
            con.execute(
                "INSERT OR IGNORE INTO achievement (student_id,video_id,completed_at) VALUES (?,?,?)",
                (student_id, row["top_video"], now.isoformat(timespec="seconds")),
            )
        counts[status] += 1
    return counts


def seed_learning_records(con: sqlite3.Connection) -> int:
    records = 0
    accounts = [
        dict(row)
        for row in con.execute(
            "SELECT student_id, major, semester FROM student WHERE student_id LIKE 'YY%'"
        ).fetchall()
    ]
    for account in accounts:
        counts = seed_learning_records_for(
            con, account["student_id"], account["major"], account["semester"]
        )
        records += counts["completed"] + counts["learning"]
    con.commit()
    return records


# ---------------------------------------------------------------- 统计落库

def refresh_resource_stats(con: sqlite3.Connection) -> Dict[str, Any]:
    """统计每专业每门课的课程资源数，写回 major.resource_count。"""
    total = con.execute("SELECT COUNT(*) FROM video WHERE video_id LIKE 'YY%'").fetchone()[0]
    con.execute(
        """
        UPDATE major SET resource_count = (
          SELECT COUNT(*) FROM video v WHERE v.major = major.name
        )
        """
    )
    con.commit()
    return {"total_curriculum_resources": total}


# ---------------------------------------------------------------- 对外入口

def rebuild_all(per_course: int = RESOURCES_PER_COURSE) -> Dict[str, Any]:
    """全量重建：课程体系 -> 资源池 -> 真实视频归位 -> 清理旧数据 -> 学习行为 -> 岗位市场。"""
    con = connect()
    try:
        ensure_curriculum_schema(con)
        curriculum = rebuild_curriculum(con)
        resources = seed_resources(con, per_course=per_course)
        relink = relink_live_videos(con)
        purged = purge_legacy_curriculum(con)
        records = seed_learning_records(con)
        stats = refresh_resource_stats(con)
        con.execute("DELETE FROM recommend_log WHERE is_synthetic=1")
        con.commit()
        # 岗位与技能映射依赖资源池里的 video_id，必须在资源池稳定之后再重建
        from .job_market import build_job_market

        job_market = build_job_market(con)
    finally:
        con.close()
    return {
        "school": SCHOOL_NAME,
        "majors": curriculum["majors"],
        "courses": curriculum["courses"],
        "new_resources": resources,
        "resources_per_course": per_course,
        "live_videos_relinked": relink["linked"],
        "live_matched_by_title": relink["matched_by_title"],
        "live_fallback_course": relink["fallback"],
        "purged": purged,
        "learning_records": records,
        "job_market": job_market,
        **stats,
    }


def main() -> None:
    result = rebuild_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
