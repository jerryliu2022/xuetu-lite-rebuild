# 学途 Lite 学生端推荐平台

面向**岳阳学院 2026 年招生专业**的学生课程推荐系统：数据底座来自学校官方的招生专业页与教务处人才培养方案，推荐链路按「专业 + 学期」定位，输出专业路径、就业路径、考研路径三条主线和一套学习闭环。

当前数据规模：

| 维度 | 数量 |
| --- | --- |
| 招生专业 | 25 个（马克思主义学院、化工与材料、机械与智能制造、信息工程、经济与管理、文学与传媒、艺术与体育、食品科学与工程） |
| 培养方案课程 | 898 门 |
| 课程资源 | 44,997 条（每门课平均约 50 条，其中 B 站真实联网采集计入 `data_origin='live'`） |
| 招聘岗位 | 150 条（25 个专业各 6 条） |
| 技能 → 课程映射 | 216 条，覆盖 111 个技能 |
| 考研科目 | 研招网最新年份真实核对记录 |
| 学生演示账号 | 25 个，每专业一个 |

## 技术栈

- 前端：原生 HTML/CSS/JavaScript，无构建工具。浅色/深色双主题（`data-theme` + CSS 变量驱动）、课程封面卡片网格、岗位反推学习路径、播放页。
- 后端：FastAPI + SQLite，提供登录、SSO 校验、三路推荐、资源统计、封面生成、播放进度和埋点接口。
- 推荐：多路召回 + TF-IDF 内容向量 + ItemCF（课程级降维）+ 轻量 Logistic Ranker + MMR 多样性重排。
- 部署：CPU-only，无 GPU、向量库或大型模型依赖，适合普通竞赛环境本地启动。

## 核心链路

1. `backend/yueyang_curriculum.py` 存放岳阳学院 25 个招生专业的培养方案课程体系（来源于招生专业页与教务处人才培养方案），是全部数据的唯一口径来源。
2. `backend/data_expansion.py` 按培养方案重建 `major` / `course` 表，为每门课生成 50 条课程资源，把联网采集到的真实 B 站课程归位到对应课程，并造出真实感的学习行为。
3. `backend/job_market.py` 按 25 个专业重建 `job` 与 `skill_course_map`，把岗位技能映射回**培养方案里真实存在的那门课**，技能反推路径才走得通。
4. `backend/train_model.py` 从 SQLite 读取曝光样本，训练轻量排序模型，输出 `data/artifacts/ranker_model.json`。
5. `backend/recommender.py` 在线执行三路召回、排序与路径生成。
6. `backend/evaluate_model.py` 做离线评估，输出 `data/artifacts/model_evaluation.json`。
7. `backend/app.py` 暴露 API 并托管前端页面。

## 运行

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild

# 首次或数据需要重建时执行（幂等，可重复跑）
python -m backend.data_expansion          # 重建 25 个专业的课程体系与课程资源池
python -m backend.train_model             # 重训排序模型

# 启动服务
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

打开：

- 页面：`http://127.0.0.1:8765`
- 演示账号：`YY08`（计算机科学与技术），其余 `YY01`~`YY25` 对应另外 24 个专业
- 演示密码：`123456`
- 模型报告：`http://127.0.0.1:8765/api/admin/model-report`

> 只想把页面跑起来看效果的话，**只需要最后一条**：`backend/app.py` 启动时会调 `bootstrap()`，检测到数据库里还没有岳阳学院的课程体系就会自动重建，模型缺失时自动训练（`app.py:48-67`）。
>
> 手动重训入口 `python -m backend.train_model` 可正常执行，会打印样本数、正样本率和 AUC。注意它**会覆盖** `data/artifacts/ranker_model.json`，重训前建议先备份该文件。
>
> （该命令曾抛 `NameError`：`train_model.py` 的 `main()` 调用了不存在的 `train_rank_model()`，实际函数名是 `train_fold_model`。已修复，详见 `learn/docs/推荐系统算法与特征工程详解.md` 9.7 节。）

## 界面设计

### 浅色 / 深色主题

两个主题都由 CSS 变量驱动，写在 `frontend/styles.css` 的 `:root` 与 `:root[data-theme="dark"]` 里，切换只改 `<html data-theme>` 一个属性，不重排 DOM。

| 位置 | 键 |
| --- | --- |
| 登录页右上角 | `#themeToggle` |
| 主界面顶栏右侧 | `#themeToggle` |
| 播放页顶部面包屑 | `#themeToggle` |

行为：

- 首次访问跟随系统 `prefers-color-scheme`；
- 用户点过之后写入 `localStorage["xuetu-theme"]`，此后优先用用户选择；
- 按钮上是 `aria-pressed`，图标与文案跟随当前主题（浅色时显示「深色」，反之亦然）。

配色刻意避开「深色 + 蓝」的严肃感：主色是紫 `#6c5ce7`，搭配薄荷青 `#00c2a8`、暖橙 `#ff8a5b` 和阳光黄 `#ffc233`；卡片 16px 圆角、按钮悬停微放大 1.02、点击缩到 0.97。

### 响应式断点

| 断点 | 课程网格 | 侧边栏 |
| --- | --- | --- |
| ≤ 1400px | 一行 4 个 | 常驻 |
| ≤ 1180px | 一行 3 个 | 折成顶部整块 |
| ≤ 900px | 一行 2 个 | 同上 |
| ≤ 560px | 一行 1 个 | 同上 |

无障碍：正文对比度按 WCAG 2.2 AA 校验，焦点环用 `:focus-visible` 统一处理，动画只作用在 `transform` / `opacity`，并响应 `prefers-reduced-motion`。

### 课程封面

每个推荐资源都有一张封面，由后端现算：

```
GET /api/cover/{video_id}.svg
```

`app.py` 的 `build_cover_svg()` 按课程的 `cover_color` 生成渐变底 + 课程名 + 平台角标 + 播放量/讲数/学期。**不用外部图床**，因为竞赛演示现场可能断网，外部图片一断就是一片白块。

### 前端验收工具

```powershell
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765   # 先起服务
python tools/ui_verify.py
```

`tools/ui_verify.py` 用本机无头 Chrome + DevTools Protocol 真实驱动页面，检查主题切换、四个标签页渲染、封面图片是否真的加载成功（`naturalWidth > 0`）、课程网格列数（默认宽度下应为 4 列）、1024 / 390 两个响应式宽度是否横向溢出、控制台异常与失败请求，并把截图写到 `data/artifacts/ui_shots/`、文字报告写到 `data/artifacts/ui_verify_report.txt`。

## 前端启动

前端是纯静态的 HTML/CSS/JS，**没有 npm、没有 webpack/vite、没有独立的开发服务器**，不需要 `npm install`，也不需要单独起一个前端进程。

它由后端 FastAPI 顺手托管，靠的是 `backend/app.py` 里这三处：

| 位置 | 代码 | 作用 |
| --- | --- | --- |
| `app.py` | `app.mount("/static", StaticFiles(directory=FRONTEND))` | 把 `frontend/` 目录挂到 `/static`，浏览器就能取到 `app.js`、`styles.css` |
| `app.py` | `@app.get("/")` 返回 `frontend/index.html` | 首页 |
| `app.py` | `@app.get("/play/{video_id}")` 返回同一个 `index.html` | 播放页，由前端按 `location.pathname` 自己分流 |

**所以「启动前端」= 启动后端服务，再打开浏览器访问对应地址。**

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

启动成功后访问：

| 入口 | 地址 |
| --- | --- |
| 首页（登录 + 四标签推荐） | `http://127.0.0.1:8765/` |
| 播放页（需先登录） | `http://127.0.0.1:8765/play/{video_id}` |
| 课程封面（可直接看） | `http://127.0.0.1:8765/api/cover/{video_id}.svg` |
| 前端源码（可直接看） | `http://127.0.0.1:8765/static/app.js`、`http://127.0.0.1:8765/static/styles.css` |

登录用演示账号 `YY08` / `123456`（另有 `YY01`~`YY25`，密码相同）。

### 前端三个文件分别管什么

| 文件 | 作用 |
| --- | --- |
| `frontend/index.html` | 页面骨架，主体只有一个空壳 `<div id="app">`，内容全靠 JS 渲染 |
| `frontend/styles.css` | 全部样式：双主题变量、侧栏、顶栏、课程卡片网格、岗位卡片、播放页布局 |
| `frontend/app.js` | 全部逻辑：主题切换、登录、四标签切换、卡片渲染、播放页、埋点上报 |

`index.html` 用**绝对路径**引用资源，这一点很关键：

```html
<link rel="stylesheet" href="/static/styles.css?v=20260916-theme-v3" />
<script src="/static/app.js?v=20260916-theme-v3"></script>
```

### 不要双击 index.html 直接打开

双击 `frontend/index.html`（走 `file://` 协议）会白屏或报错，两个原因：

1. `app.js` 里是 `const API = ""`，所有请求都走相对路径（如 `api("/api/profile/...")`）。在 `file://` 下会变成 `file:///api/profile/...`，必然失败。
2. `index.html` 里的 `/static/app.js` 在 `file://` 下会去找系统盘根目录。

**必须通过 `http://127.0.0.1:8765/` 访问**，也就是后端服务必须跑着。前端和接口同源，所以没有跨域问题，也不需要配代理。

### 改了前端代码怎么生效

后端给所有响应加了 `Cache-Control: no-store`（`app.py` 的 `no_store_static_assets` 中间件），所以改完 `app.js` / `styles.css` **直接刷新浏览器即可**，不用清缓存，也不用重启服务。

### 前端到后端的完整启动链路

```
浏览器打开 http://127.0.0.1:8765/
   ↓  后端返回 frontend/index.html
   ↓  浏览器再取 /static/styles.css、/static/app.js
app.js 执行，先看 sessionStorage 里有没有登录态
   ├── 没有 → 渲染登录页，等用户输入（默认填 YY08 / 123456）
   └── 有   → loadApp() 并发请求 8 个接口
              /api/profile/{student_id}
              /api/recommendations/professional?page_size=
              /api/recommendations/jobs
              /api/recommendations/exam
              /api/admin/data-quality
              /api/admin/model-evaluation
              /api/demo-accounts
              /api/stats/resources
           → 拿到 JSON 后渲染成侧栏 + 标签页内容
```

### 教学版前端（可选，跟上面互不影响）

项目里另有一套配套的教学页面，在 `learn/` 下，**独立进程、独立端口**：

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild
python learn/start.py
```

访问 `http://127.0.0.1:8766`。它需要主服务（8765）也跑着，因为教学页里的「后端接口实验室」会直接请求主项目接口。

教学页的示例账号与主项目**保持一致**（`YY01`~`YY25`），练习库 `learn/data/learn_test.db` 是主库的完整副本，首次启动或点「重置」时用 SQLite 官方 backup API 重新复制，所以主库换了数据底座它也会跟着换。

## 部署到阿里云服务器（后台运行）

本地和服务器只差一处：**监听地址从 `127.0.0.1` 换成 `0.0.0.0`**。

`127.0.0.1` 只监听本机回环，外部连不上；`0.0.0.0` 监听全部网卡，才能被公网访问。

前端不用改任何代码——`app.js` 里是 `const API = ""`，所有请求走相对路径，换成公网 IP 访问天然就通。

### 1. 装环境

项目要求 **Python 3.8+**（代码用了 `from __future__ import annotations` 来兼容 3.8 的 `X | None` 联合类型写法）。

```bash
# Alibaba Cloud Linux / CentOS
sudo yum install -y python3 python3-pip
python3 --version

# Ubuntu / Debian
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
python3 --version
```

> 阿里云 CentOS 7、Alibaba Cloud Linux 2/3 自带的 `python3` 是 **3.6，版本不够**。
> 装高版本：`sudo yum install -y python39 python39-pip`，之后把命令里的 `python3` 换成 `python3.9`。

### 2. 上传代码并建虚拟环境

```bash
sudo mkdir -p /opt/xuetu && sudo chown -R $USER /opt/xuetu
# 用 scp / 宝塔面板 / git 把项目传到 /opt/xuetu/xuetu-lite-rebuild

cd /opt/xuetu/xuetu-lite-rebuild
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

依赖只装 4 个包（`fastapi`、`uvicorn`、`pydantic`、`requests`），没有数据库驱动——SQLite 是 Python 自带的。

> 注意：前端验收工具 `tools/ui_verify.py` 额外需要 `websockets`，它只是开发期工具，服务器上不需要装。

### 3. 阿里云控制台放行端口

这一步只能在控制台点，没有命令：

**ECS 控制台 → 实例 → 安全组 → 配置规则 → 入方向 → 手动添加**

| 字段 | 填什么 |
| --- | --- |
| 协议类型 | 自定义 TCP |
| 端口范围 | `8765/8765`（教学页再加一条 `8766/8766`） |
| 授权对象 | `0.0.0.0/0` 表示对所有人开放；只给自己用就填你的本机公网 IP，更安全 |

如果服务器开了系统防火墙，还要单独放行：

```bash
# CentOS / Alibaba Cloud Linux
sudo firewall-cmd --add-port=8765/tcp --permanent && sudo firewall-cmd --reload

# Ubuntu
sudo ufw allow 8765/tcp
```

### 4. 先前台试跑一次

```bash
cd /opt/xuetu/xuetu-lite-rebuild
source .venv/bin/activate
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8765 --workers 1
```

看到 `Application startup complete` 就说明正常，浏览器访问 `http://服务器公网IP:8765` 应该能打开登录页。按 `Ctrl+C` 停掉，再往下做后台运行。

> **`--workers 1` 不要省。** 项目用 SQLite 单文件数据库，每个请求都新建连接，
> 多 worker 会并发争抢写锁；而且 `bootstrap()` 在**进程启动时同步执行**
> （可能要建库、重建课程资源池、训练模型），多进程同时启动会重复跑一遍。演示场景单 worker 完全够。

### 5. 后台运行（三选一）

#### 方式 A：nohup —— 最快，适合临时演示

```bash
cd /opt/xuetu/xuetu-lite-rebuild
mkdir -p logs

nohup .venv/bin/python -m uvicorn backend.app:app \
  --host 0.0.0.0 --port 8765 --workers 1 \
  > logs/app.log 2>&1 &

echo $! > logs/app.pid     # 记下 PID，方便后面停止
```

停止和看日志：

```bash
kill $(cat logs/app.pid)   # 停止服务
tail -f logs/app.log       # 实时看日志
```

缺点是**服务器重启后不会自动拉起**，适合演示当天临时用。

#### 方式 B：systemd —— 推荐，开机自启 + 崩溃自动重启

写服务文件：

```bash
sudo tee /etc/systemd/system/xuetu.service > /dev/null <<'EOF'
[Unit]
Description=Xuetu Lite 学途推荐服务
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/xuetu/xuetu-lite-rebuild
ExecStart=/opt/xuetu/xuetu-lite-rebuild/.venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8765 --workers 1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable xuetu    # 设置开机自启
sudo systemctl start xuetu     # 立即启动
sudo systemctl status xuetu    # 看运行状态
```

日常运维命令：

```bash
sudo systemctl restart xuetu               # 重启（改完代码用这个）
sudo systemctl stop xuetu                  # 停止
journalctl -u xuetu -f                     # 实时日志
journalctl -u xuetu --since "10 min ago"   # 看最近 10 分钟日志
```

两个关键点：

- `WorkingDirectory` 必须写对。`python -m uvicorn backend.app:app` 依赖 cwd 在项目根目录才能找到 `backend` 包，否则报 `ModuleNotFoundError: No module named 'backend'`。
- `ExecStart` 直接写虚拟环境里的 python 绝对路径，**不用**先 `source activate`。
- `User=root` 是为了省事。想更规范就新建专用用户，并把 `/opt/xuetu/xuetu-lite-rebuild/data` 的属主改成它，否则 SQLite 写不进去。

#### 方式 C：screen / tmux —— 需要进去交互调试时用

```bash
screen -S xuetu
cd /opt/xuetu/xuetu-lite-rebuild
.venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8765 --workers 1
# 按 Ctrl+A 然后按 D 脱离；下次 screen -r xuetu 回到这个会话
```

### 6. 教学页也一起部署（可选）

`learn/start.py` 把 host 写死成 `127.0.0.1` 了，服务器上要对外访问就绕开它，直接用 uvicorn 命令：

```bash
nohup .venv/bin/python -m uvicorn server.app:app --app-dir learn \
  --host 0.0.0.0 --port 8766 --workers 1 \
  > logs/learn.log 2>&1 &
```

同样记得在安全组放行 8766，并且主服务 8765 要先跑起来。

教学页内部靠 `http://127.0.0.1:8765` 转发主项目接口（`learn/server/app.py`），同机 localhost 通信，**不用改**。

> 一个诚实的提醒：`learn/static/demos/` 下那几个前端示例（`d02-fetch`、`d03-template`、`d05-form`、`d07-async`、`d10-track`）把后端地址写死成了 `http://127.0.0.1:8765`。这些代码是在**浏览器里**跑的，服务器部署后从自己电脑打开教学页，它们会去连你自己电脑的 8765 而不是服务器的，因此会连不上。这套 demo 本来就是为本地教学设计的，服务器上当作讲解材料看即可。

### 7. 部署后自检

```bash
curl http://127.0.0.1:8765/health
# 期望输出：{"ok":true,"db":true,"model":true}

curl -s "http://127.0.0.1:8765/api/demo-accounts" | head -c 200
curl -s "http://127.0.0.1:8765/api/stats/resources?student_id=YY08" | head -c 300
```

`/health` 三个字段都为 `true` 才算正常：`ok` 服务活着、`db` 数据库文件在、`model` 模型文件在。

### 8. 常见故障对照

| 现象 | 原因 |
| --- | --- |
| 服务器上 `curl` 通，外部浏览器打不开 | 阿里云安全组没放行 8765 |
| 监听地址显示 `127.0.0.1:8765`，外部连不上 | 启动时没加 `--host 0.0.0.0` |
| `ModuleNotFoundError: No module named 'backend'` | 启动时 cwd 不在项目根目录，systemd 要配 `WorkingDirectory` |
| 启动报 `Permission denied`，写不了数据库 | `data/` 目录属主和运行服务的用户不一致 |
| 首次启动卡十几秒到一分钟才响应 | `bootstrap()` 在重建课程资源池 + 训练模型（44,997 条资源），属正常，之后就快了 |
| 页面能开但课程卡片没有封面 | `/api/cover/{id}.svg` 被反代或 CDN 拦了；封面是后端现算的，不需要外网 |
| nohup 的 `logs/app.log` 越涨越大 | 用系统 `logrotate` 切割，或定期清空：`> logs/app.log` |

## 关键 API

### 学生端

- `POST /api/login`：账号密码登录。
- `GET /api/sso`：学号、时间戳、HMAC 签名透传校验。
- `GET /api/profile/{student_id}`：侧栏成长数据 + 本学期/下学期/往期课程。
- `GET /api/recommendations/professional`：专业路径推荐。支持 `filter=all|studying|advanced|vacation` 与 `page_size`（8~200，默认 24）。
- `GET /api/recommendations/jobs`：岗位匹配和技能反推路径。岗位池按学生专业收敛，匹配档位动态计算。
- `GET /api/recommendations/exam`：考研公共课和专业课路径。
- `GET /api/videos/{video_id}`：播放页详情和集数。
- `POST /api/videos/{video_id}/progress`：学习进度与自动成就。
- `POST /api/track`：曝光、点击、播放、点赞、收藏等埋点。

### 数据与资源

- `GET /api/cover/{video_id}.svg`：课程封面（SVG，离线可用，不需要外网）。
- `GET /api/majors`：25 个招生专业及各专业资源数。
- `GET /api/stats/resources`：每专业每门课的课程资源数 + 各平台分布。支持 `major=` 指定专业。
- `GET /api/demo-accounts`：25 个演示账号及有效性指标。

### 管理端

- `GET /api/admin/data-quality`：数据来源、当前规模、专业覆盖率、更新频率和真实采集状态。
- `GET /api/admin/model-evaluation`：模型评估报告。
- `POST /api/admin/evaluate`：重新生成模型效果评估（全量 25 个账号，约 7 分钟）。
- `POST /api/admin/collect-live`：执行真实联网采集、入库、重训和评估。
- `GET /api/admin/rebuild-curriculum`：按培养方案重建课程体系与资源池（幂等）。

## 推荐算法说明

学生视频课程推荐有三个特点：课程先修顺序强、冷启动学生多、外部视频数据质量参差不齐。因此本项目没有选择重 GPU 的深度排序模型，而是选择可解释、低成本的混合方案。

### 召回：按「专业 + 学期」定位

三路召回**严格限定在 `course.major = 学生专业` 之内**，这正是换岳阳学院培养方案后最关键的一处修正——原实现是全局取 `status='studying'` 的课程，等价于所有人看到同一批课。

| 路 | 触发条件 | 召回理由 |
| --- | --- | --- |
| 1 | 本学期课程 | 正在学习《X》，补充同课程资源 |
| 2 | 往期已修的专业基础/核心课 | 已学《X》，推荐后继进阶课《Y》 |
| 3 | 下学期课程 | 下学期将学《X》，适合假期预习 |
| 4 | 学生真实学习记录 | 基于你的学习记录做 ItemCF 相似召回 |
| 5 | 本专业热门补位 | 匹配培养方案，优先补齐高热度课程资源 |

每门课进候选池的条数按课程类型分档（`RESOURCE_QUOTA_BY_KIND`）：专业核心 8 条、学科基础 5 条、通识必修 2 条。不限制的话，「大学英语」这类全校公共课会靠播放量把专业路径挤下去。

### 排序：8 维特征 + 逻辑回归

特征维度严格保持 8 维，`ranker_model.json` 里的权重与均值方差继续可用：

`log_popularity`、`rating`、`free_access`、`curriculum_graph`、`itemcf_similarity`、`platform_preference`、`course_downstream_value`、`major_alignment`

最终混合分：

```
blended = 0.50 * 模型预测CTR
        + 0.32 * 召回理由强度 × 课程类型系数
        + 0.12 * 课程质量（热度 + 评分）
        + 0.06 * 免费权重
```

课程推荐场景下，**培养方案的路径正确性比短期点击偏好更重要**，所以召回理由强度的权重被提到 0.32，否则一次「高播放量的通识课外包资源」就能盖过「下学期该学的专业课」。

### 重排：MMR

同一门课最多占 3 个推荐位，同平台的位置有惩罚，主题高度相似的资源也会被压。

### 性能：ItemCF 从 O(n²) 降到课程级

视频数从 116 涨到 44,997 之后，原来的视频级 ItemCF（两两算相似度）直接把训练过程拖到被系统杀掉。现在改成**课程级 TF-IDF 建相似度，再把相似度按同课派生到视频**，候选规模不变但计算量降到课程数（898）量级。同时打分过程用 `ScoringContext` 把平台偏好、课程后继价值预计算并缓存，去掉每个候选重复查库。

## 数据来源与真实性边界

- **专业与课程体系**：来自岳阳学院 2026 年招生专业页（25 个专业）与教务处人才培养方案。计算机科学与技术等专业的学位课程为官网原文；个别专业官网未公开培养方案时，按《普通高等学校本科专业类教学质量国家标准》与同类院校通用方案补齐，并在数据文件里标注来源。
- **课程资源**：分两类。`data_origin='live'` 的是 B 站公开搜索真实联网采集，带上传日期与点赞/收藏数，计入「真实采集」统计；`data_origin='curriculum'` 的是按培养方案课程生成的资源池，不冒充真实采集。MOOC、极客时间、学堂在线、网易云课堂的公开搜索只返回 JS 空壳或未收录页面，无法验证日期，因此这几家的资源按培养方案课程生成。
- **招聘岗位**：`backend/job_market.py` 按 25 个专业构造的样本岗位，`source_collected_at` 一律留空，因此**不会**被 `/api/admin/data-quality` 计入真实采集——拉勾/BOSS 的公开搜索当前返回阿里云验证码页，无法合法自动采集。
- **考研科目**：已按研招网最新年份官方硕士目录核对湖南研招单位，只保存真实开设的院校-专业科目组合，未开设的不伪造空科目记录。
- **覆盖结论**：`/api/admin/data-quality` 实时计算库内规模、真实采集规模、专业覆盖、数据窗口和最近采集历史，页面在登录后的「数据与模型」查看。

## 模型效果测试方案

评估代码在 `backend/evaluate_model.py`，结果写到 `data/artifacts/model_evaluation.json`。

### 正样本定义（这是评估里最容易踩坑的地方）

正样本 = 培养方案里「**本学期 / 下学期 / 已修课程的直系后继课**」三类课程的资源中，该生尚未接触过的部分。

> 为什么不用「已学视频」当正样本？因为推荐链路**刻意排除已学视频**，两个集合天然不相交，Precision@5 会永远是 0。原实现就是这个口径，改掉之后指标才开始有区分度。

### 指标

| 指标 | 含义 |
| --- | --- |
| `precision@5` | 前 5 个推荐中命中「应学未学」资源的比例 |
| `ndcg@5` | 排序位置质量，命中越靠前得分越高 |
| `hit_rate@5` | 前 5 位至少命中一条的账号比例 |
| `recall_full` | 整条推荐链路对正样本池的覆盖率 |
| `course_coverage_rate` | 一次推荐覆盖本专业培养方案课程门数的比例 |
| `major_fit` | 推荐资源落在本专业培养方案内的比例 |
| `platform_diversity` | 推荐资源在 5 个平台上的分散度 |
| `catalog_coverage` | 账号群合计触达全库资源的比例 |

> `recall@5` 的分母是几百条正样本池，前 5 个位置最多命中 5 条，数值天然趋近于 0，没有展示价值，所以页面上换成了 `recall_full`。

### 留一法交叉验证

遮住账号的一条学习记录，重训后看能否召回。**这个数天然偏低**，因为已学资源按设计会被排到学习路径之外——它衡量的是「旧知识复现」而不是「路径推荐」。真正的路径质量看 `precision@5` / `ndcg@5` / `course_coverage_rate`。

### 当前结果（25 个专业账号，样本 3,624 条）

| 指标 | 数值 |
| --- | --- |
| 离线 AUC | 0.9995 |
| Precision@5 | 0.5520 |
| NDCG@5 | 0.6463 |
| HitRate@5 | 1.0000 |
| 培养方案课程覆盖率 | 0.9153（平均覆盖 32.9 门 / 36 门） |
| 本专业对齐度 | 1.0000 |
| Recall@全量 | 0.0842 |

## 真实数据采集与更新

`backend/live_collectors.py` 负责真实联网采集、时间过滤、去重入库、采集报告记录、自动重训和评估刷新。

目标约束：

- 视频教程：B站、MOOC、极客时间等平台各目标 50 门课程；只保留 6 年内上传或公开页面可确认为当前有效的课程。
- 招聘岗位：拉勾网 + BOSS直聘目标 200 条；只保留 2 年内发布或当前公开列表中的有效岗位。
- 考研科目：湖南所有研招单位，按目标专业查询最新年份招生目录/考试科目。
- 数据更新：每次采集都会写入 `collection_run` 和 `collector_state`，并刷新 `data/artifacts/live_collection_report.json`。

运行命令：

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild
python -m backend.live_collectors --video-per-platform 50 --job-total 200
```

前端入口：登录后进入「数据与模型」页面，点击「采集真实数据」。

合规说明：采集器只访问公开页面或公开接口，不绕过登录、验证码、付费墙和反爬限制。若目标站点拒绝访问或需要授权，采集报告会标记为未达标，并记录失败原因。

## 采集失败排障

如果所有 HTTPS 数据源同时出现 `sslkey.log` 权限错误，通常是本机外部代理设置了不可写的 `SSLKEYLOGFILE`。采集器启动时会自动清除该变量。

如果 B站出现 `HTTP 412`，采集器会使用正常的关键词 Referer/Origin、限速和瞬时失败恢复；如果公开接口最终仍拒绝访问，会记录为失败，不会伪造入库数量。

如果 MOOC、极客时间、拉勾或 BOSS 只返回空页面、TLS 超时或需要登录，报告会显示「未达到目标数量」及原因。这类情况需要使用平台授权接口、允许访问的公开导出文件或在合规网络环境执行，不能靠绕过风控解决。

SQLite 采集写入使用 30 秒等待；采集循环有总时限和连续失败熔断，避免单个站点异常拖住整次更新。
