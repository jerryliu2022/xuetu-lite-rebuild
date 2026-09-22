# -*- coding: utf-8 -*-
"""对 0 条课程做一轮补采：放宽过滤（时长>=20s），换泛化关键词。"""
import sqlite3
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backend.real_collectors import (  # noqa: E402
    REAL_DB, _map_video, _api_search, _now,
)
from backend.live_collectors import build_bilibili_session  # noqa: E402

TARGET = 30
EXTRA_KEYWORDS = [
    "{name} 网课", "{name} 精讲", "{name} 全套", "{name} 复习",
    "习近平新时代 思政", "习思想 概论 课", "毛概 精讲", "体育课 教学视频",
]


def main() -> None:
    con = sqlite3.connect(REAL_DB)
    con.row_factory = sqlite3.Row
    courses = [
        dict(r)
        for r in con.execute(
            """SELECT course_id, name, major FROM course
               WHERE major IS NOT NULL AND major != ''
               AND course_id NOT IN (SELECT DISTINCT course_id FROM video)"""
        )
    ]
    print(f"待补采: {len(courses)} 门", flush=True)
    existing = {r[0] for r in con.execute("SELECT video_id FROM video")}
    session = build_bilibili_session()

    # 按课名去重：同名的 25 个专业共享同一批视频，采一次后其余专业复制映射即可
    done_names = {}
    for course in courses:
        name = course["name"]
        if name in done_names:
            src = done_names[name]
            rows = con.execute(
                "SELECT * FROM video WHERE course_id=? LIMIT ?", (src["course_id"], TARGET)
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["course_id"] = course["course_id"]
                d["major"] = course["major"]
                con.execute(
                    """INSERT OR IGNORE INTO video(video_id,course_id,title,platform,is_paid,teacher,
                       org,popularity,rating,episodes,duration,tags,summary,source_url,cover_color,
                       upload_date,like_count,favorite_count,major,source_raw_id,source_collected_at,
                       data_origin,course_name,cover_url)
                       VALUES(:video_id,:course_id,:title,:platform,:is_paid,:teacher,:org,:popularity,
                       :rating,:episodes,:duration,:tags,:summary,:source_url,:cover_color,:upload_date,
                       :like_count,:favorite_count,:major,:source_raw_id,:source_collected_at,
                       :data_origin,:course_name,:cover_url)""",
                    d,
                )
                con.execute(
                    "INSERT OR IGNORE INTO video_episode(episode_id,video_id,episode_no,title,duration,play_url) "
                    "VALUES('BF-'||video_id, video_id, 1, '正片', duration, "
                    "'https://player.bilibili.com/player.html?bvid='||video_id||'&page=1&autoplay=0')"
                )
            con.commit()
            print(f"  [复制映射] {name} <- {src['name']}: {len(rows)} 条", flush=True)
            continue

        got = []
        seen = set()
        kws = [name + s for s in ("", " 教程", " 网课", " 精讲", " 全套")]
        kws += [k.format(name=name[:6]) for k in EXTRA_KEYWORDS if "{name}" in k]
        for kw in kws:
            if len(got) >= TARGET:
                break
            for page in (1, 2):
                if len(got) >= TARGET:
                    break
                time.sleep(1.0)
                items = _api_search(session, kw, "totalrank", page)
                for item in items:
                    bvid = item.get("bvid")
                    if not bvid or bvid in existing or bvid in seen:
                        continue
                    if (item.get("play") or 0) <= 0:
                        continue
                    seen.add(bvid)
                    video_row, episode_row = _map_video(item, course)
                    if video_row["duration"] < 20:
                        continue
                    got.append((video_row, episode_row))
                    if len(got) >= TARGET:
                        break
        if got:
            con.executemany(
                """INSERT OR IGNORE INTO video(video_id,course_id,title,platform,is_paid,teacher,org,
                   popularity,rating,episodes,duration,tags,summary,source_url,cover_color,upload_date,
                   like_count,favorite_count,major,source_raw_id,source_collected_at,data_origin,
                   course_name,cover_url)
                   VALUES(:video_id,:course_id,:title,:platform,:is_paid,:teacher,:org,:popularity,
                   :rating,:episodes,:duration,:tags,:summary,:source_url,:cover_color,:upload_date,
                   :like_count,:favorite_count,:major,:source_raw_id,:source_collected_at,:data_origin,
                   :course_name,:cover_url)""",
                [r[0] for r in got],
            )
            con.executemany(
                """INSERT OR IGNORE INTO video_episode(episode_id,video_id,episode_no,title,duration,play_url)
                   VALUES(:episode_id,:video_id,:episode_no,:title,:duration,:play_url)""",
                [r[1] for r in got],
            )
            con.commit()
            for r in got:
                existing.add(r[0]["video_id"])
        done_names[name] = course
        print(f"  {name}: +{len(got)} 条", flush=True)

    left = con.execute(
        """SELECT COUNT(*) FROM course WHERE major IS NOT NULL AND major != ''
           AND course_id NOT IN (SELECT DISTINCT course_id FROM video)"""
    ).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM video").fetchone()[0]
    con.close()
    print(f"补采完成: 库内 {total} 条，仍为 0 条的课程 {left} 门", flush=True)


if __name__ == "__main__":
    main()
