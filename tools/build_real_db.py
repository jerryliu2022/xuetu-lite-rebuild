# -*- coding: utf-8 -*-
"""构建真实数据新库 data/xuetu_real.db。

做法：对正式库执行 WAL checkpoint 后整库复制，然后清空所有"生成/演示"
性质的表（video / video_episode / learning_record / achievement /
behavior_log / skill_course_map），保留培养方案（course / major）、
学生账号（student）、岗位（job）、考研目录（exam_subject）等基础数据。
采集完成后由 tools/switch_real_db.py 切换为正式库。
"""
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backend.data_acquisition import DB_PATH  # noqa: E402

REAL_DB = PROJECT / "data" / "xuetu_real.db"

WIPE_TABLES = [
    "video_episode",  # 先删子表
    "video",
    "learning_record",
    "achievement",
    "behavior_log",
    "skill_course_map",  # 全部指向旧生成视频，采集完成后按新视频重建
]


def main() -> None:
    # WAL 库直接拷文件会丢未合并的数据，先 checkpoint 收拢。
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    REAL_DB.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB_PATH) + suffix)
        if src.exists() and suffix:
            Path(str(REAL_DB) + suffix).unlink(missing_ok=True)
    shutil.copy2(DB_PATH, REAL_DB)
    if Path(str(REAL_DB) + "-wal").exists():
        Path(str(REAL_DB) + "-wal").unlink()

    con = sqlite3.connect(REAL_DB)
    con.execute("PRAGMA foreign_keys = ON")
    # 真实封面列：B站 API 返回的 pic。
    cols = {r[1] for r in con.execute("PRAGMA table_info(video)")}
    if "cover_url" not in cols:
        con.execute("ALTER TABLE video ADD COLUMN cover_url TEXT")
    for table in WIPE_TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.execute(f"DELETE FROM {table}")
        print(f"  清空 {table}: {n} 行")
    con.execute("DELETE FROM collection_run")
    con.execute("DELETE FROM collector_state WHERE key LIKE 'real_%'")
    con.commit()
    stats = {
        "course": con.execute("SELECT COUNT(*) FROM course").fetchone()[0],
        "major": con.execute("SELECT COUNT(*) FROM major").fetchone()[0],
        "student": con.execute("SELECT COUNT(*) FROM student").fetchone()[0],
        "video": con.execute("SELECT COUNT(*) FROM video").fetchone()[0],
        "job": con.execute("SELECT COUNT(*) FROM job").fetchone()[0],
        "exam_subject": con.execute("SELECT COUNT(*) FROM exam_subject").fetchone()[0],
    }
    con.close()
    print("新库就绪:", REAL_DB)
    print("保留:", stats)


if __name__ == "__main__":
    main()
