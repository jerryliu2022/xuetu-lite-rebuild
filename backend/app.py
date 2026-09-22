from __future__ import annotations

import hmac
import json
import sqlite3
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db as _db
from . import recommender
from .data_quality import quality_report
from .data_acquisition import (
    DB_PATH,
    reset_database,
    write_seed_files,
)
from .evaluate_model import EVAL_PATH, evaluate, evaluate_async, evaluate_status
from .live_collectors import collect_async, collection_status, ensure_schema as ensure_live_schema
from .student_profiles import (
    DEFAULT_STUDENT_ID,
    account_overview,
    ensure_student_profiles,
    rebuild_official_profiles,
)
from .train_model import MODEL_PATH, main as train_main

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SSO_SECRET = b"xuetu-lite-demo-secret"


def _curriculum_ready() -> bool:
    """数据库里是否已经有岳阳学院的培养方案课程体系。"""
    try:
        con = _db.open_connection()
        try:
            count = con.execute("SELECT COUNT(*) FROM course WHERE course_id LIKE 'YY%'").fetchone()[0]
        finally:
            con.close()
        return count > 100
    except Exception:
        return False


def bootstrap() -> None:
    if not DB_PATH.exists():
        write_seed_files()
        reset_database()
    ensure_live_schema()
    # 索引务必在补数据之前建好：库已经存在时 rebuild_all 不会跑，
    # 这里单独兜一次，保证老库也能拿到最新的覆盖索引。
    from .data_expansion import ensure_indexes

    _index_con = _db.open_connection()
    try:
        created = ensure_indexes(_index_con)
        if created:
            print(f"[bootstrap] 新建索引 {len(created)} 个: {', '.join(created)}")
    finally:
        _index_con.close()
    if not _curriculum_ready():
        # 首次启动：把岳阳学院 25 个专业的培养方案与课程资源池建起来
        from .data_expansion import rebuild_all

        rebuild_all()
    ensure_student_profiles()
    model_outdated = False
    if MODEL_PATH.exists():
        try:
            report = json.loads(MODEL_PATH.read_text(encoding="utf-8")).get("report", {})
            model_outdated = len(report.get("feature_names", [])) != 8
        except Exception:
            model_outdated = True
    if not MODEL_PATH.exists() or model_outdated:
        train_main()
    # 放在最后：全新库走完 rebuild / train 之后也要切成 WAL。
    # 切之前先把 bootstrap 期间建的长连接放掉，否则 PRAGMA 可能撞上未结束的事务。
    _db.close_all()
    journal = _db.enable_wal()
    if journal != "wal":
        print(f"[bootstrap] 日志模式 = {journal}（未能启用 WAL，写延迟会偏高，不影响功能）")


bootstrap()
app = FastAPI(title="学途 Lite 推荐服务", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_store_static_assets(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


class LoginPayload(BaseModel):
    student_id: str
    password: str


class TrackPayload(BaseModel):
    student_id: str
    video_id: Optional[str] = None
    event_type: str
    duration: int = 0


class ProgressPayload(BaseModel):
    student_id: str
    episode_no: int


class CollectPayload(BaseModel):
    video_per_platform: int = 50
    job_total: int = 200
    collect_exam: bool = True
    retrain: bool = True


def con() -> sqlite3.Connection:
    """请求级连接：本线程的长连接（WAL + 复用，见 backend/db.py）。"""
    return _db.connection()


def row_or_404(db: sqlite3.Connection, sql: str, params=()):
    row = db.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="未找到数据")
    return dict(row)


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/play/{video_id}")
def play(video_id: str):
    return FileResponse(FRONTEND / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "db": DB_PATH.exists(), "model": MODEL_PATH.exists()}


# 与 frontend/index.html 里 <link rel="icon"> 用的是同一张图。
# index.html 走的是 data-URI，浏览器不会来请求 /favicon.ico；
# 这里单独暴露一份，是为了书签、爬虫以及直接访问 /favicon.ico 的场景不出现 404。
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#6c5ce7"/><stop offset="100%" stop-color="#00c2a8"/>'
    "</linearGradient></defs>"
    '<rect width="64" height="64" rx="16" fill="url(#g)"/>'
    '<text x="32" y="45" font-size="34" text-anchor="middle" fill="#ffffff">\u25c6</text>'
    "</svg>"
)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.post("/api/login")
def login(payload: LoginPayload):
    db = con()
    student = db.execute("SELECT * FROM student WHERE student_id=?", (payload.student_id,)).fetchone()
    if not student or payload.password not in {"123456", "xuetu"}:
        raise HTTPException(status_code=401, detail="学号或密码错误。演示账号为 YY01 ~ YY25（对应 25 个招生专业），密码统一 123456")
    return {"token": f"demo-{payload.student_id}", "student": dict(student)}


@app.get("/api/sso")
def sso(student_id: str, ts: str, sig: str):
    expected = hmac.new(SSO_SECRET, f"{student_id}:{ts}".encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="SSO 签名校验失败")
    db = con()
    return {"token": f"sso-{student_id}", "student": row_or_404(db, "SELECT * FROM student WHERE student_id=?", (student_id,))}


@app.get("/api/profile/{student_id}")
def profile(student_id: str):
    return recommender.user_profile(student_id)


@app.get("/api/demo-accounts")
def demo_accounts():
    return account_overview()


@app.get("/api/recommendations/professional")
def professional(
    student_id: str = DEFAULT_STUDENT_ID,
    filter: str = "all",
    page_size: int = recommender.PAGE_SIZE,
):
    page_size = max(8, min(int(page_size), 200))
    return recommender.recommend_professional(student_id, filter, page_size=page_size)


@app.get("/api/recommendations/jobs")
def jobs(student_id: str = DEFAULT_STUDENT_ID, job_id: Optional[str] = None):
    return recommender.jobs_for_student(student_id, job_id)


@app.get("/api/recommendations/exam")
def exam(student_id: str = DEFAULT_STUDENT_ID, school: Optional[str] = None, major: Optional[str] = None):
    return recommender.exam_recommendations(student_id, school, major)


@app.get("/api/videos/{video_id}")
def video(video_id: str, student_id: str = DEFAULT_STUDENT_ID):
    return recommender.video_detail(video_id, student_id)


@app.post("/api/track")
def track(payload: TrackPayload):
    db = con()
    db.execute(
        "INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at) VALUES (?,?,?,?,?)",
        (payload.student_id, payload.event_type, payload.video_id, payload.duration, datetime.now().isoformat()),
    )
    db.commit()
    return {"ok": True}


@app.post("/api/videos/{video_id}/progress")
def progress(video_id: str, payload: ProgressPayload):
    db = con()
    video = row_or_404(db, "SELECT * FROM video WHERE video_id=?", (video_id,))
    watched = max(0, min(payload.episode_no, int(video["episodes"])))
    ratio = watched / int(video["episodes"])
    status = "completed" if watched >= int(video["episodes"]) else "learning"
    db.execute(
        """
        INSERT INTO learning_record (student_id,video_id,watched_episodes,progress,status)
        VALUES (?,?,?,?,?)
        ON CONFLICT(student_id, video_id) DO UPDATE SET
          watched_episodes=excluded.watched_episodes,
          progress=excluded.progress,
          status=excluded.status
        """,
        (payload.student_id, video_id, watched, ratio, status),
    )
    db.execute(
        "INSERT INTO behavior_log (student_id,event_type,video_id,duration,created_at) VALUES (?,?,?,?,?)",
        (payload.student_id, "complete" if status == "completed" else "play", video_id, 0, datetime.now().isoformat()),
    )
    if status == "completed":
        db.execute(
            "INSERT OR IGNORE INTO achievement (student_id,video_id,completed_at) VALUES (?,?,?)",
            (payload.student_id, video_id, datetime.now().isoformat()),
        )
        db.execute("UPDATE student SET xp=xp+80 WHERE student_id=?", (payload.student_id,))
    db.commit()
    return {"ok": True, "watched_episodes": watched, "progress": round(ratio, 4), "status": status}


@app.post("/api/videos/{video_id}/like")
def like(video_id: str, payload: TrackPayload):
    payload.video_id = video_id
    payload.event_type = "like"
    return track(payload)


@app.post("/api/videos/{video_id}/favorite")
def favorite(video_id: str, payload: TrackPayload):
    payload.video_id = video_id
    payload.event_type = "favorite"
    return track(payload)


@app.get("/api/admin/model-report")
def model_report():
    # 走推荐器里那份带指纹缓存的模型，别为了取一个小字段去解析 2 MB 的 JSON
    return recommender.load_model()["report"]


@app.get("/api/admin/data-quality")
def data_quality():
    return quality_report()


@app.get("/api/admin/model-evaluation")
def model_evaluation():
    """评估报告只在文件里读，绝不在这里同步跑 evaluate()。

    evaluate() 实测 2 分钟以上且单核跑满；若在请求线程里同步执行，
    首屏 loadApp 会挂两分钟，且 GIL 被占死导致全服务所有接口变慢
    （前端表现：滚动 / 切页 / 切专业全部"等待响应"）。
    文件缺失或损坏时：后台启动评估，先返回零值占位结构，前端照常渲染。
    """
    if EVAL_PATH.exists():
        try:
            return json.loads(EVAL_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    state = evaluate_status()
    if state["status"] != "running":
        evaluate_async()
    return _empty_evaluation(state)


def _empty_evaluation(state: dict) -> dict:
    """评估尚未完成时的占位报告，字段结构与完整报告对齐，数值为零值。"""
    return {
        "status": "evaluating",
        "evaluation_status": state,
        "offline_train_report": {"auc": None, "sample_count": 0},
        "leave_one_out": {"averages": {"hit_rate@5": None, "hit_rate@24": None}},
        "ranking_eval": {
            "method": "评估正在后台运行，完成后自动展示完整指标。",
            "averages": {},
            "per_student": {},
        },
        "student_accounts": [],
        "effect_improvement_plan": [],
        "data_quality": {},
        "major_catalog_size": 0,
    }


@app.post("/api/admin/evaluate")
def run_evaluation():
    """立即返回；真正的评估在后台线程跑，用 /api/admin/evaluate-status 查进度。"""
    return evaluate_async()


@app.get("/api/admin/evaluate-status")
def evaluate_status_api():
    return evaluate_status()


@app.post("/api/admin/collect-live")
def collect_live(payload: CollectPayload):
    """立即返回；采集在后台线程跑，用 /api/admin/collect-status 查进度。"""
    return collect_async(
        video_per_platform=payload.video_per_platform,
        job_total=payload.job_total,
        collect_exam=payload.collect_exam,
        retrain=payload.retrain,
    )


@app.get("/api/admin/collect-status")
def collect_status_api():
    return collection_status()


@app.post("/api/admin/rebuild")
def rebuild():
    write_seed_files()
    reset_database()
    train_main()
    evaluation = evaluate()
    return {"ok": True, "report": recommender.load_model()["report"], "evaluation": evaluation}


@app.post("/api/admin/rebuild-profiles")
def rebuild_profiles():
    overview = rebuild_official_profiles()
    train_main()
    evaluation = evaluate()
    return {
        "ok": True,
        "accounts": overview,
        "report": recommender.load_model()["report"],
        "evaluation": evaluation,
    }


@app.get("/api/admin/rebuild-curriculum")
def rebuild_curriculum_api(per_course: int = 50):
    """按岳阳学院培养方案重建课程体系与课程资源池（幂等，可重复执行）。"""
    from .data_expansion import rebuild_all

    result = rebuild_all(per_course=per_course)
    train_main()
    result["evaluation"] = evaluate()
    return result


# ---------------------------------------------------------------- 课程封面

def _svg_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _wrap(text: str, per_line: int, max_lines: int = 2) -> list[str]:
    text = (text or "").strip()
    if not text:
        return [""]
    lines = [text[i:i + per_line] for i in range(0, len(text), per_line)]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: per_line - 1] + "…"
    return lines


def _shade(hex_color: str, factor: float) -> str:
    hex_color = (hex_color or "#5B8DEF").lstrip("#")
    if len(hex_color) != 6:
        hex_color = "5B8DEF"
    rgb = [int(hex_color[i:i + 2], 16) for i in (0, 2, 4)]
    if factor >= 0:
        rgb = [int(c + (255 - c) * factor) for c in rgb]
    else:
        rgb = [int(c * (1 + factor)) for c in rgb]
    return "#%02X%02X%02X" % tuple(max(0, min(255, c)) for c in rgb)


def build_cover_svg(video: dict) -> str:
    """按视频信息生成课程封面：主色渐变 + 课程名 + 平台角标 + 数据指标。

    不用外部图床，离线也能渲染，避免演示现场断网白图。
    """
    base = _shade(video.get("cover_color") or "#5B8DEF", 0.0)
    top = _shade(base, 0.18)
    bottom = _shade(base, -0.42)
    course_name = video.get("course_name") or (video.get("title") or "").split("｜")[0]
    platform = video.get("platform") or "课程"
    semester = video.get("course_semester")
    kind = video.get("course_kind") or ""
    popularity = int(video.get("popularity") or 0)
    episodes = int(video.get("episodes") or 0)

    lines = _wrap(course_name, 8, 2)
    title_svg = "".join(
        f'<text x="26" y="{92 + i * 30}" font-size="25" font-weight="700" '
        f'fill="#FFFFFF" font-family="Microsoft YaHei,PingFang SC,sans-serif">{_svg_escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    meta_bits = [f"{popularity / 10000:.1f}万播放"]
    if episodes:
        meta_bits.append(f"共{episodes}讲")
    if semester:
        meta_bits.append(f"第{semester}学期")
    meta = "  ·  ".join(meta_bits)
    chip = f"{kind} · {semester}学期" if kind and semester else kind or "培养方案课程"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180" width="320" height="180" role="img" aria-label="{_svg_escape(course_name)}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{top}"/>
      <stop offset="100%" stop-color="{bottom}"/>
    </linearGradient>
  </defs>
  <rect width="320" height="180" fill="url(#g)"/>
  <circle cx="286" cy="26" r="62" fill="#FFFFFF" opacity="0.10"/>
  <circle cx="52" cy="168" r="52" fill="#000000" opacity="0.10"/>
  <rect x="26" y="24" width="{max(58, len(platform) * 15 + 22)}" height="26" rx="13" fill="#FFFFFF" opacity="0.9"/>
  <text x="{26 + max(58, len(platform) * 15 + 22) / 2}" y="42" font-size="14" font-weight="700"
        text-anchor="middle" fill="{bottom}" font-family="Microsoft YaHei,PingFang SC,sans-serif">{_svg_escape(platform)}</text>
  {title_svg}
  <text x="26" y="146" font-size="13" fill="#FFFFFF" opacity="0.85"
        font-family="Microsoft YaHei,PingFang SC,sans-serif">{_svg_escape(meta)}</text>
  <text x="26" y="166" font-size="12" fill="#FFFFFF" opacity="0.65"
        font-family="Microsoft YaHei,PingFang SC,sans-serif">{_svg_escape(chip)}</text>
</svg>"""


@app.get("/api/cover/{video_id}.svg")
def cover(video_id: str):
    db = con()
    row = db.execute(
        """
        SELECT v.*, c.semester AS course_semester, c.course_kind AS course_kind
        FROM video v LEFT JOIN course c ON v.course_id = c.course_id
        WHERE v.video_id=?
        """,
        (video_id,),
    ).fetchone()
    if not row:
        return Response(
            content=build_cover_svg({"course_name": "课程资源", "cover_color": "#5B8DEF"}),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return Response(
        content=build_cover_svg(dict(row)),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ---------------------------------------------------------------- 专业与资源统计

@app.get("/api/majors")
def majors():
    db = con()
    data = [
        dict(row)
        for row in db.execute(
            """
            SELECT m.*,
                   (SELECT COUNT(*) FROM video v WHERE v.major=m.name) AS resource_count
            FROM major m ORDER BY m.major_id
            """
        ).fetchall()
    ]
    return {"school": "岳阳学院", "total": len(data), "majors": data}


@app.get("/api/stats/resources")
def resource_stats(student_id: str = DEFAULT_STUDENT_ID):
    return recommender.major_resource_stats(student_id)
