from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "processed" / "xuetu_lite.db"

# 5 个目标专业各配一个正式演示账号，账号资料与专业方向一一对应。
DEMO_ACCOUNTS: List[Dict[str, Any]] = [
    {
        "student_id": "20240101",
        "name": "李明",
        "school": "江城大学",
        "major": "计算机科学与技术",
        "grade": "大二",
        "semester": 3,
        "level": 6,
        "xp": 1280,
        "next_xp": 1500,
        "intro": "基础课程已通关，正在补数据结构与算法。",
    },
    {
        "student_id": "20240102",
        "name": "周然",
        "school": "江城大学",
        "major": "软件工程",
        "grade": "大三",
        "semester": 5,
        "level": 7,
        "xp": 1640,
        "next_xp": 2100,
        "intro": "以 Java 后端与软件工程实战为主线。",
    },
    {
        "student_id": "20240103",
        "name": "陈思",
        "school": "江城大学",
        "major": "数据科学与大数据技术",
        "grade": "大二",
        "semester": 3,
        "level": 5,
        "xp": 930,
        "next_xp": 1200,
        "intro": "正在积累 SQL、统计分析与数据科学工具链。",
    },
    {
        "student_id": "20240104",
        "name": "林澈",
        "school": "江城大学",
        "major": "人工智能",
        "grade": "大二",
        "semester": 4,
        "level": 5,
        "xp": 1050,
        "next_xp": 1400,
        "intro": "数学与编程基础已过，开始系统学习机器学习和深度学习。",
    },
    {
        "student_id": "20240105",
        "name": "赵程",
        "school": "江城大学",
        "major": "网络工程",
        "grade": "大二",
        "semester": 4,
        "level": 5,
        "xp": 1010,
        "next_xp": 1350,
        "intro": "专注计算机网络、系统原理与网络工程方向。",
    },
]


def official_student_ids() -> List[str]:
    return [account["student_id"] for account in DEMO_ACCOUNTS]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def rows(con: sqlite3.Connection, sql: str, params=()) -> List[Dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def scalar(con: sqlite3.Connection, sql: str, params=()) -> int:
    return int(con.execute(sql, params).fetchone()[0] or 0)


def _profile_pool(con: sqlite3.Connection, major: str) -> List[Dict[str, Any]]:
    """按专业取真实入库课程，数量不足时再补同专业/通用课程。"""
    seen: set[str] = set()
    result: List[Dict[str, Any]] = []
    exact = rows(
        con,
        """
        SELECT * FROM video
        WHERE major=? AND source_collected_at IS NOT NULL
        ORDER BY popularity DESC, rating DESC
        LIMIT 12
        """,
        (major,),
    )
    for item in exact:
        seen.add(item["video_id"])
        result.append(item)
    if len(result) < 5:
        fallback = rows(
            con,
            """
            SELECT * FROM video
            WHERE source_collected_at IS NOT NULL
              AND (major IS NULL OR major='' OR major=?)
            ORDER BY popularity DESC, rating DESC
            LIMIT 12
            """,
            (major,),
        )
        for item in fallback:
            if item["video_id"] not in seen:
                seen.add(item["video_id"])
                result.append(item)
    return result[:6]


def _write_profile(con: sqlite3.Connection, account: Dict[str, Any], now: datetime) -> None:
    pool = _profile_pool(con, account["major"])
    if not pool:
        return
    statuses = [
        (1.0, "completed"),
        (0.68, "learning"),
        (0.42, "learning"),
        (0.28, "learning"),
        (0.12, "learning"),
    ]
    completed_count = 0
    for index, (ratio, status) in enumerate(statuses):
        video = pool[index % len(pool)]
        episodes = max(1, int(video["episodes"] or 1))
        watched = episodes if status == "completed" else min(episodes, max(1, int(round(episodes * ratio))))
        progress = min(1.0, round(watched / episodes, 4))
        con.execute(
            """
            INSERT INTO learning_record (student_id, video_id, watched_episodes, progress, status)
            VALUES (?,?,?,?,?)
            ON CONFLICT(student_id, video_id) DO UPDATE SET
              watched_episodes=excluded.watched_episodes,
              progress=excluded.progress,
              status=excluded.status
            """,
            (account["student_id"], video["video_id"], watched, progress, status),
        )
        created_at = (now - timedelta(days=24 - index * 3)).isoformat(timespec="seconds")
        if status == "completed":
            completed_count += 1
            con.execute(
                "INSERT OR IGNORE INTO achievement (student_id,video_id,completed_at) VALUES (?,?,?)",
                (account["student_id"], video["video_id"], created_at),
            )
            con.execute(
                """
                INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at)
                VALUES (?,?,?,?,?)
                """,
                (account["student_id"], "complete", video["video_id"], 0, created_at),
            )
        else:
            con.execute(
                """
                INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at)
                VALUES (?,?,?,?,?)
                """,
                (account["student_id"], "play", video["video_id"], 60 * (index + 2), created_at),
            )
    con.execute(
        """
        INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at)
        VALUES (?,?,?,?,?)
        """,
        (account["student_id"], "like", pool[0]["video_id"], 0, (now - timedelta(days=2)).isoformat(timespec="seconds")),
    )
    con.execute(
        """
        INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at)
        VALUES (?,?,?,?,?)
        """,
        (account["student_id"], "favorite", pool[1 % len(pool)]["video_id"], 0, (now - timedelta(days=1)).isoformat(timespec="seconds")),
    )
    xp = int(account["xp"]) + completed_count * 80
    con.execute(
        """
        UPDATE student SET xp=? WHERE student_id=?
        """,
        (xp, account["student_id"]),
    )


def _upsert_student(con: sqlite3.Connection, account: Dict[str, Any]) -> None:
    con.execute(
        """
        INSERT INTO student (student_id,name,school,major,grade,semester,level,xp,next_xp)
        VALUES (:student_id,:name,:school,:major,:grade,:semester,:level,:xp,:next_xp)
        ON CONFLICT(student_id) DO UPDATE SET
          name=excluded.name,
          school=excluded.school,
          major=excluded.major,
          grade=excluded.grade,
          semester=excluded.semester,
          level=excluded.level,
          xp=excluded.xp,
          next_xp=excluded.next_xp
        """,
        account,
    )


def ensure_student_profiles(con: Optional[sqlite3.Connection] = None, sync_recommend: bool = True) -> Dict[str, Any]:
    """幂等补齐 5 个专业账号；不删除已有学习记录。"""
    own_con = con is None
    con = con or connect()
    now = datetime.now()
    for account in DEMO_ACCOUNTS:
        _upsert_student(con, account)
    for account in DEMO_ACCOUNTS:
        has_history = scalar(
            con,
            "SELECT COUNT(*) FROM learning_record WHERE student_id=?",
            (account["student_id"],),
        )
        if has_history == 0:
            _write_profile(con, account, now)
    if sync_recommend:
        from .live_collectors import sync_recommend_log

        sync_recommend_log(con)
    if own_con:
        con.commit()
        con.close()
    return account_overview()


def rebuild_official_profiles(con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """按当前课程库重建 5 个专业画像，用于演示和模型训练前的数据对齐。"""
    own_con = con is None
    con = con or connect()
    now = datetime.now()
    ids = official_student_ids()
    placeholders = ",".join("?" * len(ids))
    con.execute(f"DELETE FROM achievement WHERE student_id IN ({placeholders})", ids)
    con.execute(f"DELETE FROM learning_record WHERE student_id IN ({placeholders})", ids)
    con.execute(f"DELETE FROM behavior_log WHERE student_id IN ({placeholders})", ids)
    con.execute(f"DELETE FROM recommend_log WHERE student_id IN ({placeholders})", ids)
    for account in DEMO_ACCOUNTS:
        _upsert_student(con, account)
        _write_profile(con, account, now)
    from .live_collectors import sync_recommend_log

    sync_recommend_log(con)
    if own_con:
        con.commit()
        con.close()
    return account_overview()


def account_overview(con: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    own_con = con is None
    con = con or connect()
    accounts = []
    for template in DEMO_ACCOUNTS:
        student_rows = rows(
            con,
            "SELECT * FROM student WHERE student_id=?",
            (template["student_id"],),
        )
        if not student_rows:
            continue
        student = student_rows[0]
        learning_count = scalar(
            con,
            "SELECT COUNT(*) FROM learning_record WHERE student_id=?",
            (template["student_id"],),
        )
        positive_count = scalar(
            con,
            """
            SELECT COUNT(*) FROM learning_record
            WHERE student_id=? AND progress>=0.25
            """,
            (template["student_id"],),
        )
        completed_count = scalar(
            con,
            "SELECT COUNT(*) FROM achievement WHERE student_id=?",
            (template["student_id"],),
        )
        major_match_videos = scalar(
            con,
            """
            SELECT COUNT(*) FROM learning_record l
            JOIN video v ON v.video_id=l.video_id
            WHERE l.student_id=? AND v.major=?
            """,
            (template["student_id"], template["major"]),
        )
        accounts.append(
            {
                **student,
                "intro": template["intro"],
                "learning_count": learning_count,
                "positive_count": positive_count,
                "completed_count": completed_count,
                "major_match_videos": major_match_videos,
                "password": "123456",
            }
        )
    if own_con:
        con.close()
    return {
        "password": "123456",
        "student_ids": official_student_ids(),
        "accounts": accounts,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    print(rebuild_official_profiles())


if __name__ == "__main__":
    main()
