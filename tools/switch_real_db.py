# -*- coding: utf-8 -*-
"""采集完成后，把 data/xuetu_real.db 切换为正式库。

用法：
    python tools/switch_real_db.py --check   # 只检查是否具备切换条件
    python tools/switch_real_db.py --force   # 跳过完成度校验强制切换
    python tools/switch_real_db.py           # 校验通过则切换

步骤：
1. 校验采集完成度（进度文件 phase=done 且覆盖率 >= 95%）；
2. 对运行中的服务释放连接（backend.db.close_all 只影响本进程，切换前需先停服务）；
3. 备份旧库为 data/processed/xuetu_lite.backup-<时间>.db；
4. 整库替换；重建 skill_course_map（skill -> 新真实视频）；
5. 提示重启 uvicorn。
"""
import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backend.data_acquisition import DB_PATH  # noqa: E402

REAL_DB = PROJECT / "data" / "xuetu_real.db"
PROGRESS = PROJECT / "data" / "artifacts" / "real_collect_progress.json"


def completion_check(force: bool) -> bool:
    if force:
        print("--force：跳过完成度校验")
        return True
    if not PROGRESS.exists():
        print("没有进度文件，无法确认采集完成")
        return False
    data = json.loads(PROGRESS.read_text(encoding="utf-8"))
    phase = data.get("phase")
    done, total = data.get("courses_done", 0), data.get("courses_total", 0)
    inserted = data.get("videos_inserted_total", 0)
    print(f"进度: phase={phase} 课程 {done}/{total} 已采 {inserted} 条")
    con = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    covered = con.execute(
        """SELECT COUNT(DISTINCT course_id) FROM video"""
    ).fetchone()[0]
    all_courses = con.execute(
        "SELECT COUNT(*) FROM course WHERE major IS NOT NULL AND major != ''"
    ).fetchone()[0]
    con.close()
    ratio = (covered / all_courses) if all_courses else 0
    if phase != "done" or ratio < 0.95:
        print(f"未达到切换条件（需要 phase=done 且课程覆盖>=95%，当前 {ratio:.1%}）")
        return False
    print(f"课程覆盖率 {ratio:.1%}（{covered}/{all_courses}），可以切换")
    return True


def remap_skills(con: sqlite3.Connection, old_db: Path) -> int:
    """把 skill_course_map 按 (skill, prerequisite_skill) 重新映射到新真实视频。"""
    old = sqlite3.connect(f"file:{old_db}?mode=ro", uri=True)
    pairs = old.execute(
        "SELECT DISTINCT skill, prerequisite_skill FROM skill_course_map"
    ).fetchall()
    old.close()
    con.execute("DELETE FROM skill_course_map")
    mapped = 0
    for skill, prereq in pairs:
        like = f"%{skill}%"
        vids = [
            r[0]
            for r in con.execute(
                """SELECT video_id FROM video
                   WHERE title LIKE ? OR tags LIKE ? OR course_name LIKE ?
                   ORDER BY popularity DESC LIMIT 5""",
                (like, like, like),
            )
        ]
        for vid in vids:
            con.execute(
                "INSERT OR IGNORE INTO skill_course_map(skill,video_id,prerequisite_skill) VALUES(?,?,?)",
                (skill, vid, prereq),
            )
            mapped += 1
    return mapped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查不切换")
    parser.add_argument("--force", action="store_true", help="跳过完成度校验")
    args = parser.parse_args()

    if not completion_check(args.force):
        sys.exit(1)

    con_new = sqlite3.connect(REAL_DB)
    stats = {
        "video": con_new.execute("SELECT COUNT(*) FROM video").fetchone()[0],
        "real": con_new.execute("SELECT COUNT(*) FROM video WHERE data_origin='real'").fetchone()[0],
        "episode": con_new.execute("SELECT COUNT(*) FROM video_episode").fetchone()[0],
        "course": con_new.execute("SELECT COUNT(*) FROM course").fetchone()[0],
    }
    print("新库概况:", stats)
    if args.check:
        con_new.close()
        return

    # 提醒：必须先停 uvicorn，否则 Windows 下文件被占用。
    import urllib.request

    try:
        urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=2)
        print("服务仍在 8765 运行，先停止再切换：taskkill /F /PID <pid>")
        con_new.close()
        sys.exit(1)
    except Exception:
        pass

    backup = Path(str(DB_PATH) + f".backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(DB_PATH, backup)
    print("旧库已备份:", backup)

    con_new.commit()
    n = remap_skills(con_new, backup)
    con_new.commit()
    con_new.close()
    print(f"skill_course_map 重建：{n} 条")

    shutil.copy2(REAL_DB, DB_PATH)
    print("已切换:", DB_PATH)
    print("请重启服务: cd 项目目录 && python -m uvicorn backend.app:app --port 8765")


if __name__ == "__main__":
    main()
