"""学生账号与画像。

岳阳学院 2026 年招生的 25 个专业各配一个演示账号（学号 YY01 ~ YY25），
账号的专业、院系、培养方案直接来自 `yueyang_curriculum.MAJORS`，
所以账号资料和专业数据永远一致，不会出现"账号是计算机专业、课表是别的专业"这种错位。

学习行为的生成规则见 `data_expansion.seed_learning_records_for`：
按培养方案把往期学期课程标记为已修完、本学期课程标记为正在学，
推荐引擎的"本学期 / 下学期"召回因此有真实依据。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import db as _db
from .yueyang_curriculum import MAJORS, SCHOOL_NAME

# 库路径统一由 backend/db.py 提供，避免每个模块各写一遍、改一处漏一处
DB_PATH = _db.DB_PATH

ACCOUNT_NAMES = [
    "李明", "周然", "陈思", "林澈", "赵程", "孙悦", "吴桐", "郑好", "王泽",
    "冯琪", "蒋一帆", "沈嘉言", "韩雨桐", "杨帆", "许安然", "何嘉树", "邓雅",
    "曹亦辰", "彭书瑶", "苏子墨", "蒋知远", "谢清和", "乔若谷", "樊星辰", "秦望舒",
]

# 25 个专业账号：学号 YY01~YY25，与 major 表的主键顺序一一对应
DEMO_ACCOUNTS: List[Dict[str, Any]] = [
    {
        "student_id": f"YY{index:02d}",
        "name": ACCOUNT_NAMES[(index - 1) % len(ACCOUNT_NAMES)],
        "school": SCHOOL_NAME,
        "major": major["name"],
        "grade": "大二",
        "semester": 3,
        "level": 5,
        "xp": 980,
        "next_xp": 1400,
        "intro": f"{major['college']} ・ {major['category']}",
    }
    for index, major in enumerate(MAJORS, start=1)
]

DEFAULT_STUDENT_ID = "YY08"  # 计算机科学与技术
DEFAULT_PASSWORD = "123456"


def official_student_ids() -> List[str]:
    return [account["student_id"] for account in DEMO_ACCOUNTS]


def connect() -> sqlite3.Connection:
    # 建画像时要靠外键约束挡住脏引用，所以单独持一条开了 foreign_keys 的长连接
    return _db.connection(foreign_keys=True)


def rows(con: sqlite3.Connection, sql: str, params=()) -> List[Dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def scalar(con: sqlite3.Connection, sql: str, params=()) -> int:
    return int(con.execute(sql, params).fetchone()[0] or 0)


def _upsert_student(con: sqlite3.Connection, account: Dict[str, Any]) -> None:
    con.execute(
        """
        INSERT INTO student (student_id,name,school,major,grade,semester,level,xp,next_xp)
        VALUES (:student_id,:name,:school,:major,:grade,:semester,:level,:xp,:next_xp)
        ON CONFLICT(student_id) DO UPDATE SET
          name=excluded.name, school=excluded.school, major=excluded.major,
          grade=excluded.grade, semester=excluded.semester,
          level=excluded.level, xp=excluded.xp, next_xp=excluded.next_xp
        """,
        account,
    )


def ensure_student_profiles(con: Optional[sqlite3.Connection] = None, sync_recommend: bool = True) -> Dict[str, Any]:
    """幂等补齐 25 个专业账号；已有学习记录的账号不会被覆盖。"""
    own_con = con is None
    con = con or connect()
    try:
        for account in DEMO_ACCOUNTS:
            _upsert_student(con, account)
        from .data_expansion import seed_learning_records_for

        for account in DEMO_ACCOUNTS:
            has_history = scalar(
                con,
                "SELECT COUNT(*) FROM learning_record WHERE student_id=?",
                (account["student_id"],),
            )
            if has_history == 0:
                seed_learning_records_for(
                    con, account["student_id"], account["major"], account["semester"]
                )
        if sync_recommend:
            from .live_collectors import sync_recommend_log

            sync_recommend_log(con)
        con.commit()
    finally:
        if own_con:
            con.close()
    return account_overview(con if not own_con else None)


def rebuild_official_profiles(con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """按当前课程库重建全部专业画像，用于演示前对齐数据。"""
    own_con = con is None
    con = con or connect()
    try:
        ids = official_student_ids()
        marks = ",".join("?" * len(ids))
        for table in ("achievement", "learning_record", "behavior_log", "recommend_log"):
            con.execute(f"DELETE FROM {table} WHERE student_id IN ({marks})", ids)
        from .data_expansion import seed_learning_records_for

        for account in DEMO_ACCOUNTS:
            _upsert_student(con, account)
            seed_learning_records_for(
                con, account["student_id"], account["major"], account["semester"]
            )
        from .live_collectors import sync_recommend_log

        sync_recommend_log(con)
        con.commit()
    finally:
        if own_con:
            con.close()
    return account_overview()


def account_overview(con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    own_con = con is None
    con = con or connect()
    try:
        # 整个总览一次批量取齐：原实现对每个账号跑 6 条独立查询
        # （25 个账号 = 150 次往返），这里压成 5 条聚合查询 + 内存拼装。
        ids = official_student_ids()
        marks = ",".join("?" * len(ids))
        students = {
            row["student_id"]: row
            for row in rows(con, f"SELECT * FROM student WHERE student_id IN ({marks})", ids)
        }
        learning_stats = {
            row["student_id"]: row
            for row in rows(
                con,
                f"""SELECT student_id, COUNT(*) AS total_count,
                           SUM(CASE WHEN progress>=0.25 THEN 1 ELSE 0 END) AS positive_count
                    FROM learning_record WHERE student_id IN ({marks}) GROUP BY student_id""",
                ids,
            )
        }
        achievement_stats = {
            row["student_id"]: row["count"]
            for row in rows(
                con,
                f"""SELECT student_id, COUNT(*) AS count FROM achievement
                    WHERE student_id IN ({marks}) GROUP BY student_id""",
                ids,
            )
        }
        # 同一门专业的视频数：按 (学生, 视频专业) 分组，等效于原来的逐账号 JOIN 过滤
        major_match_stats = {
            (row["student_id"], row["major"]): row["count"]
            for row in rows(
                con,
                """SELECT l.student_id AS student_id, v.major AS major, COUNT(*) AS count
                   FROM learning_record l JOIN video v ON v.video_id=l.video_id
                   WHERE v.major IS NOT NULL
                   GROUP BY l.student_id, v.major""",
            )
        }
        major_lookup = {
            row["name"]: row
            for row in rows(
                con,
                """SELECT name, college, course_count, resource_count, tuition, total_credits
                   FROM major""",
            )
        }

        accounts = []
        for template in DEMO_ACCOUNTS:
            student = students.get(template["student_id"])
            if not student:
                continue
            stats = learning_stats.get(template["student_id"]) or {}
            learning_count = int(stats.get("total_count") or 0)
            positive_count = int(stats.get("positive_count") or 0)
            completed_count = int(achievement_stats.get(template["student_id"]) or 0)
            major_match_videos = int(
                major_match_stats.get((template["student_id"], template["major"])) or 0
            )
            major_info = major_lookup.get(template["major"]) or {}
            accounts.append(
                {
                    **student,
                    "intro": template["intro"],
                    "learning_count": learning_count,
                    "positive_count": positive_count,
                    "completed_count": completed_count,
                    "major_match_videos": major_match_videos,
                    "college": major_info.get("college", ""),
                    "course_count": major_info.get("course_count", 0),
                    "resource_count": major_info.get("resource_count", 0),
                    "tuition": major_info.get("tuition"),
                    "total_credits": major_info.get("total_credits"),
                    "password": DEFAULT_PASSWORD,
                }
            )
        return {
            "password": DEFAULT_PASSWORD,
            "default_student_id": DEFAULT_STUDENT_ID,
            "student_ids": official_student_ids(),
            "accounts": accounts,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    finally:
        if own_con:
            con.close()


def main() -> None:
    print(rebuild_official_profiles())


if __name__ == "__main__":
    main()
