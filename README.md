# 学途 Lite 学生端推荐平台

这是按给定提示词重新完成的一套全新实现，包含数据获取/入库、后端 API、推荐模型训练、在线预测、学生端前端 UI 与学习闭环。

## 技术栈

- 前端：原生 HTML/CSS/JavaScript，复刻参考 PDF 的深色侧栏、顶部搜索、三标签、课程色块卡片、岗位卡片和播放页布局。
- 后端：FastAPI + SQLite，提供登录、SSO 校验、推荐、岗位路径、考研路径、播放进度和埋点接口。
- 推荐：多路召回 + TF-IDF 内容向量 + ItemCF + 轻量 Logistic Ranker。
- 部署：CPU-only，无 GPU、向量库或大型模型依赖，适合普通竞赛环境本地启动。

## 核心链路

1. `backend/data_acquisition.py` 生成并导入学生、课程、视频、岗位、技能映射、考研科目、学习记录、成就、行为埋点和推荐日志。
2. `backend/train_model.py` 从 SQLite 读取样本，训练轻量排序模型，输出 `data/artifacts/ranker_model.json`。
3. `backend/recommender.py` 在线执行专业路径、就业路径、考研路径的召回、排序与路径生成。
4. `backend/app.py` 暴露 API 并托管前端页面。

## 运行

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild
python -m backend.data_acquisition
python -m backend.train_model
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

打开：

- 页面：`http://127.0.0.1:8765`
- 演示账号：`20240101`
- 演示密码：`123456`
- 模型报告：`http://127.0.0.1:8765/api/admin/model-report`

> 只想把页面跑起来看效果的话，上面三步里**只需要最后一条**：`backend/app.py` 启动时会自己调 `bootstrap()`，数据库和模型文件不存在时会自动建库、训练（`app.py:30-47`）。
>
> 注意 `python -m backend.train_model` 是手动重训入口，当前 `train_model.py:314` 的 `main()` 调用了不存在的 `train_rank_model()`（实际函数名是 `train_fold_model`），手动执行会 NameError；走服务启动不会触发，因为模型文件已存在。详见 `learn/docs/推荐系统算法与特征工程详解.md` 的已知问题章节。

## 前端启动

前端是纯静态的 HTML/CSS/JS，**没有 npm、没有 webpack/vite、没有独立的开发服务器**，不需要 `npm install`，也不需要单独起一个前端进程。

它由后端 FastAPI 顺手托管，靠的是 `backend/app.py` 里这三处：

| 位置 | 代码 | 作用 |
| --- | --- | --- |
| `app.py:64` | `app.mount("/static", StaticFiles(directory=FRONTEND))` | 把 `frontend/` 目录挂到 `/static`，浏览器就能取到 `app.js`、`styles.css` |
| `app.py:104-106` | `@app.get("/")` 返回 `frontend/index.html` | 首页 |
| `app.py:109-111` | `@app.get("/play/{video_id}")` 返回同一个 `index.html` | 播放页，由前端按 `location.pathname` 自己分流（`app.js:109`） |

**所以「启动前端」= 启动后端服务，再打开浏览器访问对应地址。**

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

启动成功后访问：

| 入口 | 地址 |
| --- | --- |
| 首页（登录 + 三标签推荐） | `http://127.0.0.1:8765/` |
| 播放页（需先登录） | `http://127.0.0.1:8765/play/{video_id}` |
| 前端源码（可直接看） | `http://127.0.0.1:8765/static/app.js`、`http://127.0.0.1:8765/static/styles.css` |

登录用演示账号 `20240101` / `123456`（另有 `20240102`~`20240105`，密码相同）。

### 前端三个文件分别管什么

| 文件 | 作用 |
| --- | --- |
| `frontend/index.html` | 页面骨架，主体只有一个空壳 `<div id="app">`，内容全靠 JS 渲染 |
| `frontend/styles.css` | 全部样式：深色侧栏、顶部搜索、课程色块卡片、播放页布局 |
| `frontend/app.js` | 全部逻辑：登录、三标签切换、卡片渲染、播放页、埋点上报 |

`index.html` 用**绝对路径**引用资源，这一点很关键：

```html
<link rel="stylesheet" href="/static/styles.css?v=20260904-five-profiles" />
<script src="/static/app.js?v=20260904-five-profiles"></script>
```

### 不要双击 index.html 直接打开

双击 `frontend/index.html`（走 `file://` 协议）会白屏或报错，两个原因：

1. `app.js:1` 是 `const API = ""`，所有请求都走相对路径（如 `api("/api/profile/...")`）。在 `file://` 下会变成 `file:///api/profile/...`，必然失败。
2. `index.html` 里的 `/static/app.js` 在 `file://` 下会去找系统盘根目录。

**必须通过 `http://127.0.0.1:8765/` 访问**，也就是后端服务必须跑着。前端和接口同源，所以没有跨域问题，也不需要配代理。

### 改了前端代码怎么生效

后端给所有响应加了 `Cache-Control: no-store`（`app.py:57-61`），所以改完 `app.js` / `styles.css` **直接刷新浏览器即可**，不用清缓存，也不用重启服务。

### 前端到后端的完整启动链路

```
浏览器打开 http://127.0.0.1:8765/
   ↓  后端返回 frontend/index.html
   ↓  浏览器再取 /static/styles.css、/static/app.js
app.js 执行，先看 sessionStorage 里有没有登录态（app.js:561）
   ├── 没有 → 渲染登录页，等用户输入 20240101 / 123456
   └── 有   → loadApp() 并发请求 6 个接口（app.js:114-120）
              /api/profile/{student_id}
              /api/recommendations/professional
              /api/recommendations/jobs
              /api/recommendations/exam
              /api/admin/data-quality
              /api/admin/model-evaluation
           → 拿到 JSON 后渲染成侧栏 + 卡片列表
```

### 教学版前端（可选，跟上面互不影响）

项目里另有一套配套的教学页面，在 `learn/` 下，**独立进程、独立端口**：

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild
python learn/start.py
```

访问 `http://127.0.0.1:8766`。它需要主服务（8765）也跑着，因为教学页里的「后端接口实验室」会直接请求主项目接口。

## 关键 API

- `POST /api/login`：账号密码登录。
- `GET /api/sso`：学号、时间戳、HMAC 签名透传校验。
- `GET /api/profile/{student_id}`：左侧成长栏数据。
- `GET /api/recommendations/professional`：专业路径推荐。
- `GET /api/recommendations/jobs`：岗位匹配和技能反推路径。
- `GET /api/recommendations/exam`：考研公共课和专业课路径。
- `GET /api/videos/{video_id}`：播放页详情和集数。
- `POST /api/videos/{video_id}/progress`：学习进度与自动成就。
- `POST /api/track`：曝光、点击、播放、点赞、收藏等埋点。
- `GET /api/admin/data-quality`：数据来源、当前规模、专业覆盖率、更新频率和真实采集状态。
- `GET /api/admin/model-evaluation`：模型训练报告、Precision@5、Recall@5、NDCG@5、覆盖率和多样性指标。
- `POST /api/admin/evaluate`：重新生成模型效果评估。

## 模型选择说明

学生视频课程推荐有三个特点：课程先修顺序强、冷启动学生多、外部视频数据质量参差不齐。因此本项目没有选择重 GPU 的深度排序模型，而是选择可解释、低成本的混合方案：

- 用课程先修 DAG 和学期状态保证推荐顺序合理。
- 用 TF-IDF 内容向量解决新课程冷启动。
- 用 ItemCF 学习行为捕捉相似课程偏好。
- 用 Logistic Ranker 综合热度、评分、平台偏好、免费/付费、课程图谱关系和协同相似度。

后续真实接入更大规模行为数据后，可以平滑升级到 GBDT+LR 或 DeepFM，但当前方案更适合竞赛演示和低硬件部署。

## 数据边界与正式采集目标

数据库保留演示种子数据和真实公开采集数据，只有带 `source_collected_at` 且通过时间窗口校验的数据才计入“真实采集”统计：

- B站：已执行真实公开搜索采集，最近 6 年课程 97 门全部真实，覆盖 5 个目标专业，带点赞/收藏/播放/上传日期；重复运行时按 BV 号去重更新。
- MOOC、极客时间：两个站点公开搜索仅返回 JS 空壳/未收录课程页，无法自动回读近 6 年课程日期；采集报告标记失败并写明原因，不伪造课程。
- 招聘岗位：拉勾网/BOSS直聘公开搜索被阿里云验证码/风控拦截，自动化脚本无法合法取得岗位卡片；当前保留 4 条演示岗位作为展示，真实岗位采集需平台授权接口或人工导出后入库。
- 考研科目：已按研招网 2027 官方硕士目录真实核对湖南研招单位，保存 31 条“院校-本科专业-考试科目”记录，覆盖 5 个专业、15 所实际开设有对应硕士专业的湖南高校；未开设专业不伪造空科目。
- 覆盖结论：`/api/admin/data-quality` 实时计算库内规模、真实采集规模、专业覆盖、数据窗口和最近采集历史，页面在登录后的“数据质量报告/数据与模型”查看。

## 模型效果测试方案

已新增 `backend/evaluate_model.py` 和 `/api/admin/model-evaluation`。当前测试以学习进度 `>= 25%` 的课程作为弱正样本，对专业路径推荐进行离线评估：

- `Precision@5`：前 5 个推荐中命中弱正样本的比例。
- `Recall@5`：弱正样本被前 5 推荐召回的比例。
- `HitRate@5`：学生维度前 5 推荐是否至少命中一个正样本。
- `NDCG@5`：命中结果在推荐列表中的排序位置质量。
- `catalog_coverage`：推荐结果覆盖课程库的比例。
- `platform_diversity`：MOOC、B站、极客时间的推荐多样性。

推荐排序已增强为“模型预测 + 召回理由强度 + 课程质量 + 免费/付费约束 + MMR 多样性重排”。这会比单纯按热门或单纯按模型分更适合学生课程推荐：既照顾正在学、已学进阶和预习路径，又避免同平台/同主题课程挤占全部推荐位。

当前本地重训结果：97 条曝光样本、正样本率 7.2%、离线 AUC 0.9841，`Precision@5 0.20 / Recall@5 0.78 / HitRate@5 1.00 / NDCG@5 0.70`。评估文件位于 `data/artifacts/model_evaluation.json`。

## 真实数据采集与更新

已新增 `backend/live_collectors.py`，用于执行真实联网采集、时间过滤、去重入库、采集报告记录、自动重训和评估刷新。

目标约束：

- 专业覆盖：`计算机科学与技术`、`软件工程`、`人工智能`、`数据科学与大数据技术`、`网络工程`。
- 视频教程：B站、MOOC、极客时间每个平台目标 50 门课程；只保留 6 年内上传或公开页面可确认为当前有效的课程。
- 招聘岗位：拉勾网 + BOSS直聘目标 200 条；只保留 2 年内发布或当前公开列表中的有效岗位。
- 考研科目：湖南所有研招单位，按 5 个目标专业查询最新年份招生目录/考试科目。
- 数据更新：每次采集都会写入 `collection_run` 和 `collector_state`，并刷新 `data/artifacts/live_collection_report.json`。

运行命令：

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild
python -m backend.live_collectors --video-per-platform 50 --job-total 200
```

前端入口：登录后进入“数据与模型”页面，点击“采集真实数据”。

后端接口：

- `POST /api/admin/collect-live`：执行真实采集、入库、重训和评估。
- `GET /api/admin/data-quality`：查看最新采集规模、平台分布、专业覆盖和采集历史。

合规说明：采集器只访问公开页面或公开接口，不绕过登录、验证码、付费墙和反爬限制。若目标站点拒绝访问或需要授权，采集报告会标记为未达标，并记录失败原因。

## 采集失败排障

如果所有 HTTPS 数据源同时出现 `sslkey.log` 权限错误，通常是本机外部代理设置了不可写的 `SSLKEYLOGFILE`。采集器启动时会自动清除该变量。

如果 B站出现 `HTTP 412`，采集器会使用正常的关键词 Referer/Origin、限速和瞬时失败恢复；如果公开接口最终仍拒绝访问，会记录为失败，不会伪造入库数量。

如果 MOOC、极客时间、拉勾或 BOSS 只返回空页面、TLS 超时或需要登录，报告会显示“未达到目标数量”及原因。这类情况需要使用平台授权接口、允许访问的公开导出文件或在合规网络环境执行，不能靠绕过风控解决。

SQLite 采集写入使用 30 秒等待；采集循环有总时限和连续失败熔断，避免单个站点异常拖住整次更新。
