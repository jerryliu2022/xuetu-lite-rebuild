# -*- coding: utf-8 -*-
"""真实采集器：按培养方案逐门课采集 B站真实视频，写入新库 data/xuetu_real.db。

分摊比例调整说明（依据 2026-09-21 实测可行性探测）：
- B站公开搜索 API 可行，返回真实 bvid / 封面图 / 播放量 / 发布时间 → 每门课 50 条全部走 B站；
- 中国大学MOOC：搜索页是 SPA 空壳、web RPC 需登录 cookie，不可行 → 0；
- 学堂在线：搜索页仅 7.8KB JS 空壳、旧 API 已 404，不可行 → 0；
- 网易云课堂：study.163.com 已重定向到 systemError.htm（个人版下线），不可行 → 0；
- 极客时间：SPA + 风控，无公开接口，不可行 → 0。
B站上实际存在 MOOC 官方号与大量高校课程搬运源，课程覆盖不受影响，
但平台归属如实标 B站，不冒充其他平台。

运行：python -m backend.real_collectors          # 前台全量
     python -m backend.real_collectors --limit 5 # 只采 5 门（试跑）
断点续采：collector_state 表 real_last_course_id；进度实时写
data/artifacts/real_collect_progress.json（原子写）。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .data_acquisition import DB_PATH
from .live_collectors import build_bilibili_session

REAL_DB = Path(DB_PATH).parent.parent / "xuetu_real.db"  # data/processed/xuetu_lite.db -> data/xuetu_real.db
PROGRESS_PATH = Path(DB_PATH).parent.parent / "artifacts" / "real_collect_progress.json"
if not REAL_DB.exists():
    raise SystemExit(f"新库不存在，先运行 tools/build_real_db.py: {REAL_DB}")
PER_COURSE_TARGET = 50
REQUEST_INTERVAL = (0.6, 1.2)          # 相邻两次 API 的随机间隔（放慢避风控）
RISK_CONTROL_SLEEP = 30                 # 连续风控时的退避基数（30*attempt 递增）
MAX_RISK_RETRIES = 5
STALE_TAGS = re.compile(r"<[^>]+>|&#39;|&quot;|&amp;|&lt;|&gt;|\s+")

KEYWORD_SUFFIXES = ["", " 教程", " 课程", " 公开课", " 讲解"]
SEARCH_ORDERS = ["totalrank", "click", "pubdate"]
COVER_PALETTE = ["#5B8DEF", "#7C5CFF", "#00B8A9", "#F6A623", "#43C6AC", "#2EC4B6", "#118AB2"]


def _clean(text: str) -> str:
    return STALE_TAGS.sub(" ", text or "").strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_progress(data: Dict[str, Any]) -> None:
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


def _api_search(session: requests.Session, keyword: str, order: str, page: int) -> List[Dict[str, Any]]:
    url = "https://api.bilibili.com/x/web-interface/search/type"
    params = {"search_type": "video", "keyword": keyword, "order": order, "page": page}
    headers = {
        "Referer": "https://search.bilibili.com/video?keyword=" + urllib.parse.quote(keyword),
        "Origin": "https://search.bilibili.com",
        "Accept": "application/json, text/plain, */*",
    }
    for attempt in range(MAX_RISK_RETRIES):
        try:
            r = session.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 412:
                time.sleep(RISK_CONTROL_SLEEP * (attempt + 1) * 0.5)
                continue
            data = r.json()
            if data.get("code") == 412 or data.get("code") == -412:
                time.sleep(RISK_CONTROL_SLEEP * (attempt + 1) * 0.5)
                continue
            if data.get("code") != 0:
                return []
            return (data.get("data") or {}).get("result") or []
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
    return []


def _parse_duration(raw: Any) -> int:
    """B站搜索接口时长可能是秒数或 '分:秒' / '时:分:秒' 字符串。"""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    parts = str(raw).strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    seconds = 0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def _map_video(item: Dict[str, Any], course: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把一条 B站搜索结果映射为 video / video_episode 行。"""
    bvid = item.get("bvid") or ""
    view = int(item.get("play") or 0)
    like = int(item.get("like") or 0)
    duration = _parse_duration(item.get("duration"))  # 兼容秒数与 "分:秒"
    pubdate = int(item.get("pubdate") or 0)
    upload = datetime.fromtimestamp(pubdate).date().isoformat() if pubdate else "2026-09-21"
    pic = item.get("pic") or ""
    cover_url = ("https:" + pic) if pic.startswith("//") else pic
    rating = round(min(5.0, 3.6 + 1.4 * (like / view) * 12), 2) if view else 3.6
    video_id = bvid
    video_row = {
        "video_id": video_id,
        "course_id": course["course_id"],
        "title": _clean(item.get("title") or "")[:160] or "B站课程视频",
        "platform": "B站",
        "is_paid": 0,
        "teacher": _clean((item.get("author") or "")[:60]),
        "org": _clean((item.get("author") or "")[:60]),
        "popularity": view,
        "rating": rating,
        "episodes": 1,
        "duration": duration,
        "tags": _clean(item.get("tag") or "")[:200],
        "summary": _clean(item.get("description") or "")[:400],
        "source_url": f"https://www.bilibili.com/video/{bvid}",
        "cover_color": COVER_PALETTE[hash(bvid) % len(COVER_PALETTE)],
        "upload_date": upload,
        "like_count": like,
        "favorite_count": int(item.get("favorite") or 0),
        "major": course["major"],
        "source_raw_id": f"bilibili:{bvid}",
        "source_collected_at": _now(),
        "data_origin": "real",
        "course_name": course["name"],
        "cover_url": cover_url,
    }
    episode_row = {
        "episode_id": f"{bvid}-P1",
        "video_id": video_id,
        "episode_no": 1,
        "title": "正片",
        "duration": str(duration),
        # 官方嵌入播放器，iframe 可直接播，点卡片即看
        "play_url": f"https://player.bilibili.com/player.html?bvid={bvid}&page=1&autoplay=0",
    }
    return video_row, episode_row


def _course_keywords(course: Dict[str, Any]) -> List[str]:
    name = course["name"]
    major = course.get("major") or ""
    plans = [name + s for s in KEYWORD_SUFFIXES]
    if major and major != name:
        plans.append(f"{major} {name}")
    return plans


def collect_course(session: requests.Session, course: Dict[str, Any],
                   existing: set) -> Tuple[int, int, int]:
    """采一门课，返回 (新增, 跳过已有, 请求数)。"""
    target = PER_COURSE_TARGET
    got: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    seen_here = set()
    requests_made = 0
    for order in SEARCH_ORDERS:
        if len(got) >= target:
            break
        for keyword in _course_keywords(course):
            if len(got) >= target:
                break
            for page in (1, 2):
                if len(got) >= target:
                    break
                requests_made += 1
                time.sleep(random.uniform(*REQUEST_INTERVAL))
                items = _api_search(session, keyword, order, page)
                for item in items:
                    bvid = item.get("bvid")
                    if not bvid or bvid in existing or bvid in seen_here:
                        continue
                    # 只收可播放、时长 >= 60s 的正片类结果
                    if (item.get("play") or 0) <= 0 or _parse_duration(item.get("duration")) < 60:
                        continue
                    seen_here.add(bvid)
                    got.append(_map_video(item, course))
                    if len(got) >= target:
                        break
    return got, requests_made


def run(limit: Optional[int] = None) -> None:
    import sqlite3

    con = sqlite3.connect(REAL_DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    cols = {r[1] for r in con.execute("PRAGMA table_info(video)")}
    if "cover_url" not in cols:
        con.execute("ALTER TABLE video ADD COLUMN cover_url TEXT")
    courses = [
        dict(r)
        for r in con.execute(
            """SELECT course_id, name, major FROM course
               WHERE major IS NOT NULL AND major != ''
               ORDER BY course_id"""
        )
    ]
    if limit:
        courses = courses[:limit]
    total_courses = len(courses)
    done_id = con.execute(
        "SELECT value FROM collector_state WHERE key='real_last_course_id'"
    ).fetchone()
    start_index = 0
    if done_id:
        for i, c in enumerate(courses):
            if c["course_id"] == done_id["value"]:
                start_index = i + 1
                break
    existing = {r[0] for r in con.execute("SELECT video_id FROM video")}

    print(f"[{_now()}] 待采课程 {total_courses} 门，从第 {start_index + 1} 门续起", flush=True)
    session = build_bilibili_session()
    inserted_total = 0
    errors: List[str] = []
    for i in range(start_index, total_courses):
        course = courses[i]
        try:
            rows, reqs = collect_course(session, course, existing)
            video_rows = [r[0] for r in rows]
            episode_rows = [r[1] for r in rows]
            if video_rows:
                con.executemany(
                    """INSERT OR IGNORE INTO video(
                       video_id,course_id,title,platform,is_paid,teacher,org,popularity,rating,
                       episodes,duration,tags,summary,source_url,cover_color,upload_date,
                       like_count,favorite_count,major,source_raw_id,source_collected_at,
                       data_origin,course_name,cover_url)
                       VALUES (:video_id,:course_id,:title,:platform,:is_paid,:teacher,:org,
                       :popularity,:rating,:episodes,:duration,:tags,:summary,:source_url,
                       :cover_color,:upload_date,:like_count,:favorite_count,:major,
                       :source_raw_id,:source_collected_at,:data_origin,:course_name,:cover_url)""",
                    video_rows,
                )
                con.executemany(
                    """INSERT OR IGNORE INTO video_episode(
                       episode_id,video_id,episode_no,title,duration,play_url)
                       VALUES (:episode_id,:video_id,:episode_no,:title,:duration,:play_url)""",
                    episode_rows,
                )
                con.execute(
                    """INSERT INTO collector_state(key,value,updated_at) VALUES('real_last_course_id',?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (course["course_id"], _now()),
                )
                con.commit()
            for r in video_rows:
                existing.add(r["video_id"])
            inserted_total += len(video_rows)
            progress = {
                "updated_at": _now(),
                "phase": "collecting",
                "courses_done": i + 1,
                "courses_total": total_courses,
                "current_course": course["name"],
                "videos_inserted_total": inserted_total,
                "requests_made_total": None,
                "recent_errors": errors[-5:],
            }
            _write_progress(progress)
            print(
                f"[{_now()}] ({i + 1}/{total_courses}) {course['name']}: "
                f"+{len(video_rows)} 条 (请求{reqs}次, 累计{inserted_total})",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{course['name']}: {type(exc).__name__}: {str(exc)[:120]}")
            print(f"[{_now()}] ERROR {course['name']}: {exc}", flush=True)
            time.sleep(5)

    summary = {
        "updated_at": _now(),
        "phase": "done",
        "courses_done": total_courses,
        "courses_total": total_courses,
        "videos_inserted_total": inserted_total,
        "recent_errors": errors[-10:],
    }
    _write_progress(summary)
    con.execute(
        """INSERT INTO collection_run(domain,source,target_count,inserted_count,skipped_count,
           ok,message,started_at,finished_at)
           VALUES('video','B站-培养方案全量真实采集',?,?,?,?,?,?,?)""",
        (total_courses * PER_COURSE_TARGET, inserted_total, 0,
         1 if not errors else 0,
         f"真实采集 {inserted_total} 条 / 目标 {total_courses * PER_COURSE_TARGET} 条，错误 {len(errors)} 个",
         summary.get("started_at") or _now(), _now()),
    )
    con.commit()
    con.close()
    print(f"[{_now()}] 采集完成：{inserted_total} 条，错误 {len(errors)} 个", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="只采前 N 门课（试跑）")
    args = parser.parse_args()
    run(limit=args.limit)
