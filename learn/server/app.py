# -*- coding: utf-8 -*-
"""
learn 教学服务的主程序。

端口 8766（主项目是 8765，互不干扰）。

它对外提供四类能力：
    1. 知识点索引      —— 每条都标了归属（前端/后端/数据库/推荐）和源码出处
    2. SQL 沙箱        —— 在练习库副本上执行 SQL
    3. Python 沙箱     —— 在线写后端代码操作数据库
    4. 推荐模型演练场  —— 7 个模型由易到难，可横向对比指标
另外还代理转发主项目的接口，方便在页面上直接「发请求看返回」。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db_sandbox, models_playground
from .code_runner import PRESET_CODE, run_code
from .db_sandbox import PRESET_SQL, describe_schema, ensure_test_db, execute_sql
from .knowledge import CHAPTERS, LAYER_META, search as knowledge_search

ROOT = Path(__file__).resolve().parents[1]          # learn/
STATIC_DIR = ROOT / "static"
DEMO_DIR = STATIC_DIR / "demos"
PROJECT_ROOT = ROOT.parent                           # xuetu-lite-rebuild/
MAIN_DB = PROJECT_ROOT / "data" / "processed" / "xuetu_lite.db"

MAIN_APP_BASE = "http://127.0.0.1:8765"              # 主项目地址

ensure_test_db()

app = FastAPI(title="学途 Lite 边跑边学", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_store_static(request, call_next):
    """所有响应禁止缓存 —— 和主项目 app.py:57 同款做法。

    不加这个的话，改完 learn.js 浏览器还在用旧文件，能坑人到怀疑人生。
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------- 请求体模型
class SqlPayload(BaseModel):
    sql: str


class CodePayload(BaseModel):
    code: str


class ProxyPayload(BaseModel):
    method: str = "GET"
    path: str
    params: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------- 页面
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "service": "learn", "test_db": ensure_test_db().get("ok", False)}


@app.get("/api/learn/status")
def status():
    """检查主项目服务是否在线，页面用它给出提示。"""
    try:
        res = requests.get(f"{MAIN_APP_BASE}/health", timeout=3)
        return {"main_online": res.status_code == 200, "main_base": MAIN_APP_BASE, "detail": res.json()}
    except Exception as exc:  # noqa: BLE001
        return {"main_online": False, "main_base": MAIN_APP_BASE, "detail": str(exc)}


# ---------------------------------------------------------------- 知识点
@app.get("/api/learn/knowledge")
def knowledge():
    return {"layers": LAYER_META, "chapters": CHAPTERS}


@app.get("/api/learn/knowledge/search")
def knowledge_search_api(q: str = ""):
    return {"results": knowledge_search(q)}


@app.get("/api/learn/demo/{name}", response_class=PlainTextResponse)
def get_demo(name: str):
    """返回某个前端示例的完整 HTML 源码，页面上用于「看代码 / 复制代码」。"""
    safe = Path(name).name                      # 防目录穿越
    path = DEMO_DIR / safe
    if not path.exists() or path.suffix != ".html":
        raise HTTPException(status_code=404, detail="找不到该示例文件")
    return path.read_text(encoding="utf-8")


@app.get("/api/learn/demos")
def list_demos():
    """列出所有可运行的前端示例。"""
    out = []
    for path in sorted(DEMO_DIR.glob("*.html")):
        first_line = path.read_text(encoding="utf-8").splitlines()[:1]
        out.append({"file": path.name, "size": path.stat().st_size, "head": first_line[0] if first_line else ""})
    return {"demos": out}


# ---------------------------------------------------------------- SQL 沙箱
@app.get("/api/learn/preset-sql")
def preset_sql():
    return {"items": PRESET_SQL}


@app.post("/api/learn/sql")
def run_sql(payload: SqlPayload):
    return execute_sql(payload.sql)


@app.get("/api/learn/schema")
def schema():
    return describe_schema()


@app.post("/api/learn/reset-db")
def reset_db():
    """从正式库重新复制一份练习库，把之前练坏的数据全部还原。"""
    result = ensure_test_db(force=True)
    result["schema"] = describe_schema().get("tables", [])
    return result


# ---------------------------------------------------------------- Python 沙箱
@app.get("/api/learn/preset-code")
def preset_code():
    return {"items": PRESET_CODE}


@app.post("/api/learn/run-code")
def run_python(payload: CodePayload):
    return run_code(payload.code)


# ---------------------------------------------------------------- 推荐模型演练场
@app.get("/api/learn/models")
def models():
    return {
        "models": [
            {"key": m["key"], "level": m["level"], "name": m["name"], "tag": m["tag"], "desc": m["desc"]}
            for m in models_playground.MODELS
        ]
    }


@app.get("/api/learn/model/run")
def model_run(key: str = "popularity", student_id: str = "20240101", topn: int = 9):
    try:
        return models_playground.run_model(key, student_id, topn)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/learn/model/compare")
def model_compare(student_id: str = "20240101"):
    try:
        return {"student_id": student_id, "rows": models_playground.compare_all(student_id)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------- 主项目接口代理
# 页面上的「后端在线测试」走这里，避免浏览器跨域问题
@app.post("/api/learn/proxy")
def proxy(payload: ProxyPayload):
    url = f"{MAIN_APP_BASE}{payload.path}"
    try:
        if payload.method.upper() == "POST":
            res = requests.post(url, json=payload.body, params=payload.params, timeout=60)
        else:
            res = requests.get(url, params=payload.params, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=200,
            content={"ok": False, "status": 0, "error": str(exc),
                     "hint": "主项目未启动？执行：python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765"},
        )
    text = res.text
    try:
        body = res.json()
    except Exception:  # noqa: BLE001
        body = text
    return {"ok": res.ok, "status": res.status_code, "body": body}


# d10-track.html 用这个接口把刚写入的埋点读出来
@app.get("/api/learn/behavior-log")
def behavior_log(student_id: str = "20240101", limit: int = 10):
    if not MAIN_DB.exists():
        return []
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, student_id, event_type, video_id, duration, created_at "
            "FROM behavior_log WHERE student_id=? ORDER BY id DESC LIMIT ?",
            (student_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------- 主项目 API 清单
MAIN_APIS = [
    {"method": "GET", "path": "/health", "params": [],
     "desc": "健康检查：看数据库和模型是否就绪", "source": "backend/app.py:114-116"},
    {"method": "GET", "path": "/api/demo-accounts", "params": [],
     "desc": "5 个演示账号及其学习概况", "source": "backend/app.py:142-144"},
    {"method": "POST", "path": "/api/login", "params": [],
     "body": {"student_id": "20240101", "password": "123456"},
     "desc": "账号密码登录，返回 token 与学生信息", "source": "backend/app.py:119-125"},
    {"method": "GET", "path": "/api/sso", "params": [
        {"name": "student_id", "value": "20240101"},
        {"name": "ts", "value": "1700000000"},
        {"name": "sig", "value": "wrong-signature"}],
     "desc": "HMAC 签名免登录（这里故意传错签名演示 401）", "source": "backend/app.py:128-134"},
    {"method": "GET", "path": "/api/profile/20240101", "params": [],
     "desc": "学生画像：基本信息 + 培养方案 + 成就 + 学习记录", "source": "backend/app.py:137-139"},
    {"method": "GET", "path": "/api/recommendations/professional", "params": [
        {"name": "student_id", "value": "20240101"},
        {"name": "filter", "value": "all"}],
     "desc": "专业路径推荐；filter 可改成 studying / advanced / vacation",
     "source": "backend/app.py:147-149"},
    {"method": "GET", "path": "/api/recommendations/jobs", "params": [
        {"name": "student_id", "value": "20240101"}],
     "desc": "岗位匹配 + 技能反推学习路径", "source": "backend/app.py:152-154"},
    {"method": "GET", "path": "/api/recommendations/exam", "params": [
        {"name": "student_id", "value": "20240101"},
        {"name": "school", "value": ""},
        {"name": "major", "value": ""}],
     "desc": "考研路径：公共课 + 专业课（可指定院校专业）", "source": "backend/app.py:157-159"},
    {"method": "GET", "path": "/api/videos/V002", "params": [
        {"name": "student_id", "value": "20240101"}],
     "desc": "视频详情：全部集数 + 该学生的进度", "source": "backend/app.py:162-164"},
    {"method": "POST", "path": "/api/videos/V002/progress", "params": [],
     "body": {"student_id": "20240101", "episode_no": 6},
     "desc": "更新学习进度（UPSERT，会真的写库）", "source": "backend/app.py:178-207"},
    {"method": "POST", "path": "/api/track", "params": [],
     "body": {"student_id": "20240101", "video_id": "V002", "event_type": "click", "duration": 0},
     "desc": "埋点上报：写一条行为日志", "source": "backend/app.py:167-175"},
    {"method": "GET", "path": "/api/admin/model-report", "params": [],
     "desc": "训练报告：样本数、正样本率、AUC、8 个特征名", "source": "backend/app.py:224-226"},
    {"method": "GET", "path": "/api/admin/model-evaluation", "params": [],
     "desc": "离线评估：Precision@5 / Recall@5 / NDCG@5 等", "source": "backend/app.py:234-238"},
    {"method": "GET", "path": "/api/admin/data-quality", "params": [],
     "desc": "数据来源、覆盖率、采集历史", "source": "backend/app.py:229-231"},
]


@app.get("/api/learn/apis")
def apis():
    return {"base": MAIN_APP_BASE, "apis": MAIN_APIS}
