/* ============================================================
 * learn 页面主逻辑
 *
 * 写法刻意和主项目 frontend/app.js 保持一致（SPA + innerHTML 重绘），
 * 这样学习者读这边的代码，顺便就把主项目的前端写法看熟了。
 *
 * 页面分两类内容：
 *   1. 知识点：从 /api/learn/knowledge 拉索引，渲染成带归属标签和源码出处的卡片
 *   2. 实验区：SQL 沙箱 / 后端接口测试 / 推荐模型演练场
 * ============================================================ */

const state = {
  chapters: [],
  layers: {},
  models: [],
  students: ["YY08", "YY09", "YY10", "YY11", "YY12"],
  view: "hero",          // hero | chapter | item | sql | api | model
  currentId: null,
  apiList: [],
};

// ---- 和主项目 app.js:16 一样的 DOM 快捷查询 ----
const $ = (s) => document.querySelector(s);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `请求失败 ${res.status}`);
  }
  return res.json();
}

// ---- 提示条，写法同主项目 app.js:28-34 ----
function toast(text) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = text;
  $("#toastBox").appendChild(node);
  setTimeout(() => node.remove(), 2200);
}

// HTML 转义：把服务端返回的内容安全地塞进 innerHTML
function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}


/* ============================================================
 * 启动
 * ============================================================ */
async function boot() {
  try {
    const data = await api("/api/learn/knowledge");
    state.chapters = data.chapters;
    state.layers = data.layers;
  } catch (e) {
    $("#content").innerHTML = `<div class="card">加载知识点失败：${esc(e.message)}</div>`;
    return;
  }

  try {
    const m = await api("/api/learn/models");
    state.models = m.models;
  } catch (e) {
    state.models = [];
  }

  renderNav();
  renderHero();
  checkMainStatus();

  // 视图路由：支持 hash（#sql）和查询参数（?view=sql）两种方式。
  // 查询参数是为了方便无头浏览器/截图工具，有些环境下 hash 传不进去。
  window.addEventListener("hashchange", routeFromHash);
  routeFromHash();
}

function currentViewKey() {
  if (location.hash.length > 1) return location.hash.slice(1);
  const q = new URLSearchParams(location.search);
  return q.get("view") || "";
}

function setHash(h) {
  if (location.hash !== h) history.replaceState(null, "", h);
}

function routeFromHash() {
  const v = currentViewKey();
  if (v === "sql") return renderSqlLab();
  if (v === "api") return renderApiLab();
  if (v === "model") return renderModelLab();
  if (v.startsWith("item-")) {
    const found = findItem(v.slice(5));
    if (found) return renderItem(found.item, found.chapter);
    return;
  }
  if (v.startsWith("chapter-")) {
    const ch = state.chapters.find((x) => x.id === v.slice(8));
    if (ch) renderChapter(ch);
  }
}


/* ============================================================
 * 主项目在线状态
 * ============================================================ */
async function checkMainStatus() {
  const chip = $("#mainStatus");
  try {
    const s = await api("/api/learn/status");
    if (s.main_online) {
      chip.className = "status-chip on";
      chip.textContent = "主项目已启动 8765 ✓";
    } else {
      chip.className = "status-chip off";
      chip.textContent = "主项目未启动（后端类示例会失败）";
    }
  } catch (e) {
    chip.className = "status-chip off";
    chip.textContent = "状态未知";
  }
}


/* ============================================================
 * 侧边导航
 * ============================================================ */
function renderNav() {
  const chaptersHtml = state.chapters.map((ch) => `
    <div class="nav-group">
      <div class="nav-chapter" data-chapter="${ch.id}">
        <span>${esc(ch.subtitle2 ? ch.subtitle2 : ch.title.replace(/^第 \d+ 章：/, ""))}</span>
        <span class="cnt">${ch.items.length}</span>
      </div>
      <div class="nav-items" id="navch-${ch.id}">
        ${ch.items.map((it) => `
          <div class="nav-item" data-item="${it.id}">${esc(it.title)}</div>
        `).join("")}
      </div>
    </div>
  `).join("");

  $("#nav").innerHTML = `${chaptersHtml}
    <div class="nav-lab">
      <div class="nav-lab-title">动手实验区</div>
      <div class="nav-item" data-lab="sql">SQL 数据库实验室</div>
      <div class="nav-item" data-lab="api">后端接口实验室</div>
      <div class="nav-item" data-lab="model">推荐模型演练场</div>
    </div>`;

  $("#nav").addEventListener("click", (e) => {
    const item = e.target.closest("[data-item]");
    if (item) {
      const found = findItem(item.dataset.item);
      if (found) renderItem(found.item, found.chapter);
      return;
    }
    const lab = e.target.closest("[data-lab]");
    if (lab) {
      const target = lab.dataset.lab;
      if (target === "sql") renderSqlLab();
      if (target === "api") renderApiLab();
      if (target === "model") renderModelLab();
    }
  });
}

function findItem(id) {
  for (const ch of state.chapters) {
    const it = ch.items.find((x) => x.id === id);
    if (it) return { item: it, chapter: ch };
  }
  return null;
}

function markActive(selector) {
  document.querySelectorAll(".nav-item, .nav-chapter").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll(selector).forEach((el) => el.classList.add("active"));
}


/* ============================================================
 * 首页
 * ============================================================ */
function renderHero() {
  state.view = "hero";
  // 注意：这里不能 setHash("#home") —— boot 流程是 renderHero() 先执行、
  // routeFromHash() 后执行，如果在这里改了 hash，会把 URL 里带来的
  // ?view=sql 之类的初始视图覆盖掉，路由就永远失效了。
  markActive("[data-chapter='ch0']");
  $("#content").innerHTML = `
    <div class="hero">
      <h1>把「学途 Lite」拆开来看</h1>
      <p>这是一个可交互的学习页面：左边选知识点，右边就能直接运行代码看结果。
         所有 SQL 和代码都跑在一份<b>数据库副本</b>上，随便怎么折腾都不会影响正在运行的项目。</p>
    </div>

    <div class="card">
      <div class="card-title">先看这条链路</div>
      <p class="card-summary">
        浏览器输入网址 → 前端 JS 发 fetch 请求 → 后端 FastAPI 找到对应函数 →
        执行 SQL 查 SQLite → 数据变成 JSON 返回 → 前端渲染成页面。
      </p>
      <div class="section-label">三个实验区，建议按这个顺序逛</div>
      <div class="row">
        <button class="btn" onclick="renderSqlLab()">① SQL 数据库实验室</button>
        <button class="btn secondary" onclick="renderApiLab()">② 后端接口实验室</button>
        <button class="btn green" onclick="renderModelLab()">③ 推荐模型演练场</button>
      </div>
      <p class="card-summary" style="margin-top:12px">
        也可以直接从左边导航按章节看知识点：每个知识点都标了
        <span class="badge" style="background:#6b46c1">归属</span>，
        并且写明了它在源码里的具体位置（文件名:行号）。
      </p>
    </div>

    <div class="card">
      <div class="card-title">目录</div>
      ${state.chapters.map((ch) => `
        <div style="padding:8px 0;border-bottom:1px dashed var(--line)">
          <div style="font-weight:600;cursor:pointer" data-chapter="${ch.id}">${esc(ch.title)}</div>
          <div style="color:var(--ink-2);font-size:13px">${esc(ch.subtitle)}</div>
        </div>
      `).join("")}
    </div>
  `;
}


/* ============================================================
 * 章节 + 知识点卡片
 * ============================================================ */
function renderChapter(chapter) {
  state.view = "chapter";
  setHash("#chapter-" + chapter.id);
  markActive(`[data-chapter='${chapter.id}']`);
  $("#content").innerHTML = `
    <h2 style="margin:0 0 4px">${esc(chapter.title)}</h2>
    <p style="color:var(--ink-2);margin:0 0 18px">${esc(chapter.subtitle)}</p>
    ${chapter.items.map((it) => knowledgeCard(it)).join("")}
  `;
}

function knowledgeCard(item) {
  const layer = state.layers[item.layer] || { name: item.layer, color: "#666" };
  return `
    <div class="card" id="kp-${item.id}">
      <div class="card-head">
        <span class="badge" style="background:${layer.color}">${esc(layer.name)}</span>
        <span class="badge level">${esc(item.level)}</span>
        <h3 class="card-title" style="margin:0">${esc(item.title)}</h3>
      </div>
      <p class="card-summary">${esc(item.summary)}</p>

      <div class="section-label">要点</div>
      <ul class="points">${(item.points || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>

      ${item.source ? `
        <div class="source"><b>源码出处：</b><code>${esc(item.source)}</code></div>
      ` : ""}

      ${item.demo ? `<div class="section-label">可运行示例</div>${demoBlock(item)}` : ""}
      ${item.api ? `<div class="section-label">接口测试</div>${apiBlock(item)}` : ""}
      ${item.sql_demo ? `<div class="section-label">SQL 练习</div>
          <div class="row"><button class="btn secondary" data-run-preset="${esc(item.sql_demo.id)}">打开这条 SQL 并运行</button></div>` : ""}
      ${item.code_demo ? `<div class="section-label">后端代码练习</div>
          <div class="row"><button class="btn secondary" data-run-code="${esc(item.code_demo.id)}">载入这段代码并运行</button></div>` : ""}
      ${item.model_key ? `<div class="section-label">推荐模型演示</div>
          <div class="row"><button class="btn green" data-run-model="${esc(item.model_key)}">运行对应模型看效果</button></div>` : ""}
      ${item.howto ? `<div class="source"><b>怎么练：</b>${esc(item.howto)}</div>` : ""}
    </div>
  `;
}

function renderItem(item, chapter) {
  state.view = "item";
  setHash("#item-" + item.id);
  markActive(`[data-item='${item.id}']`);
  $("#content").innerHTML = `
    <div style="margin-bottom:12px">
      <a class="ghost-btn" data-chapter="${chapter.id}">← 返回 ${esc(chapter.title)}</a>
    </div>
    ${knowledgeCard(item)}
  `;
}


/* ============================================================
 * 前端示例区块：iframe 运行 + 查看源码 + 复制
 * ============================================================ */
function demoBlock(item) {
  const file = item.demo;
  return `
    <div class="demo-tabs">
      <button class="demo-tab active" data-demo-tab="run-${item.id}">运行效果</button>
      <button class="demo-tab" data-demo-tab="src-${item.id}">查看源码</button>
    </div>
    <div id="tab-run-${item.id}">
      <iframe class="demo-frame" src="/static/demos/${esc(file)}"></iframe>
      <div class="row" style="margin-top:8px">
        <a class="btn warn" href="/static/demos/${esc(file)}" target="_blank">在新窗口打开 ↗</a>
        <button class="btn secondary" data-copy-demo="${esc(file)}">复制完整 HTML</button>
      </div>
    </div>
    <div id="tab-src-${item.id}" style="display:none">
      <pre class="out" id="srcbox-${item.id}">加载中...</pre>
    </div>
  `;
}

async function loadDemoSource(file, itemId) {
  try {
    const res = await fetch(`/api/learn/demo/${file}`);
    const text = await res.text();
    $(`#srcbox-${itemId}`).textContent = text;
  } catch (e) {
    $(`#srcbox-${itemId}`).textContent = "加载失败：" + e.message;
  }
}


/* ============================================================
 * 后端接口区块
 * ============================================================ */
function apiBlock(item) {
  const a = item.api;
  const params = a.params || [];
  const fields = params.length
    ? `<div class="param-grid">${params.map((p, i) => `
        <div class="param-field">
          <label>${esc(p.name)}</label>
          <input type="text" id="param-${item.id}-${i}" value="${esc(p.value)}" />
        </div>`).join("")}</div>`
    : `<div style="color:var(--ink-3);font-size:12.5px">该接口不需要额外参数</div>`;

  const bodyArea = a.body
    ? `<div class="section-label">请求体 JSON</div>
       <textarea class="code-input" id="body-${item.id}" style="min-height:90px">${esc(JSON.stringify(a.body, null, 2))}</textarea>`
    : "";

  return `
    <div class="api-item">
      <div class="api-head">
        <span class="api-method ${a.method}">${a.method}</span>
        <span class="api-path">${esc(a.path)}</span>
      </div>
      <div class="api-desc">${esc(a.desc)}</div>
      ${fields}
      ${bodyArea}
      <div class="row" style="margin-top:10px">
        <button class="btn" data-api-send="${item.id}">发送请求</button>
      </div>
      <pre class="out" id="apiout-${item.id}">点「发送请求」后，后端返回的原始数据会显示在这里</pre>
    </div>
  `;
}

async function sendApiCard(itemId) {
  // 从 knowledge 里找对应的 api 定义
  let apiDef = null;
  for (const ch of state.chapters) {
    const it = ch.items.find((x) => x.id === itemId);
    if (it && it.api) { apiDef = it.api; break; }
  }
  if (!apiDef) { toast("找不到接口定义"); return; }

  const out = $(`#apiout-${itemId}`);
  out.className = "out";
  out.textContent = "请求中...";

  const params = {};
  (apiDef.params || []).forEach((p, i) => {
    const el = $(`#param-${itemId}-${i}`);
    if (el && el.value.trim() !== "") params[p.name] = el.value.trim();
  });

  let body = null;
  const bodyEl = $(`#body-${itemId}`);
  if (bodyEl) {
    try { body = JSON.parse(bodyEl.value); }
    catch (e) { out.className = "out err"; out.textContent = "请求体不是合法 JSON：" + e.message; return; }
  }

  try {
    const res = await api("/api/learn/proxy", {
      method: "POST",
      body: JSON.stringify({ method: apiDef.method, path: apiDef.path, params, body }),
    });
    const show = JSON.stringify(res.body, null, 2);
    out.textContent = `HTTP ${res.status}\n\n${
      typeof res.body === "object" ? show.slice(0, 4000) : String(res.body).slice(0, 4000)
    }`;
    if (!res.ok) out.className = "out err";
  } catch (e) {
    out.className = "out err";
    out.textContent = "请求失败：" + e.message;
  }
}


/* ============================================================
 * ① SQL 实验室
 * ============================================================ */
const DEFAULT_SQL = `-- 在这里写 SQL，点「执行」看结果
-- 这是练习库的副本，随便增删改都不会影响正式项目
SELECT video_id, title, platform, popularity, rating
FROM video
ORDER BY popularity DESC
LIMIT 8;`;

async function renderSqlLab() {
  state.view = "sql";
  setHash("#sql");
  markActive("[data-lab='sql']");
  $("#content").innerHTML = `
    <h2 style="margin:0 0 4px">① SQL 数据库实验室</h2>
    <p style="color:var(--ink-2);margin:0 0 18px">
      练习库 <code>learn/data/learn_test.db</code> 是正式库的一份完整副本 ——
      表结构、索引、数据全都一样。你可以在上面放心执行 INSERT / UPDATE / DELETE，
      搞乱了点「重置练习库」就能一键还原。
    </p>

    <div class="card">
      <div class="card-head"><h3 class="card-title" style="margin:0">预置 SQL 练习</h3>
        <button class="btn warn" id="resetDbBtn">重置练习库</button>
        <button class="btn secondary" id="schemaBtn">查看全部表结构</button>
      </div>
      <div id="presetSqlBox" class="row">加载中...</div>
    </div>

    <div class="card">
      <div class="card-head"><h3 class="card-title" style="margin:0">自己写 SQL</h3></div>
      <textarea class="code-input" id="sqlInput">${esc(DEFAULT_SQL)}</textarea>
      <div class="row" style="margin-top:10px">
        <button class="btn" id="runSqlBtn">执行 (Ctrl+Enter)</button>
        <span style="color:var(--ink-3);font-size:12.5px">
          支持多条语句（用分号隔开）；DROP / ALTER 等破坏结构的语句会被拦截
        </span>
      </div>
      <div id="sqlResult"></div>
    </div>

    <div class="card" id="schemaCard" style="display:none">
      <div class="card-title">表结构</div>
      <div id="schemaBody">加载中...</div>
    </div>

    <div class="card">
      <div class="card-head">
        <h3 class="card-title" style="margin:0">自己写后端 Python 代码操作数据库</h3>
        <span class="badge" style="background:#0f9d58">后端</span>
      </div>
      <p class="card-summary">
        这里的代码会真的执行。变量 <code>DB_PATH</code> 已指向练习库，
        可以直接 <code>sqlite3.connect(DB_PATH)</code>；也可以直接用封装好的
        <code>connect_db()</code> 和 <code>rows()</code>（和项目里的写法一样）。
        危险模块（os / subprocess / socket）和 open / eval 都被沙箱拦掉了。
      </p>
      <div id="presetCodeBox" class="row">加载中...</div>
      <div class="section-label">代码编辑区</div>
      <textarea class="code-input" id="pyInput"># 写点什么试试，例如：
result = rows(connect_db(), "SELECT COUNT(*) AS c FROM video")
print("练习库共有", result[0]["c"], "门课程")</textarea>
      <div class="row" style="margin-top:10px">
        <button class="btn green" id="runPyBtn">运行代码 (Ctrl+Enter)</button>
        <span style="color:var(--ink-3);font-size:12.5px">
          超时 8 秒；把结果赋给 result 变量，就能在「返回值」区看到
        </span>
      </div>
      <div id="pyResult"></div>
    </div>
  `;

  loadPresetSql();
  loadPresetCode();
  bindSqlEvents();
  bindPyEvents();
}

async function loadPresetSql() {
  try {
    const r = await api("/api/learn/preset-sql");
    $("#presetSqlBox").innerHTML = r.items.map((it) => `
      <button class="btn ${it.level === "写入" ? "warn" : "secondary"}"
              data-preset-sql="${it.id}"
              title="${esc(it.source)}">
        ${esc(it.level)} · ${esc(it.title)}
      </button>
    `).join("");
  } catch (e) {
    $("#presetSqlBox").innerHTML = esc(e.message);
  }
}

function bindSqlEvents() {
  $("#runSqlBtn").addEventListener("click", runSql);
  $("#sqlInput").addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") runSql();
  });
  $("#resetDbBtn").addEventListener("click", async () => {
    $("#resetDbBtn").disabled = true;
    $("#resetDbBtn").textContent = "重置中...";
    try {
      await api("/api/learn/reset-db", { method: "POST" });
      toast("练习库已从正式库重新复制，恢复原样");
    } catch (e) { toast("重置失败：" + e.message); }
    $("#resetDbBtn").disabled = false;
    $("#resetDbBtn").textContent = "重置练习库";
  });
  $("#schemaBtn").addEventListener("click", async () => {
    const card = $("#schemaCard");
    card.style.display = card.style.display === "none" ? "block" : "none";
    if (card.style.display !== "none") loadSchema();
  });

  $("#presetSqlBox").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-preset-sql]");
    if (!btn) return;
    const id = btn.dataset.presetSql;
    const r = await api("/api/learn/preset-sql");
    const item = r.items.find((x) => x.id === id);
    if (!item) return;
    $("#sqlInput").value = item.sql;
    $("#sqlResult").innerHTML = `
      <div class="result-box" style="margin-top:10px">
        <div style="font-weight:600">${esc(item.title)}</div>
        <div style="color:var(--ink-2);font-size:13px">${esc(item.desc)}</div>
        <div class="source" style="margin-top:8px"><b>源码出处：</b><code>${esc(item.source)}</code></div>
      </div>`;
    await runSql();
  });
}

async function runSql() {
  const sql = $("#sqlInput").value;
  const box = $("#sqlResult");
  try {
    const r = await api("/api/learn/sql", { method: "POST", body: JSON.stringify({ sql }) });
    renderSqlResult(r, box);
  } catch (e) {
    box.innerHTML = `<pre class="out err">${esc(e.message)}</pre>`;
  }
}

function renderSqlResult(r, box) {
  let html = "";
  if (r.ok) {
    html += `<div class="result-box"><span class="result-ok">✓ 执行成功</span>
             　${esc(r.message)}　耗时 ${r.elapsed_ms}ms</div>`;
  } else {
    html += `<div class="result-box"><span class="result-err">✗ 执行失败</span>　${esc(r.message || r.error || "")}</div>`;
  }

  if (r.rows && r.rows.length) {
    const cols = r.columns || Object.keys(r.rows[0]);
    html += `<table class="grid"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
      ${r.rows.slice(0, 120).map((row) => `<tr>${cols.map((c) => {
        const v = row[c];
        return `<td class="${v === null ? "null" : ""}">${v === null ? "NULL" : esc(v)}</td>`;
      }).join("")}</tr>`).join("")}
    </tbody></table>`;
    if (r.rows.length > 120) html += `<div style="color:var(--ink-3);font-size:12.5px;margin-top:6px">
      仅显示前 120 行，共 ${r.rows.length} 行</div>`;
  }
  box.innerHTML = html;
}

async function loadSchema() {
  try {
    const r = await api("/api/learn/schema");
    $("#schemaBody").innerHTML = r.tables.map((t) => `
      <div style="margin-bottom:10px">
        <div><b>${esc(t.table)}</b> <span style="color:var(--ink-3)">${t.rows} 行</span></div>
        <div style="color:var(--ink-2);font-size:12.5px">${t.columns.map(esc).join(" · ")}</div>
      </div>`).join("");
  } catch (e) {
    $("#schemaBody").innerHTML = esc(e.message);
  }
}


/* ============================================================
 * ② 后端接口实验室
 * ============================================================ */
async function renderApiLab() {
  state.view = "api";
  setHash("#api");
  markActive("[data-lab='api']");
  $("#content").innerHTML = `
    <h2 style="margin:0 0 4px">② 后端接口实验室</h2>
    <p style="color:var(--ink-2);margin:0 0 18px">
      下面列出主项目全部后端接口。填参数、点发送，看后端真实返回的 JSON。
    </p>
    <div id="apiList">加载中...</div>
  `;

  try {
    const r = await api("/api/learn/apis");
    state.apiList = r.apis;
    $("#apiList").innerHTML = r.apis.map((a, idx) => apiIndexCard(a, idx)).join("");
    $("#apiList").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-api-index]");
      if (btn) sendApiIndex(Number(btn.dataset.apiIndex), btn.dataset.apiIndex);
    });
  } catch (e) {
    $("#apiList").innerHTML = `<div class="card">加载接口列表失败：${esc(e.message)}</div>`;
  }
}

function apiIndexCard(a, idx) {
  const params = a.params || [];
  const fields = params.length ? `<div class="param-grid">${params.map((p, i) => `
      <div class="param-field">
        <label>${esc(p.name)}</label>
        <input type="text" id="ap-${idx}-${i}" value="${esc(p.value)}" />
      </div>`).join("")}</div>` : "";
  const bodyArea = a.body ? `<div class="section-label">请求体 JSON</div>
      <textarea class="code-input" id="apb-${idx}" style="min-height:90px">${esc(JSON.stringify(a.body, null, 2))}</textarea>` : "";

  return `
    <div class="api-item">
      <div class="api-head">
        <span class="api-method ${a.method}">${a.method}</span>
        <span class="api-path">${esc(a.path)}</span>
        <button class="btn" data-api-index="${idx}">发送</button>
      </div>
      <div class="api-desc">${esc(a.desc)}</div>
      ${fields}${bodyArea}
      <div class="source" style="margin-top:10px"><b>源码出处：</b><code>${esc(a.source)}</code></div>
      <pre class="out" id="apiout-i-${idx}">点「发送」查看返回</pre>
    </div>`;
}

async function sendApiIndex(idx) {
  const a = state.apiList[idx];
  const out = $(`#apiout-i-${idx}`);
  out.className = "out";
  out.textContent = "请求中...";

  const params = {};
  (a.params || []).forEach((p, i) => {
    const el = $(`#ap-${idx}-${i}`);
    if (el && el.value.trim() !== "") params[p.name] = el.value.trim();
  });

  let body = null;
  const bodyEl = $(`#apb-${idx}`);
  if (bodyEl) {
    try { body = JSON.parse(bodyEl.value); }
    catch (e) { out.className = "out err"; out.textContent = "请求体 JSON 不合法：" + e.message; return; }
  }

  try {
    const res = await api("/api/learn/proxy", {
      method: "POST",
      body: JSON.stringify({ method: a.method, path: a.path, params, body }),
    });
    const text = typeof res.body === "object"
      ? JSON.stringify(res.body, null, 2)
      : String(res.body);
    out.textContent = `HTTP ${res.status}\n\n${text.slice(0, 4000)}`;
    if (!res.ok) out.className = "out err";
  } catch (e) {
    out.className = "out err";
    out.textContent = "请求失败：" + e.message;
  }
}


/* ============================================================
 * ③ 推荐模型演练场
 * ============================================================ */
async function renderModelLab() {
  state.view = "model";
  setHash("#model");
  markActive("[data-lab='model']");
  $("#content").innerHTML = `
    <h2 style="margin:0 0 4px">③ 推荐模型演练场</h2>
    <p style="color:var(--ink-2);margin:0 0 18px">
      同一个任务 —— 给一个学生推荐 9 门课 —— 用从简单到复杂的 7 种方案各做一遍。
      换模型、换学生，对比它们的推荐结果和指标。
    </p>

    <div class="card">
      <div class="card-head">
        <h3 class="card-title" style="margin:0">选择模型与学生</h3>
      </div>
      <div class="row">
        <select id="modelSelect">
          ${state.models.map((m) => `<option value="${m.key}">${esc(m.name)} —— ${esc(m.desc)}</option>`).join("")}
        </select>
        <select id="studentSelect">
          ${state.students.map((s) => `<option value="${s}">学生 ${s}</option>`).join("")}
        </select>
        <button class="btn green" id="runModelBtn">运行这个模型</button>
        <button class="btn secondary" id="compareBtn">7 个模型横向对比</button>
      </div>
      <div id="modelResult"></div>
    </div>

    <div class="card" id="compareCard" style="display:none">
      <div class="card-title">全部模型指标对比</div>
      <p class="card-summary" style="font-size:13px">
        命中率用<b>留一法</b>计算：把学生看过的课逐门遮住，看模型能否把它重新捞回前 5。
        这和项目里 <code>backend/evaluate_model.py</code> 的评估口径一致。
      </p>
      <div id="compareBody">加载中...</div>
    </div>
  `;

  $("#runModelBtn").addEventListener("click", runSelectedModel);

  // 支持 ?view=model&run=full&sid=YY08 直接运行某个模型（方便分享链接）
  const params = new URLSearchParams(location.search);
  if (params.get("run")) {
    $("#modelSelect").value = params.get("run");
    if (params.get("sid")) $("#studentSelect").value = params.get("sid");
    runSelectedModel();
  }
  if (params.get("compare") === "1") {
    $("#compareBtn").click();
  }
  $("#compareBtn").addEventListener("click", async () => {
    const card = $("#compareCard");
    card.style.display = "block";
    $("#compareBody").innerHTML = `
      <div class="loading">正在跑 7 个模型 × 5 折留一法评估，约需几秒...</div>`;
    card.scrollIntoView({ behavior: "smooth" });
    try {
      const r = await api(`/api/learn/model/compare?student_id=${$("#studentSelect").value}`);
      renderCompare(r.rows);
    } catch (e) {
      $("#compareBody").innerHTML = `<pre class="out err">${esc(e.message)}</pre>`;
    }
  });
}

async function runSelectedModel() {
  const key = $("#modelSelect").value;
  const sid = $("#studentSelect").value;
  const box = $("#modelResult");
  box.innerHTML = `<div class="loading">模型运行中...</div>`;
  try {
    const r = await api(`/api/learn/model/run?key=${key}&student_id=${sid}&topn=9`);
    renderModelResult(r);
  } catch (e) {
    box.innerHTML = `<pre class="out err">${esc(e.message)}</pre>`;
  }
}

function renderModelResult(r) {
  const m = r.metrics || {};
  const metrics = [
    ["Precision@5", m["precision@5"]],
    ["Recall@5", m["recall@5"]],
    ["HitRate@5", m["hit_rate@5"]],
    ["NDCG@5", m["ndcg@5"]],
    ["课程覆盖率", m.coverage],
    ["平台多样性", m.diversity],
  ];

  const ex = r.explain || {};
  const extra = r.extra || {};

  let extraHtml = "";
  if (extra.weights) {
    extraHtml += `<div class="section-label">模型学到的特征权重</div>
      <pre class="out">${esc(Object.entries(extra.weights)
        .map(([k, v]) => `${k.padEnd(26)} ${String(v).padStart(8)}`)
        .join("\n"))}\n${"bias".padEnd(26)} ${String(extra.bias).padStart(8)}</pre>`;
  }
  if (extra.train_auc !== undefined) {
    const trainInfo = extra.sample_count !== undefined
      ? `　样本 ${extra.sample_count} 条　正样本率 ${extra.positive_rate}`
      : "　（复用模型 6 的训练结果）";
    extraHtml += `<div class="result-box">训练集 AUC：<b>${extra.train_auc}</b>${trainInfo}</div>`;
  }
  if (extra.pipeline) {
    extraHtml += `<div class="result-box">流水线：${extra.pipeline.map(esc).join(" → ")}　（召回候选 ${extra.recall_count} 个）</div>`;
  }
  if (extra.vocab_size) {
    extraHtml += `<div class="result-box">词表大小：${extra.vocab_size}，向量维度等于词表大小（稀疏表示）</div>`;
  }

  const folds = (m.folds || []).slice(0, 8);
  let foldsHtml = "";
  if (folds.length) {
    foldsHtml = `<div class="section-label">留一法逐折结果</div>
      <table class="grid"><thead><tr><th>被遮住的课</th><th>排在第几位</th><th>前5命中</th></tr></thead><tbody>
      ${folds.map((f) => `
        <tr>
          <td>${esc(f.held_title || f.held_out)}</td>
          <td>${f.rank ? "第 " + f.rank + " 位" : "未进前 9"}</td>
          <td style="color:${f.top5_hit ? "var(--green)" : "var(--red)"}">${f.top5_hit ? "命中 ✓" : "未命中 ✗"}</td>
        </tr>`).join("")}
      </tbody></table>`;
  }

  $("#modelResult").innerHTML = `
    <div class="row" style="margin-top:14px">
      <span class="badge" style="background:#c5221f">${esc(r.tag || "")}</span>
      <span class="badge level">难度 ${r.level}/7</span>
    </div>

    <div class="metric-grid">
      ${metrics.map(([name, val]) => `
        <div class="metric"><span>${name}</span><strong>${val === undefined ? "--" : val}</strong></div>
      `).join("")}
    </div>

    <div class="explain-box">
      <dl>
        <dt>核心思路</dt><dd>${esc(ex.idea || "")}</dd>
        <dt>打分公司</dt><dd><code>${esc(ex.formula || "")}</code></dd>
        <dt>优点</dt><dd>${esc(ex.pros || "")}</dd>
        <dt>局限</dt><dd>${esc(ex.cons || "")}</dd>
        ${ex.tradeoff ? `<dt>关键权衡</dt><dd>${esc(ex.tradeoff)}</dd>` : ""}
        <dt>源码出处</dt><dd><code>${esc(ex.source || "")}</code></dd>
      </dl>
    </div>
    ${extraHtml}

    <div class="section-label">推荐结果 Top ${r.items.length}</div>
    <div class="rec-list">
      ${r.items.map((it, i) => `
        <div class="rec-item">
          <div class="rec-rank">${i + 1}</div>
          <div class="rec-body">
            <div class="rec-title">${esc(it.title)}</div>
            <div class="rec-meta">${esc(it.platform)} · ${esc(it.plays)} · ${it.is_paid ? "付费" : "免费"}</div>
            <div class="rec-reason">${esc(it.reason || "")}</div>
            <div class="rec-score">综合分 ${it.score}${it.ctr !== undefined ? ` · CTR ${it.ctr}` : ""}</div>
          </div>
        </div>`).join("")}
    </div>
    ${foldsHtml}
  `;
}

function renderCompare(rows) {
  const keys = ["precision@5", "recall@5", "hit_rate@5", "ndcg@5", "coverage", "diversity"];
  const labels = {
    "precision@5": "Precision@5", "recall@5": "Recall@5", "hit_rate@5": "HitRate@5",
    "ndcg@5": "NDCG@5", coverage: "课程覆盖率", diversity: "平台多样性",
  };
  // 每列的最优值高亮
  const best = {};
  keys.forEach((k) => {
    const vals = rows.map((r) => (r.metrics || {})[k]).filter((v) => typeof v === "number");
    best[k] = vals.length ? Math.max(...vals) : null;
  });

  $("#compareBody").innerHTML = `
    <table class="grid">
      <thead><tr>
        <th>模型</th>${keys.map((k) => `<th>${labels[k]}</th>`).join("")}<th>Top1 推荐</th>
      </tr></thead>
      <tbody>
        ${rows.map((r) => `
          <tr>
            <td><b>L${r.level}</b> ${esc(r.name)}<br /><span style="color:var(--ink-3);font-size:11.5px">${esc(r.tag)}</span></td>
            ${keys.map((k) => {
              const v = (r.metrics || {})[k];
              const isBest = best[k] !== null && v === best[k] && v > 0;
              return `<td class="${v === undefined ? "null" : ""}" style="${isBest ? "color:var(--green);font-weight:700" : ""}">
                ${v === undefined ? "--" : v}${isBest ? " ★" : ""}</td>`;
            }).join("")}
            <td style="font-size:12px">${esc(r.top1 || "")}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    <p style="color:var(--ink-2);font-size:13px;margin-top:12px">
      ★ 标记该指标的最优值。可以清楚看到：从热度榜到逻辑回归，
      Precision@5 一路走高；而最后一步 MMR 重排牺牲了一点 NDCG，换来了明显更高的多样性。
    </p>`;
}


/* ============================================================
 * 全局事件委托
 * ============================================================ */
document.addEventListener("click", (e) => {
  // 章节跳转
  const chBtn = e.target.closest("[data-chapter]");
  if (chBtn) {
    const ch = state.chapters.find((c) => c.id === chBtn.dataset.chapter);
    if (ch) { renderChapter(ch); window.scrollTo(0, 0); }
    return;
  }

  // demo 标签切换
  const tab = e.target.closest("[data-demo-tab]");
  if (tab) {
    const val = tab.dataset.demoTab;
    const idx = val.lastIndexOf("-");
    const mode = val.slice(0, idx);
    const itemId = val.slice(idx + 1);
    $(`#tab-run-${itemId}`).style.display = mode === "run" ? "block" : "none";
    $(`#tab-src-${itemId}`).style.display = mode === "src" ? "block" : "none";
    document.querySelectorAll(`[data-demo-tab$="-${itemId}"]`).forEach((b) => b.classList.remove("active"));
    tab.classList.add("active");
    if (mode === "src") loadDemoSource(tab.closest(".card").querySelector("[data-copy-demo]").dataset.copyDemo, itemId);
    return;
  }

  // 复制 demo 源码
  const copyBtn = e.target.closest("[data-copy-demo]");
  if (copyBtn) {
    fetch(`/api/learn/demo/${copyBtn.dataset.copyDemo}`)
      .then((r) => r.text())
      .then((t) => copyToClipboard(t))
      .catch(() => toast("复制失败"));
    return;
  }

  // 知识点里的「运行方法」按钮
  const runPreset = e.target.closest("[data-run-preset]");
  if (runPreset) {
    renderSqlLab();
    setTimeout(() => {
      const target = document.querySelector(`[data-preset-sql="${runPreset.dataset.runPreset}"]`);
      if (target) { target.click(); window.scrollTo(0, 0); }
    }, 400);
    return;
  }
  const runCode = e.target.closest("[data-run-code]");
  if (runCode) {
    renderSqlLab();
    setTimeout(() => {
      const target = document.querySelector(`[data-preset-code="${runCode.dataset.runCode}"]`);
      if (target) { target.click(); window.scrollTo(0, 0); }
    }, 400);
    return;
  }
  const runModel = e.target.closest("[data-run-model]");
  if (runModel) {
    renderModelLab();
    setTimeout(() => {
      $("#modelSelect").value = runModel.dataset.runModel;
      runSelectedModel();
      window.scrollTo(0, 0);
    }, 300);
    return;
  }

  // 卡片内的接口发送（知识点里的 api 区块）
  const sendBtn = e.target.closest("[data-api-send]");
  if (sendBtn) { sendApiCard(sendBtn.dataset.apiSend); return; }
});

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => toast("已复制到剪贴板"));
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    toast("已复制到剪贴板");
  }
}


/* ============================================================
 * ④ Python 后端代码实验室
 * ============================================================ */
async function loadPresetCode() {
  try {
    const r = await api("/api/learn/preset-code");
    $("#presetCodeBox").innerHTML = r.items.map((it) => `
      <button class="btn secondary" data-preset-code="${it.id}" title="${esc(it.source)}">
        ${esc(it.level)} · ${esc(it.title)}
      </button>`).join("");
  } catch (e) {
    $("#presetCodeBox").innerHTML = esc(e.message);
  }
}

function bindPyEvents() {
  $("#runPyBtn").addEventListener("click", runPython);
  $("#pyInput").addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") runPython();
  });
  $("#presetCodeBox").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-preset-code]");
    if (!btn) return;
    const r = await api("/api/learn/preset-code");
    const item = r.items.find((x) => x.id === btn.dataset.presetCode);
    if (!item) return;
    $("#pyInput").value = item.code;
    $("#pyResult").innerHTML = `
      <div class="result-box" style="margin-top:10px">
        <div style="font-weight:600">${esc(item.title)}</div>
        <div style="color:var(--ink-2);font-size:13px">${esc(item.desc)}</div>
        <div class="source" style="margin-top:8px"><b>源码出处：</b><code>${esc(item.source)}</code></div>
      </div>`;
    await runPython();
  });
}

async function runPython() {
  const code = $("#pyInput").value;
  const box = $("#pyResult");
  try {
    const r = await api("/api/learn/run-code", { method: "POST", body: JSON.stringify({ code }) });
    renderPyResult(r, box);
  } catch (e) {
    box.innerHTML += `<pre class="out err">${esc(e.message)}</pre>`;
  }
}

function renderPyResult(r, box) {
  let html = "";
  if (r.ok) {
    html += `<div class="result-box"><span class="result-ok">✓ 运行成功</span>　耗时 ${r.elapsed_ms}ms</div>`;
  } else {
    html += `<div class="result-box"><span class="result-err">✗ ${esc(r.stage)}</span>　${esc(r.error)}</div>`;
  }
  if (r.stdout) {
    html += `<div class="section-label">print 输出</div><pre class="out">${esc(r.stdout)}</pre>`;
  }
  if (r.ok && r.result !== null && r.result !== undefined) {
    html += `<div class="section-label">返回值 result</div>
             <pre class="out">${esc(JSON.stringify(r.result, null, 2))}</pre>`;
  }
  box.innerHTML += html;
}


boot();
