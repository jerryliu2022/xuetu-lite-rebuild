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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import recommender
from .data_quality import quality_report
from .data_acquisition import DB_PATH, reset_database, write_seed_files
from .evaluate_model import EVAL_PATH, evaluate
from .live_collectors import ensure_schema as ensure_live_schema
from .student_profiles import account_overview, ensure_student_profiles, rebuild_official_profiles
from .train_model import MODEL_PATH, main as train_main

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SSO_SECRET = b"xuetu-lite-demo-secret"


def bootstrap() -> None:
    if not DB_PATH.exists():
        write_seed_files()
        reset_database()
    ensure_live_schema()
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
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


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


@app.post("/api/login")
def login(payload: LoginPayload):
    db = con()
    student = db.execute("SELECT * FROM student WHERE student_id=?", (payload.student_id,)).fetchone()
    if not student or payload.password not in {"123456", "xuetu"}:
        raise HTTPException(status_code=401, detail="学号或密码错误，演示账号密码为 20240101 / 123456")
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
def professional(student_id: str = "20240101", filter: str = "all"):
    return recommender.recommend_professional(student_id, filter)


@app.get("/api/recommendations/jobs")
def jobs(student_id: str = "20240101", job_id: Optional[str] = None):
    return recommender.jobs_for_student(student_id, job_id)


@app.get("/api/recommendations/exam")
def exam(student_id: str = "20240101", school: Optional[str] = None, major: Optional[str] = None):
    return recommender.exam_recommendations(student_id, school, major)


@app.get("/api/videos/{video_id}")
def video(video_id: str, student_id: str = "20240101"):
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
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))["report"]


@app.get("/api/admin/data-quality")
def data_quality():
    return quality_report()


@app.get("/api/admin/model-evaluation")
def model_evaluation():
    if not EVAL_PATH.exists():
        return evaluate()
    return json.loads(EVAL_PATH.read_text(encoding="utf-8"))


@app.post("/api/admin/evaluate")
def run_evaluation():
    return evaluate()


@app.post("/api/admin/collect-live")
def collect_live(payload: CollectPayload):
    from .live_collectors import run_live_collection

    return run_live_collection(
        video_per_platform=payload.video_per_platform,
        job_total=payload.job_total,
        collect_exam=payload.collect_exam,
        retrain=payload.retrain,
    )


@app.post("/api/admin/rebuild")
def rebuild():
    write_seed_files()
    reset_database()
    train_main()
    evaluation = evaluate()
    return {"ok": True, "report": json.loads(MODEL_PATH.read_text(encoding="utf-8"))["report"], "evaluation": evaluation}


@app.post("/api/admin/rebuild-profiles")
def rebuild_profiles():
    overview = rebuild_official_profiles()
    train_main()
    evaluation = evaluate()
    return {
        "ok": True,
        "accounts": overview,
        "report": json.loads(MODEL_PATH.read_text(encoding="utf-8"))["report"],
        "evaluation": evaluation,
    }
