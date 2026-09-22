# 学途 Lite · 边跑边学

一个把「学途 Lite」项目**完整拆开讲解**的可交互学习系统。
所有 SQL、后端代码、前端示例、推荐模型都能**当场运行看到结果**，并且每条知识点都写明了它在源码里的具体位置。

> 本目录是后加的教学模块，**没有修改主项目的任何一行代码**，也不改动 `backend/`、`frontend/`、`data/` 下的任何文件。

---

## 一、快速开始

需要先启动主项目（后端示例和推荐模型依赖它的数据接口）：

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild

# 1) 主项目（端口 8765）
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

再开一个终端启动教学服务：

```powershell
cd E:\practice-examples\competition\xuetu-lite-rebuild

# 2) 教学页面（端口 8766）
python learn/start.py
# 或者：python -m uvicorn server.app:app --app-dir learn --host 127.0.0.1 --port 8766
```

打开：**http://127.0.0.1:8766**

| 服务 | 端口 | 作用 |
|------|------|------|
| 主项目 | 8765 | 学途 Lite 正式服务（查看原版界面：http://127.0.0.1:8765） |
| 教学页面 | 8766 | 本模块 |

---

## 二、页面都有什么

左侧导航分两类内容：

### 知识点（第 0～4 章）
每条知识点都带：
- **归属标签** —— 前端 / 后端 / 数据库 / 推荐系统 / 整体架构
- **难度** —— 入门 / 进阶 / 实战
- **源码出处** —— `文件名:行号`，可以直接翻源码对照

| 章节 | 内容 |
|------|------|
| 第 0 章 | 先跑通闭环：一次请求从浏览器到数据库再回来的完整旅程 |
| 第 1 章 | 前端 10 个知识点，每个配一个可运行的 HTML 示例 |
| 第 2 章 | 后端 12 个知识点，每个都能在线填参数发请求 |
| 第 3 章 | 数据库 10 个知识点 + SQL 练习 + 后端代码练习 |
| 第 4 章 | 推荐系统 8 个知识点 + 7 个模型由易到难对比 |

### 实验区（三个）
1. **SQL 数据库实验室** —— 19 条预置 SQL + 自由编辑器 + 后端 Python 代码沙箱
2. **后端接口实验室** —— 主项目 14 个接口全部可在线调用
3. **推荐模型演练场** —— 7 个模型切换运行 + 横向指标对比

---

## 三、目录结构

```
learn/
├── README.md              本文件
├── start.py               一键启动脚本
├── _verify.py             全量自测脚本（验证所有示例能否跑通）
├── db_view.py             数据库只读查看器（命令行，看正式库用）
├── server/                教学服务后端
│   ├── app.py             FastAPI 主程序，端口 8766
│   ├── knowledge.py       知识点索引（归属标签 + 源码出处）
│   ├── db_sandbox.py      练习库副本 + SQL 白名单沙箱
│   ├── code_runner.py     Python 代码沙箱（AST 静态检查）
│   └── models_playground.py  7 个推荐模型，由易到难
├── docs/                  专题文档（Markdown 源文件）
│   └── 推荐系统算法与特征工程详解.md
├── tools/
│   └── md2html.py         Markdown → HTML 转换器（生成可浏览的文档页）
├── data/
│   └── learn_test.db      练习库副本（自动生成，可随时重置）
└── static/
    ├── index.html         页面骨架
    ├── learn.css          样式
    ├── learn.js           页面逻辑
    ├── docs/              文档的 HTML 版本（由 tools/md2html.py 生成）
    └── demos/             10 个可独立运行的前端示例
        ├── d01-dom.html        DOM 查询与 innerHTML
        ├── d02-fetch.html      fetch 调用后端接口
        ├── d03-template.html   模板字符串渲染卡片
        ├── d04-event.html      事件委托 + data-*
        ├── d05-form.html       FormData + 登录接口
        ├── d06-storage.html    sessionStorage + SPA 切换
        ├── d07-async.html      async/await 与 Promise.all
        ├── d08-cssgrid.html    Flex / Grid 布局
        ├── d09-cssvar.html     CSS 变量与动效
        └── d10-track.html      埋点闭环
```

每个 `demos/*.html` 都是**完整可独立运行**的 HTML，可以直接双击在浏览器打开，也可以从页面上点「在新窗口打开」。

---

## 三之二、专题文档

页面上的知识点是「卡片式」的，适合快速查阅定位。如果需要**成体系的深度讲解**，看 `learn/docs/`：

| 文档 | 内容 |
|------|------|
| `推荐系统算法与特征工程详解.md` | 推荐系统全链路拆解：5 路混合召回各自的条件与产出、8 维特征逐个的公式推导与数据来源表、逻辑回归的标准化与梯度下降细节、MMR 重排的权衡、模型真实权重解读与手算验证、7 个已知问题 |

这份文档的特点是**所有数值都来自真实运行结果**（模型权重、召回候选数、特征取值、AUC），不是理论推演。

Markdown 是源文件，直接改它就等于改文档。改完跑一下转换，生成方便阅读的 HTML：

```powershell
python learn/tools/md2html.py
```

输出到 `learn/static/docs/`，启动教学服务后可访问：

```
http://127.0.0.1:8766/static/docs/推荐系统算法与特征工程详解.html
```

也可以直接用浏览器打开 `learn/static/docs/` 下的 html 文件，不需要服务在跑。

---

## 四、安全设计（重要）

教学场景必须允许真跑代码，所以做了三层隔离：

1. **数据库隔离** —— 练习库 `learn/data/learn_test.db` 是正式库用 SQLite 官方 `backup()` 接口做的完整副本，结构与数据完全一致。所有增删改都发生在这份副本上，正式库全程以**只读模式**打开。页面上的「重置练习库」按钮可以随时重新复制，恢复原样。
2. **SQL 白名单** —— 只允许 `SELECT / INSERT / UPDATE / DELETE / REPLACE / WITH / EXPLAIN` 和几个查询元信息的 `PRAGMA`。`DROP / ALTER / ATTACH` 一律拦截。
3. **Python 沙箱** —— 代码执行前先过 AST 静态检查：
   - `import` 模块白名单（sqlite3 / json / math / re 等纯计算模块）
   - 禁用 `open` / `eval` / `exec` / `__import__` 等危险函数
   - 禁用 `__globals__` / `__builtins__` / `__class__` 等反射属性
   - `DB_PATH` 变量写死指向练习库，碰不到正式库
   - 8 秒超时保护

---

## 五、推荐模型分级

同一个任务（给某个学生推荐 9 门课），7 种方案：

| 级别 | 模型 | 用到什么 |
|------|------|----------|
| L1 | 热度榜 | 只按播放量排序，无个性化 |
| L2 | 评分榜 | 加一个质量维度 |
| L3 | 课程图谱规则 | 首次引入「你是谁」：在学/后继/预习 |
| L4 | TF-IDF 内容相似 | 文本转向量，解决新课冷启动 |
| L5 | ItemCF 协同过滤 | 看行为相似度，捕捉跨领域关联 |
| L6 | 逻辑回归排序 | 8 维特征自动学权重 |
| L7 | 完整推荐链路 | 多路召回 + LR + 多目标融合 + MMR 重排（项目现网方案） |

**评估口径与项目一致**：采用留一法（leave-one-out），把学生看过的课逐门遮住，看模型能否把它重新捞回前 5。
这与 `backend/evaluate_model.py:67` 的做法相同。之所以不能直接拿「已看过的课」当标准答案——那些课在推荐时本来就被排除了，命中率必然是 0。

实测效果（YY08 账号）：

| 模型 | Precision@5 | NDCG@5 | 平台多样性 |
|------|-------------|--------|------------|
| L1 热度榜 | 0.0 | 0.0 | 0.11 |
| L3 课程图谱 | 0.2 | 0.13 | 0.11 |
| L4/L5 内容/协同 | 0.4 | 0.23 | 0.11 |
| L6 逻辑回归 | 0.8 | 0.51 | 0.11 |
| L7 完整链路 | 0.8 | 0.50 | **0.22** |

注意最后一行：多样性翻倍、NDCG 微降 —— 这是推荐系统里经典的「精度 vs 多样性」权衡，MMR 重排故意压低重复内容来换取覆盖面。

---

## 六、自测

改动后可以跑一遍全量自测，确认所有示例仍然可用：

```powershell
python learn/_verify.py
```

脚本会逐条验证：
- 19 条预置 SQL（并且**每条执行两次**，确保幂等）
- 8 段预置 Python 代码（第二次执行不报错才算通过）
- 7 个推荐模型能否跑出结果
- 换 5 个不同学生，推荐结果是否真的有差异
- 沙箱能否拦住 `import os`、`open()`、`eval()`、`DROP TABLE` 等危险操作

最后输出 `总失败数：0` 才算全绿。

---

## 七、顺带发现并已修复的一处源码缺陷

分析源码时发现 `backend/train_model.py` 有一处真实问题：

```python
# 修复前 —— train_model.py:313-314
def main() -> None:
    bundle = train_rank_model()   # ← 这个函数不存在
```

文件里实际定义的函数名是 `train_fold_model`（第 245 行），两者对不上。
单独执行 `python -m backend.train_model` 会抛 `NameError`。

平时没暴露是因为：模型文件 `data/artifacts/ranker_model.json` 已存在且未过期，`bootstrap()` 不会走到重训分支（见 `app.py:43-44`）。

**已修复**（`train_model.py:313-325`）：

```python
def main() -> None:
    bundle = train_fold_model()          # ← 改成真实存在的函数名
    bundle.pop("_context", None)         # ← 顺手剔除训练中间态（见下）
    ...
```

除了改函数名，还加了一行 `pop("_context")`。原因：`train_fold_model()` 的返回值里带一个 `_context`
（全量 116 条视频 + 17 门课程的快照），它只是给留一法评估在内存里复用向量用的中间态
（`evaluate_model.py:122-131` 会自己重建一份），不属于模型产物。
不剔除的话模型文件会从 960 KB 涨到 1252 KB（**多出 281 KB / +30%**）。

修复后实测：`python -m backend.train_model` 正常输出 `sample_count=175, positive_rate=0.1429, auc=0.9475`，
且重新生成的 `ranker_model.json` 与修复前**逐字段完全一致**（ranker 权重、item_similarity、tfidf 全部相同），
推荐接口首条分数仍是 `0.6289` —— 说明这只是"让命令能跑通"，没有改变任何推荐效果。

（教学页面上第 2 章最后一条知识点专门列了这条，作为「如何定位隐藏 bug」的复盘素材。）
