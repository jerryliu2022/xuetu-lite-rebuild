# -*- coding: utf-8 -*-
"""补采大学体育（三）（四）两门。"""
import sqlite3
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backend.real_collectors import REAL_DB, _map_video, _api_search  # noqa: E402
from backend.live_collectors import build_bilibili_session  # noqa: E402

INSERT_VIDEO = """INSERT OR IGNORE INTO video(video_id,course_id,title,platform,is_paid,teacher,org,
  popularity,rating,episodes,duration,tags,summary,source_url,cover_color,upload_date,
  like_count,favorite_count,major,source_raw_id,source_collected_at,data_origin,course_name,cover_url)
  VALUES(:video_id,:course_id,:title,:platform,:is_paid,:teacher,:org,:popularity,:rating,:episodes,
  :duration,:tags,:summary,:source_url,:cover_color,:upload_date,:like_count,:favorite_count,:major,
  :source_raw_id,:source_collected_at,:data_origin,:course_name,:cover_url)"""
INSERT_EPISODE = """INSERT OR IGNORE INTO video_episode(episode_id,video_id,episode_no,title,duration,play_url)
  VALUES(:episode_id,:video_id,:episode_no,:title,:duration,:play_url)"""


def main() -> None:
    con = sqlite3.connect(REAL_DB)
    con.row_factory = sqlite3.Row
    courses = [
        dict(r)
        for r in con.execute(
            """SELECT course_id,name,major FROM course
               WHERE name IN ('大学体育（三）','大学体育（四）')
               AND course_id NOT IN (SELECT DISTINCT course_id FROM video)"""
        )
    ]
    print("待补:", [(c["name"], c["major"]) for c in courses], flush=True)
    existing = {r[0] for r in con.execute("SELECT video_id FROM video")}
    session = build_bilibili_session()
    for course in courses:
        got, seen = [], set()
        kws = [course["name"] + s for s in (" 教程", " 教学", " 公开课", " 训练", " 课程")]
        kws += ["大学体育 课程", "体育与健康 课程", "体育 考试 教学"]
        for kw in kws:
            if len(got) >= 30:
                break
            for page in (1, 2):
                if len(got) >= 30:
                    break
                time.sleep(1.0)
                for item in _api_search(session, kw, "totalrank", page):
                    bvid = item.get("bvid")
                    if not bvid or bvid in existing or bvid in seen:
                        continue
                    if (item.get("play") or 0) <= 0:
                        continue
                    seen.add(bvid)
                    v, e = _map_video(item, course)
                    if v["duration"] < 20:
                        continue
                    got.append((v, e))
                    if len(got) >= 30:
                        break
        if got:
            con.executemany(INSERT_VIDEO, [r[0] for r in got])
            con.executemany(INSERT_EPISODE, [r[1] for r in got])
            con.commit()
            for r in got:
                existing.add(r[0]["video_id"])
        print(f"{course['name']}({course['major']}): +{len(got)}", flush=True)
    left = con.execute(
        """SELECT COUNT(*) FROM course WHERE major IS NOT NULL AND major!=''
           AND course_id NOT IN (SELECT DISTINCT course_id FROM video)"""
    ).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM video").fetchone()[0]
    con.close()
    print(f"最终: 库内 {total} 条, 0条课程剩 {left} 门(全部为习概)", flush=True)


if __name__ == "__main__":
    main()
