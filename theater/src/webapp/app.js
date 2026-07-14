/* 昼青集 · 读诗剧场 —— 前端（无依赖）。
 * 数据从 /api/state 一次拉全；作者动作经 /api/action（服务端写前自动备份）。
 * 所有榜单在此处从盲读记录事后派生，绝无 LLM 排名。
 */
"use strict";

const app = document.getElementById("app");
let S = null; // {poems, reads, personas}
let maps = {};

/* ---------- 工具 ---------- */

const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

const fmt1 = x => (Math.round(x * 10) / 10).toFixed(1);

function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2400);
}

async function post(path, body) {
  const res = await fetch(path, { method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.status);
  return data;
}

async function loadState() {
  const res = await fetch("/api/state");
  S = await res.json();
  S.curation = S.curation || {};
  S.favs = S.favs || {};
  S.stanzas = S.stanzas || {};
  S.calibration = S.calibration || {};
  maps.poem = new Map(S.poems.map(p => [p.id, p]));
  maps._primary = null;
  maps.persona = new Map(S.personas.map(p => [p.persona_id, p]));
  maps.readsByPoem = new Map();
  maps.readById = new Map();
  for (const r of S.reads) {
    maps.readById.set(r.read_id, r);
    if (!maps.readsByPoem.has(r.poem_id)) maps.readsByPoem.set(r.poem_id, []);
    maps.readsByPoem.get(r.poem_id).push(r);
  }
}

function blindReads(poemId) {
  return (maps.readsByPoem.get(poemId) || []).filter(r => r.context_mode === "blind");
}

/* 模型名显示层归并：reads.jsonl 永不改（铁律），只在展示与统计时合并同门异名。
 * 规则：去掉日期后缀、统一小写、点号写法归一。 */
function modelAlias(m) {
  if (!m) return "未知";
  let x = String(m).toLowerCase().replace(/-\d{8}$/, "");
  if (x === "claude-3-5-sonnet") x = "claude-3.5-sonnet";
  // 规范归并表（calibrate.py 随 scores.json 下发）：gemini 碎片归 2.5-pro 等，
  // 展示层与校准口径永远同一套合并规则
  const canon = (S && S.calibration && S.calibration.meta && S.calibration.meta.aliases) || {};
  return canon[x] || x;
}

function annotatedReads(poemId) {
  return (maps.readsByPoem.get(poemId) || []).filter(r => r.context_mode === "annotated");
}

function isHidden(readId) {
  return !!(S.curation[readId] && S.curation[readId].hidden);
}

function isFav(poemId) { return !!S.favs[poemId]; }
const favMark = id => isFav(id) ? '<span class="fav-mark" title="作者偏爱">♥</span>' : "";

/* 多作者：主作者（占多数者）在列表里不标注，只有他人作品才挂作者章，避免满屏重复 */
function primaryAuthor() {
  if (maps._primary != null) return maps._primary;
  const c = new Map();
  for (const p of S.poems) c.set(p.author, (c.get(p.author) || 0) + 1);
  maps._primary = [...c.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
  return maps._primary;
}
const authorChip = p => (p.author && p.author !== primaryAuthor())
  ? `<span class="chip" title="作者">${esc(p.author)}</span>` : "";

/* 统计与分布只用未被作者折叠的记录；折叠的仍保留在历史里 */
function statReads(poemId) {
  return blindReads(poemId).filter(r => !isHidden(r.read_id));
}

function stats(poemId) {
  const rs = statReads(poemId);
  const c = (S.calibration.poems || {})[poemId];
  // 质分优先取 display（方差匹配拉伸后的展示分），旧文件只有 cal 时回退；都没有回退均分
  const cal = c ? (c.display != null ? c.display : c.cal) : null;
  if (!rs.length) return { n: 0, mean: null, sd: null, cal };
  const scores = rs.map(r => r.score);
  const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
  const sd = Math.sqrt(scores.reduce((a, b) => a + (b - mean) ** 2, 0) / scores.length);
  return { n: scores.length, mean, sd, cal };
}

function personaName(pid) {
  const p = maps.persona.get(pid);
  return p ? p.name : pid;
}

function whenOf(p) { return p.date_written || (p.created || "").slice(0, 7); }
function yearOf(p) { return (p.date_written || p.created || "").slice(0, 4); }
function firstLine(p) {
  return (p.content.split("\n").find(l => l.trim()) || "").trim();
}
function pool() {
  return S.poems.filter(p => p.visibility === "public" && p.ai_read);
}

/* 最近评论：默认只展开一小页，可一直往下翻（批量跑完后作者要在这里扫一遍） */
const RECENT_MAX = 300, RECENT_FIRST = 10, RECENT_STEP = 30;
let recentShown = RECENT_FIRST;

function recentReads() {
  const blind = S.reads.filter(r => r.context_mode === "blind");
  blind.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
  return blind.slice(0, RECENT_MAX);
}

function fmtTs(ts) {
  if (!ts) return "";
  const d = ts.slice(0, 10); // YYYY-MM-DD
  const t = ts.slice(11, 16); // HH:MM
  const today = new Date().toISOString().slice(0, 10);
  if (d === today) return t;
  return d.slice(5) + " " + t; // MM-DD HH:MM
}

function recentRow(r) {
  const p = maps.poem.get(r.poem_id);
  const title = p ? p.title : r.poem_id;
  const persona = maps.persona.get(r.reader.persona_id);
  const pname = persona ? persona.name : r.reader.persona_id;
  const model = modelAlias(r.reader.model).replace(/^claude-/, "C:").replace(/^deepseek/, "DS:").replace(/^gemini/, "G:");
  return `<div class="recent-row">
    <div class="rmeta">
      <span class="rtime">${fmtTs(r.ts)}</span>
      <span class="rpoem"><a href="#/poem/${r.poem_id}/reads">《${esc(title)}》</a></span>
      <span class="rname"><a href="#/reader/${esc(r.reader.persona_id)}">${esc(pname)}</a></span>
      <span class="chip">${esc(model)}</span>
      ${r.long_form ? `<a class="deep-link" href="#/read/${r.read_id}">深读 →</a>` : ""}
    </div>
    <div class="rbody">
      <span class="rscore">${fmt1(r.score)}</span>
      <div class="rtext">${esc(r.reaction || "")}</div>
    </div>
  </div>`;
}

function renderRecentInto() {
  const listEl = document.getElementById("recent-list");
  const moreEl = document.getElementById("recent-more");
  if (!listEl) return;
  const items = recentReads();
  if (!items.length) {
    listEl.innerHTML = '<div class="empty">暂无盲读记录</div>';
    moreEl.innerHTML = "";
    return;
  }
  listEl.innerHTML = items.slice(0, recentShown).map(recentRow).join("\n");
  const left = items.length - Math.min(recentShown, items.length);
  moreEl.innerHTML = left > 0
    ? `<button class="btn" id="recent-more-btn">再展开 ${Math.min(RECENT_STEP, left)} 条（还有 ${left} 条）</button>`
    : (items.length > RECENT_FIRST ? '<button class="btn" id="recent-fold-btn">收起</button>' : "");
  const mb = document.getElementById("recent-more-btn");
  if (mb) mb.onclick = () => { recentShown += RECENT_STEP; renderRecentInto(); };
  const fb = document.getElementById("recent-fold-btn");
  if (fb) fb.onclick = () => {
    recentShown = RECENT_FIRST;
    renderRecentInto();
    const sec = document.getElementById("recent-reads");
    if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
  };
}

/* ---------- 路由 ---------- */

window.addEventListener("hashchange", route);

async function route() {
  if (!S) await loadState();
  const h = location.hash.replace(/^#/, "") || "/";
  const seg = h.split("/").filter(Boolean);
  window.scrollTo(0, 0);
  if (seg.length === 0) return renderBoards();
  if (seg[0] === "all") return renderAll();
  if (seg[0] === "timeline") return renderTimeline();
  if (seg[0] === "stats") return renderStats();
  if (seg[0] === "readers") return renderReaders();
  if (seg[0] === "poem" && seg[1]) return renderPoem(seg[1], seg[2] === "reads");
  if (seg[0] === "read" && seg[1]) return renderDeepRead(seg[1]);
  if (seg[0] === "board" && seg[1]) return renderBoardFull(seg[1]);
  if (seg[0] === "reader" && seg[1]) return renderReader(seg[1]);
  renderBoards();
}

/* ---------- 榜单页 ---------- */

function boardList(items, metaFn, goReads) {
  if (!items.length) return `<p class="empty">还没有足够的阅读记录。跟 agent 说一声「跑一轮」。</p>`;
  const suffix = goReads ? "/reads" : "";
  return "<ol>" + items.map(p => `<li>
      <span class="t"><a href="#/poem/${p.id}${suffix}">${esc(p.title)}</a>${authorChip(p)}</span>
      <span class="m">${metaFn(p)}</span></li>`).join("") + "</ol>";
}

const poemSize = p => p.content.replace(/\s/g, "").length;
const fmt2 = x => x.toFixed(2);
/* 质 = 校准分（跨模型/人设松紧归一后的最终分，排序用它）；均 = 原始均分（参考） */
const qm = s => s.cal != null ? `质 ${fmt2(s.cal)} · 均 ${fmt1(s.mean)}` : `均 ${fmt1(s.mean)}`;
const sMeta = p => { const s = stats(p.id); return `${qm(s)} · ${s.n} 读`; };
const dMeta = p => { const s = stats(p.id); return `σ ${fmt1(s.sd)} · ${qm(s)} · ${s.n} 读`; };
const rMeta = p => { const s = stats(p.id); return s.n ? `${qm(s)} · ${s.n} 读` : "未读"; };
const zMeta = p => `${poemSize(p)} 字`;

/* 榜单的唯一定义处：预览（前 10）与完整榜共用，保证两边永远一致 */
function boardDefs() {
  const ps = pool();
  const key = s => s.cal != null ? s.cal : s.mean;   // 有校准分按校准分排，没有回退均分
  const byScore = arr => arr
    .map(p => ({ p, s: stats(p.id) }))
    .filter(x => x.s.n >= 1)
    .sort((a, b) => key(b.s) - key(a.s) || b.s.n - a.s.n)
    .map(x => x.p);

  const sorted = [...ps].sort((a, b) => poemSize(b) - poemSize(a));
  const third = Math.max(1, Math.floor(sorted.length / 3));
  const ciPool = ps.filter(p => p.genre === "词");
  const ciRated = byScore(ciPool);

  return {
    hero: { title: "招牌榜", note: "众读者盲读校准分最高（每首至少 3 次阅读；质 = 按各读者松紧归一后的分，均 = 原始均分）", meta: sMeta,
      items: ps.map(p => ({ p, s: stats(p.id) })).filter(x => x.s.n >= 3)
        .sort((a, b) => key(b.s) - key(a.s)).map(x => x.p) },
    scores: { title: "完整打分榜", note: "所有被读过的作品，按校准分排（含仅 1–2 读的，读数少的被拉向全局均值）", meta: sMeta,
      items: byScore(ps) },
    polar: { title: "最两极榜", note: "把读者劈成两半、方差最大的诗——多义、危险、可能最好（至少 4 次阅读）", meta: dMeta,
      items: ps.map(p => ({ p, s: stats(p.id) })).filter(x => x.s.n >= 4)
        .sort((a, b) => b.s.sd - a.s.sd).map(x => x.p) },
    ci: { title: "诗词榜", note: "词作按盲读均分排（未读的排后）", meta: rMeta,
      items: ciRated.concat(ciPool.filter(p => !ciRated.includes(p))) },
    long: { title: "长诗榜", note: "篇幅前 1/3 里按盲读均分排", meta: sMeta,
      items: byScore(sorted.slice(0, third)) },
    short: { title: "短诗榜", note: "篇幅后 1/3 里按盲读均分排", meta: sMeta,
      items: byScore(sorted.slice(-third)) },
    favs: { title: "作者偏爱", note: "作者亲手标记「我觉得好」的诗（按标记时间）", meta: rMeta,
      items: ps.filter(p => isFav(p.id))
        .sort((a, b) => (S.favs[b.id].ts || "").localeCompare(S.favs[a.id].ts || "")) },
    longest: { title: "最长", note: "按字数", meta: zMeta, items: sorted },
    shortest: { title: "最短", note: "按字数", meta: zMeta, items: [...sorted].reverse() },
  };
}

/* 读者的手：每个人设给分的偏好统计 */
function readerRanking() {
  const rows = [];
  for (const persona of S.personas) {
    if (persona.superseded_by) continue;
    const rs = S.reads.filter(r => r.context_mode === "blind" &&
      r.reader.persona_id === persona.persona_id && !isHidden(r.read_id));
    if (!rs.length) { rows.push({ persona, n: 0 }); continue; }
    const scores = rs.map(r => r.score);
    const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
    const sd = Math.sqrt(scores.reduce((a, b) => a + (b - mean) ** 2, 0) / scores.length);
    rows.push({ persona, n: scores.length, mean, sd,
      min: Math.min(...scores), max: Math.max(...scores), reads: rs });
  }
  return rows.sort((a, b) => (b.mean ?? -1) - (a.mean ?? -1) || b.n - a.n);
}

function readerBoardHTML(rows, limit) {
  const list = limit ? rows.filter(r => r.n > 0).slice(0, limit) : rows;
  if (!list.length) return `<p class="empty">还没有阅读记录。</p>`;
  return "<ol>" + list.map(r => `<li>
      <span class="t"><a href="#/reader/${r.persona.persona_id}">${esc(r.persona.name)}</a></span>
      <span class="m">${r.n ? `均给 ${fmt1(r.mean)} · ${r.n} 读` : "未上场"}</span></li>`).join("") + "</ol>";
}

function boardSection(key, defs, cls) {
  const d = defs[key];
  return `<section class="board ${cls || ""}">
    <h2>${d.title}<a class="full-link" href="#/board/${key}">完整 →</a></h2>
    <p class="board-note">${d.note}</p>
    ${boardList(d.items.slice(0, 10), d.meta, true)}</section>`;
}

function renderBoards() {
  app.className = "wide";
  const defs = boardDefs();
  const readers = readerRanking();
  app.innerHTML = `
    <h1 class="page-title">榜单</h1>
    <p class="page-hint">全部从诚实的单篇盲读里事后派生；从不让模型排名。评分只是浅层信号，形状在每首诗自己的页面里。
      · <a href="#/board/scores">完整打分榜 →</a></p>
    <div class="boards">
      ${boardSection("hero", defs, "hero")}
      ${boardSection("polar", defs)}
      ${boardSection("ci", defs)}
      ${boardSection("long", defs)}
      ${boardSection("short", defs)}
      ${boardSection("favs", defs)}
      <section class="board"><h2>读者的手<a class="full-link" href="#/board/readers">完整 →</a></h2>
        <p class="board-note">每位读者给分的松紧（点名字看其打分偏好与全部评语）</p>
        ${readerBoardHTML(readers, 10)}</section>
      <details class="board more-boards"><summary>更多榜单 · 字数</summary>
        <div class="boards" style="margin-top:1.2rem">
          <section><h2>最长<a class="full-link" href="#/board/longest">完整 →</a></h2>${boardList(defs.longest.items.slice(0, 10), zMeta, true)}</section>
          <section><h2>最短<a class="full-link" href="#/board/shortest">完整 →</a></h2>${boardList(defs.shortest.items.slice(0, 10), zMeta, true)}</section>
        </div>
      </details>
    </div>
    <section class="board hero" id="recent-reads" style="margin-top:2.5rem">
      <h2>最近的评论 <span style="font-weight:400;font-size:.75rem;color:var(--ink-3);letter-spacing:.08em">${recentReads().length ? '缓存最近 ' + recentReads().length + ' 条' : ''}</span></h2>
      <p class="board-note">盲读按时间倒序，最新的在最前。点诗名到评论区，点读者名看这双眼睛。</p>
      <div class="recent-list" id="recent-list"></div>
      <div class="recent-more" id="recent-more"></div>
    </section>`;
  renderRecentInto();
}

function renderBoardFull(key) {
  app.className = "wide";
  if (key === "readers") {
    const rows = readerRanking();
    app.innerHTML = `
      <p><a class="back" href="#/">← 榜单</a></p>
      <h1 class="page-title">读者的手</h1>
      <p class="page-hint">每位读者给分的均值与松紧；点名字进读者页看其偏好与全部评语。</p>
      <div class="board">${readerBoardHTML(rows)}</div>`;
    return;
  }
  const d = boardDefs()[key];
  if (!d) { renderBoards(); return; }
  app.innerHTML = `
    <p><a class="back" href="#/">← 榜单</a></p>
    <h1 class="page-title">${d.title} · 完整</h1>
    <p class="page-hint">${d.note} · 共 ${d.items.length} 首</p>
    <div class="board">${boardList(d.items, d.meta, true)}</div>`;
}

/* ---------- 读者索引：人设卡片墙 ---------- */

function renderReaders() {
  app.className = "wide";
  const rows = readerRanking();
  const active = rows.filter(r => r.n > 0).sort((a, b) => b.n - a.n);
  const idle = rows.filter(r => !r.n);
  const card = r => {
    const ps = r.persona;
    return `<a class="reader-card${r.n ? "" : " idle"}" href="#/reader/${esc(ps.persona_id)}">
      <div class="rc-name">${esc(ps.name)}</div>
      <div class="rc-chips">
        <span class="chip">${esc(ps.generation || "")}</span>
        <span class="chip">${esc(ps.orientation || "")}</span>
        ${ps["knows_诠释"] ? '<span class="chip accent">知情</span>' : ""}
        ${ps["knows_date"] ? '<span class="chip accent">知时</span>' : ""}
      </div>
      <div class="rc-desc">${esc(ps.persona)}</div>
      <div class="rc-stats">${r.n
        ? `读过 ${r.n} 首 · 均给 ${fmt1(r.mean)} · σ ${fmt1(r.sd)} · ${fmt1(r.min)}–${fmt1(r.max)}`
        : "还没有上场"}</div>
    </a>`;
  };
  app.innerHTML = `
    <h1 class="page-title">读者</h1>
    <p class="page-hint">读诗剧场的全部眼睛，共 ${rows.length} 位。点进任何一位，看这双眼睛的松紧、分布与全部评语。</p>
    <div class="reader-cards">${active.map(card).join("")}</div>
    ${idle.length ? `<h2 style="margin:2.4rem 0 1.1rem;font-size:1.05rem;letter-spacing:.1em;color:var(--ink-2)">候场</h2>
      <div class="reader-cards">${idle.map(card).join("")}</div>` : ""}`;
}

/* 幕后演员：按模型（显示层归并）统计给分手势，图式同读者松紧。
 * reads 由调用方给：统计页传全体盲读（minN=30），读者页传该人设的读数
 * （minN=1，看每个扮演者的手势）。始终原始分——看的就是行为本身。 */
function modelChart(el, reads, minN, compact) {
  const byM = new Map();
  for (const r of reads) {
    const m = modelAlias(r.reader.model);
    if (!byM.has(m)) byM.set(m, []);
    byM.get(m).push(r.score);
  }
  const rows = [...byM.entries()].filter(([, ss]) => ss.length >= minN).map(([m, ss]) => {
    const mean = ss.reduce((a, b) => a + b, 0) / ss.length;
    const sd = Math.sqrt(ss.reduce((a, b) => a + (b - mean) ** 2, 0) / ss.length);
    return { m, n: ss.length, mean, sd, lo: quant(ss, .05), hi: quant(ss, .95),
      p8: ss.filter(s => s >= 8).length / ss.length };
  }).sort((a, b) => b.mean - a.mean);
  if (!rows.length) { el.innerHTML = `<p class="empty">还没有 ≥${minN} 读的模型。</p>`; return; }
  // compact：窄页（读者页 .dist-wrap 上限 36em）用更小的 viewBox，等比放大后字才够看
  const W = compact ? 450 : 680, rowH = 26, padL = compact ? 135 : 178,
    padR = compact ? 100 : 128, axisH = 26, padT = 8;
  const H = padT + rows.length * rowH + axisH;
  const x = s => padL + (s / 10) * (W - padL - padR);
  const baseY = padT + rows.length * rowH;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="模型手势">`;
  for (let t = 0; t <= 10; t += 2)
    svg += `<line x1="${x(t)}" y1="${padT}" x2="${x(t)}" y2="${baseY}" stroke="#e3dac7" stroke-dasharray="2 5"/>` +
      `<text x="${x(t)}" y="${baseY + 16}" text-anchor="middle" font-size="11" fill="#a29786">${t}</text>`;
  rows.forEach((r, i) => {
    const cy = padT + i * rowH + rowH / 2;
    svg += `<text x="${padL - 10}" y="${cy + 4}" text-anchor="end" font-size="11" fill="#6b6154">${esc(r.m)}<tspan fill="#a29786">（${r.n}）</tspan></text>` +
      `<line x1="${x(r.lo)}" y1="${cy}" x2="${x(r.hi)}" y2="${cy}" stroke="#2f6d62" stroke-width="1.2" opacity=".5"/>` +
      `<line x1="${x(Math.max(0, r.mean - r.sd))}" y1="${cy}" x2="${x(Math.min(10, r.mean + r.sd))}" y2="${cy}" stroke="#2f6d62" stroke-width="4" opacity=".35"/>` +
      `<circle cx="${x(r.mean)}" cy="${cy}" r="4" fill="#2f6d62"><title>${esc(r.m)} · 均 ${fmt1(r.mean)} · σ ${fmt1(r.sd)} · ${r.n} 读</title></circle>` +
      `<text x="${W - 6}" y="${cy + 4}" text-anchor="end" font-size="10.5" fill="#6b6154"><tspan font-weight="600">${fmt1(r.mean)}</tspan><tspan fill="#a29786"> ±${fmt1(r.sd)} · ≥8: ${Math.round(r.p8 * 100)}%</tspan></text>`;
  });
  el.innerHTML = svg + "</svg>";
}

/* ---------- 读者页：一个人设的打分偏好与全部评语 ---------- */

function renderReader(pid) {
  app.className = "";
  const persona = maps.persona.get(pid);
  if (!persona) { app.innerHTML = `<p class="page-hint">没有这位读者。</p>`; return; }
  const row = readerRanking().find(r => r.persona.persona_id === pid);
  const rs = (row && row.reads || []).slice().sort((a, b) => b.score - a.score);

  app.innerHTML = `
    <p><a class="back" href="#/board/readers">← 读者的手</a></p>
    <h1 class="page-title">${esc(persona.name)}</h1>
    <p class="page-hint">
      <span class="chip">${esc(persona.generation || "")}</span>
      <span class="chip">${esc(persona.orientation || "")}</span>
      ${persona["knows_诠释"] ? '<span class="chip accent">知情</span>' : ""}
      ${persona["knows_date"] ? '<span class="chip accent">知时</span>' : ""}
    </p>
    <blockquote class="persona-desc">${esc(persona.persona)}</blockquote>
    ${row && row.n ? `
      <p class="page-hint" style="margin-top:1.5rem">读过 ${row.n} 首 · 均给 ${fmt1(row.mean)} · σ ${fmt1(row.sd)} · 最低 ${fmt1(row.min)} / 最高 ${fmt1(row.max)}</p>
      <div class="dist-wrap" id="dist"></div>
      <h2 style="margin:2.2rem 0 .3rem;font-size:1.05rem;letter-spacing:.1em;color:var(--ink-2)">扮演者</h2>
      <p class="page-hint" style="margin-bottom:.8rem">这双眼睛由哪些模型扮演过、各自操控时的手势（原始分）</p>
      <div class="dist-wrap" id="actor-chart"></div>
      <div id="cards">${rs.map(r => readerReadRow(r)).join("")}</div>`
      : `<p class="page-hint" style="margin-top:1.5rem">这位读者还没有上场。</p>`}`;

  if (row && row.n) {
    renderDist(document.getElementById("dist"), rs, null);
    modelChart(document.getElementById("actor-chart"), rs, 1, true);
  }
}

function readerReadRow(r) {
  const p = maps.poem.get(r.poem_id);
  const stale = p && r.content_hash !== p.content_hash;
  return `<div class="read-card" id="card-${r.read_id}">
    <div class="rc-head">
      <span class="rname"><a href="#/poem/${r.poem_id}/reads">《${esc(p ? p.title : r.poem_id)}》</a></span>
      <span class="chip">${esc(modelAlias(r.reader.model))}</span>
      ${stale ? '<span class="chip warm">读的是旧版</span>' : ""}
      <span class="score-badge">${fmt1(r.score)}</span>
    </div>
    <div class="reaction">${esc(r.reaction)}</div>
    ${r.long_form ? `<a class="deep-link" href="#/read/${r.read_id}">深读全文 →</a>` : ""}
  </div>`;
}

/* ---------- 全部作品 ---------- */

let allFilter = { genre: "全部", showPrivate: false };

function renderAll() {
  app.className = "wide";
  const genres = ["全部", ...new Set(S.poems.map(p => p.genre))];
  let list = S.poems.filter(p => allFilter.genre === "全部" || p.genre === allFilter.genre);
  if (!allFilter.showPrivate) list = list.filter(p => p.visibility === "public");

  app.innerHTML = `
    <h1 class="page-title">全部作品</h1>
    <p class="page-hint">共 ${S.poems.length} 部；当前显示 ${list.length} 部。</p>
    <div class="filter-row">
      ${genres.map(g => `<button class="btn g ${g === allFilter.genre ? "on" : ""}" data-g="${esc(g)}">${esc(g)}</button>`).join("")}
      <button class="btn pv ${allFilter.showPrivate ? "on" : ""}">含私密</button>
    </div>
    <div>${list.map(p => {
      const s = stats(p.id);
      return `<div class="poem-row">
        <span class="id">${p.id}</span>
        <span class="t"><a href="#/poem/${p.id}">${esc(p.title)}</a>${favMark(p.id)}${authorChip(p)}
          ${p.visibility === "private" ? '<span class="chip warm">私密</span>' : ""}
          ${!p.ai_read ? '<span class="chip">存档</span>' : ""}</span>
        <span class="meta">${esc(p.genre)} · ${yearOf(p)}${s.n ? ` · ${s.n} 读 · 均 ${fmt1(s.mean)}` : ""}</span>
      </div>`; }).join("")}</div>`;

  app.querySelectorAll(".btn.g").forEach(b => b.onclick = () => { allFilter.genre = b.dataset.g; renderAll(); });
  app.querySelector(".btn.pv").onclick = () => { allFilter.showPrivate = !allFilter.showPrivate; renderAll(); };
}

/* ---------- 时间轴 ---------- */

function renderTimeline() {
  app.className = "";
  const sorted = [...S.poems].sort((a, b) =>
    (a.date_written || a.created).localeCompare(b.date_written || b.created));
  const byYear = new Map();
  for (const p of sorted) {
    const y = yearOf(p) || "无日期";
    if (!byYear.has(y)) byYear.set(y, []);
    byYear.get(y).push(p);
  }
  app.innerHTML = `
    <h1 class="page-title">时间轴</h1>
    <p class="page-hint">按写作时间（无则备忘录创建时间）。顺着这些年滚下来看。</p>
    ${[...byYear.entries()].map(([y, ps]) => `
      <section class="year-block"><h2>${esc(y)}</h2>
        ${ps.map(p => { const s = stats(p.id); return `<div class="tl-row">
          <span class="mon">${(p.date_written || p.created).slice(5, 7)}月</span>
          <span class="t"><a href="#/poem/${p.id}">${esc(p.title)}</a>${favMark(p.id)}${authorChip(p)}
            ${p.visibility === "private" ? '<span class="chip warm">私密</span>' : ""}
            ${s.n ? `<span class="chip" title="${s.n} 次盲读">${s.cal != null ? `质 ${fmt2(s.cal)}` : `均 ${fmt1(s.mean)}`}</span>` : ""}</span>
          <span class="first-line">${esc(firstLine(p))}</span>
        </div>`; }).join("")}
      </section>`).join("")}`;
}

/* ---------- 诗详情页 ---------- */

function renderPoemBody(content, poemId) {
  /* 作者手工分段（侧车）优先；没有则退回启发式 */
  const breaks = poemId && S.stanzas[poemId];
  if (Array.isArray(breaks)) {
    const lines = content.split("\n").filter(l => l.trim());
    const bset = new Set(breaks);
    const out = [];
    lines.forEach((ln, i) => {
      out.push(`<p>${esc(ln)}</p>`);
      if (bset.has(i) && i < lines.length - 1) out.push('<p class="gap"></p>');
    });
    return out.join("\n");
  }
  /* 无侧车覆盖时，老实信任数据：任何空行都算真分段。
     曾经在这里猜"是否整体双倍行距"，但双倍行距噪音（导出把每行都
     多算一个空行）和真分段在数据里长得一模一样，猜不出来，猜错了
     还会悄悄吞掉真分段（见 NOTES.md 分段恢复记录）。现有语料已核实/
     清洗过，不再需要这层猜测；以后新批次务必在导入时就把空行语义
     提取对，而不是指望这里补救。 */
  const lines = content.split("\n");
  const out = [];
  let blankRun = 0;
  for (const ln of lines) {
    if (!ln.trim()) { blankRun++; continue; }
    if (out.length && blankRun >= 1) out.push('<p class="gap"></p>');
    blankRun = 0;
    out.push(`<p>${esc(ln)}</p>`);
  }
  return out.join("\n");
}

function renderPoem(id, goReads) {
  app.className = "";
  const p = maps.poem.get(id);
  if (!p) { app.innerHTML = `<p class="page-hint">没有这首诗。</p>`; return; }
  const all = blindReads(id).slice().sort((a, b) => a.ts.localeCompare(b.ts));
  const rs = all.filter(r => !isHidden(r.read_id));
  const hidden = all.filter(r => isHidden(r.read_id));
  const ann = annotatedReads(id).slice().sort((a, b) => a.ts.localeCompare(b.ts));
  const st = stats(id);

  app.innerHTML = `
    <article>
      <header class="poem-head">
        <h1>${esc(p.title)}${isFav(p.id) ? ' <span class="fav-mark">♥</span>' : ""}</h1>
        <div class="meta">
          <span class="chip">${esc(p.genre)}</span>
          <span>写于 ${esc(whenOf(p))}</span> · <span>${p.id}</span>
          ${p.visibility === "private" ? '<span class="chip warm">私密 · 不进读者池</span>' : ""}
        </div>
        <div class="author-tools">
          <button class="btn" id="btn-vis">${p.visibility === "public" ? "设为私密" : "设为公开"}</button>
          <button class="btn" id="btn-edit">编辑</button>
          <button class="btn" id="btn-bg">背景小注</button>
          <button class="btn" id="btn-date">写作时间</button>
          <button class="btn" id="btn-genre">文体</button>
          <button class="btn ${isFav(p.id) ? "faved" : ""}" id="btn-fav">${isFav(p.id) ? "♥ 已偏爱" : "♡ 我觉得好"}</button>
        </div>
        <div id="tool-panel"></div>
      </header>
      <div class="poem-body" id="poem-body">${renderPoemBody(p.content, p.id)}</div>
      ${p.background ? `<div class="bg-note">背景小注（读者可见）：${esc(p.background)}</div>` : ""}
      ${p.note ? `<details class="self-note"><summary>自注 · 仅作者可见</summary>
          <div class="note-text">${esc(p.note)}</div></details>` : ""}
      <section class="reads-zone">
        <h2 style="font-size:1.2rem">众　目${st.n ? `<span style="color:#a4593d;margin-left:.55em;letter-spacing:.04em" title="${st.cal != null ? "校准分（按各读者松紧归一）" : "原始均分"}">${st.cal != null ? fmt2(st.cal) : fmt1(st.mean)}</span>` : ""}</h2>
        <div style="position:relative;text-align:center;font-size:.75rem;color:var(--ink-3);letter-spacing:.14em;margin:-.9rem 0 1.8rem">
          ${st.n ? `${st.n} 次观看${st.n > 1 ? ` · σ ${fmt1(st.sd)}` : ""}${st.cal != null ? ` · 原始均 ${fmt1(st.mean)}` : ""}` : "虚位以待"}
          <span style="position:absolute;right:0;top:0">作者 · ${esc(p.author || "未署名")}</span>
        </div>
        <div class="dist-wrap" id="dist"></div>
        <div id="cards">${rs.map(r => readCard(r, p)).join("") ||
          '<p class="page-hint" style="text-align:center">还没有人读过这首诗。跟 agent 说一声「跑一轮」。</p>'}</div>
        ${hidden.length ? `<details class="hidden-reads"><summary>已折叠 ${hidden.length} 条（不计入分布与榜单）</summary>
          ${hidden.map(r => readCard(r, p)).join("")}</details>` : ""}
        ${ann.length ? `<h2 style="font-size:1.05rem;margin-top:2.8rem">批注本读者</h2>
        <div style="text-align:center;font-size:.75rem;color:var(--ink-3);letter-spacing:.14em;margin:-.4rem 0 1.4rem">读的是带作者眉批的版本 · 分数只作参照，不入众目与榜单</div>
        ${ann.map(r => readCard(r, p)).join("")}` : ""}
      </section>
    </article>`;

  if (rs.length) renderDist(document.getElementById("dist"), rs, p);
  wirePoemTools(p);
  wireCutNote(p);
  wireCuration(p);
  if (goReads) {
    setTimeout(() => {
      const z = document.querySelector(".reads-zone");
      if (z) z.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 350);
  }
}

function readCard(r, p) {
  const stale = r.content_hash !== p.content_hash;
  const hid = isHidden(r.read_id);
  return `<div class="read-card${hid ? " dim" : ""}" id="card-${r.read_id}">
    <div class="rc-head">
      <span class="rname"><a href="#/reader/${esc(r.reader.persona_id)}">${esc(personaName(r.reader.persona_id))}</a></span>
      <span class="chip">${esc(modelAlias(r.reader.model))}</span>
      ${r.reader["knows_诠释"] ? '<span class="chip accent">知情</span>' : ""}
      ${r.reader["knows_date"] ? '<span class="chip accent">知时</span>' : ""}
      ${stale ? '<span class="chip warm">读的是旧版</span>' : ""}
      <span class="score-badge">${fmt1(r.score)}</span>
    </div>
    <div class="reaction">${esc(r.reaction)}</div>
    <div class="rc-foot">
      ${r.long_form ? `<a class="deep-link" href="#/read/${r.read_id}">深读全文 →</a>` : "<span></span>"}
      <button class="curate-btn" data-rid="${r.read_id}" data-hide="${hid ? 0 : 1}">${hid ? "恢复此评" : "折叠此评"}</button>
    </div>
  </div>`;
}

function wireCuration(p) {
  app.querySelectorAll(".curate-btn").forEach(b => b.onclick = async () => {
    try {
      await post("/api/curate", { read_id: b.dataset.rid, hidden: b.dataset.hide === "1" });
      await loadState();
      renderPoem(p.id, false);
      toast(b.dataset.hide === "1" ? "已折叠（不再计入分布与榜单，随时可恢复）" : "已恢复");
    } catch (e) { toast("失败：" + e.message); }
  });
}

/* ---------- 反应分布图（SVG 点状直方图，单系列） ---------- */

function renderDist(el, reads, poem) {
  const W = 640, padL = 24, padR = 24, axisH = 30;
  const x = s => padL + (s / 10) * (W - padL - padR);
  const bins = new Map();
  const dots = [];
  for (const r of reads) {
    const b = Math.round(r.score * 2) / 2;
    const k = bins.get(b) || 0;
    bins.set(b, k + 1);
    dots.push({ r, b, level: k });
  }
  const maxStack = Math.max(...bins.values());
  // 自适应：点多时压缩堆叠间距，让图高不超过 ~380px（大 n 时成密度柱）
  const step = Math.max(3.2, Math.min(13, 330 / maxStack));
  const dotR = Math.max(2.2, Math.min(5, step / 2 + 1));
  const H = 26 + maxStack * step + axisH;
  const baseY = H - axisH;
  const mean = reads.reduce((a, r) => a + r.score, 0) / reads.length;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="评分分布图">`;
  svg += `<line x1="${padL - 6}" y1="${baseY}" x2="${W - padR + 6}" y2="${baseY}" stroke="#e3dac7" stroke-width="1"/>`;
  for (let t = 0; t <= 10; t++) {
    svg += `<line x1="${x(t)}" y1="${baseY}" x2="${x(t)}" y2="${baseY + 4}" stroke="#e3dac7"/>` +
      `<text x="${x(t)}" y="${baseY + 18}" text-anchor="middle" font-size="11" fill="#a29786">${t}</text>`;
  }
  svg += `<line x1="${x(mean)}" y1="10" x2="${x(mean)}" y2="${baseY}" stroke="#a4593d" stroke-width="1" stroke-dasharray="3 4" opacity=".7"/>` +
    `<text x="${x(mean) + 6}" y="18" font-size="11" fill="#a4593d">均 ${fmt1(mean)}</text>`;
  let hasStale = false;
  for (const d of dots) {
    const dp = poem || maps.poem.get(d.r.poem_id);
    const stale = dp ? d.r.content_hash !== dp.content_hash : false;
    if (stale) hasStale = true;
    const fillAttr = stale
      ? `fill="#f6f1e7" stroke="#2f6d62" stroke-width="1.6"`
      : `fill="#2f6d62" fill-opacity=".82" stroke="#f6f1e7" stroke-width="1.5"`;
    svg += `<circle class="dot" data-rid="${d.r.read_id}" cx="${x(d.b)}" cy="${baseY - 8 - d.level * step}" r="${dotR}"
      ${fillAttr} style="cursor:pointer"/>`;
  }
  svg += "</svg>";
  el.innerHTML = svg + `<div class="dist-caption">一点即一次盲读${hasStale ? " · 空心点 = 读的是旧版" : ""} · 悬停看是谁 · 点击跳到那条短评</div>`;

  let tip = document.querySelector(".tip");
  if (!tip) { tip = document.createElement("div"); tip.className = "tip"; document.body.appendChild(tip); }
  el.querySelectorAll(".dot").forEach(c => {
    c.addEventListener("mousemove", e => {
      const r = maps.readById.get(c.dataset.rid);
      const label = poem ? personaName(r.reader.persona_id)
        : `《${(maps.poem.get(r.poem_id) || {}).title || r.poem_id}》`;
      tip.innerHTML = `<b>${esc(label)}</b> · ${fmt1(r.score)}<br>${esc(r.reaction.slice(0, 60))}${r.reaction.length > 60 ? "…" : ""}`;
      tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 260) + "px";
      tip.style.top = (e.clientY + 14) + "px";
      tip.classList.add("show");
    });
    c.addEventListener("mouseleave", () => tip.classList.remove("show"));
    c.addEventListener("click", () => {
      const card = document.getElementById("card-" + c.dataset.rid);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        document.querySelectorAll(".read-card.hl").forEach(x => x.classList.remove("hl"));
        card.classList.add("hl");
      }
    });
  });
}

/* ---------- 作者工具 ---------- */

async function doAction(body, okMsg) {
  try {
    await post("/api/action", body);
    await loadState();
    route();
    toast(okMsg);
  } catch (e) { toast("失败：" + e.message); }
}

function wirePoemTools(p) {
  const panel = document.getElementById("tool-panel");
  document.getElementById("btn-vis").onclick = () => {
    const v = p.visibility === "public" ? "private" : "public";
    doAction({ id: p.id, action: "set_visibility", value: v },
      v === "private" ? "已设为私密，立即退出读者池" : "已公开");
  };
  document.getElementById("btn-bg").onclick = () => {
    panel.innerHTML = `<div style="margin-top:1rem">
      <textarea id="bg-in" rows="3" style="width:100%;font-family:inherit;font-size:.9rem;padding:.6em;border:1px solid var(--line);border-radius:8px;background:var(--panel)"
        placeholder="只给读者看的背景小注（写于何时/何境）">${esc(p.background)}</textarea>
      <div style="margin-top:.5rem"><button class="btn primary" id="bg-save">保存</button></div></div>`;
    document.getElementById("bg-save").onclick = () =>
      doAction({ id: p.id, action: "set_background",
        value: document.getElementById("bg-in").value.trim() }, "背景小注已保存");
  };
  document.getElementById("btn-fav").onclick = async () => {
    try {
      await post("/api/favorite", { poem_id: p.id, value: !isFav(p.id) });
      await loadState();
      renderPoem(p.id, false);
      toast(isFav(p.id) ? "已标记「我觉得好」，进作者偏爱榜" : "已取消偏爱标记");
    } catch (e) { toast("失败：" + e.message); }
  };
  document.getElementById("btn-genre").onclick = () => {
    const presets = ["现代诗", "词", "歌词", "杂文", "草稿"];
    panel.innerHTML = `<div style="margin-top:1rem">
      <div style="display:flex;gap:.5em;justify-content:center;flex-wrap:wrap;margin-bottom:.6rem">
        ${presets.map(g => `<button class="btn genre-pick ${g === p.genre ? "on" : ""}" data-g="${g}">${g}</button>`).join("")}
      </div>
      <input id="genre-in" value="${esc(p.genre)}" placeholder="或自定义文体"
        style="font-family:inherit;font-size:.9rem;padding:.5em .8em;border:1px solid var(--line);border-radius:8px;background:var(--panel)">
      <button class="btn primary" id="genre-save">保存</button>
      <div style="font-size:.75rem;color:var(--ink-3);margin-top:.4rem">设为「杂文」或「草稿」将退出读者池（不再被 AI 读）；其余文体自动进入读者池。</div></div>`;
    panel.querySelectorAll(".genre-pick").forEach(b => b.onclick = () => {
      document.getElementById("genre-in").value = b.dataset.g;
    });
    document.getElementById("genre-save").onclick = () => {
      const v = document.getElementById("genre-in").value.trim();
      doAction({ id: p.id, action: "set_genre", value: v },
        `文体已改为「${v}」` + (["杂文", "草稿"].includes(v) ? "，已退出读者池" : ""));
    };
  };
  document.getElementById("btn-edit").onclick = () => renderEditMode(p);
  document.getElementById("btn-date").onclick = () => {
    panel.innerHTML = `<div style="margin-top:1rem">
      <input id="date-in" value="${esc(p.date_written || "")}" placeholder="如 2022-06（留空 = 未标明）"
        style="font-family:inherit;font-size:.9rem;padding:.5em .8em;border:1px solid var(--line);border-radius:8px;background:var(--panel)">
      <button class="btn primary" id="date-save">保存</button></div>`;
    document.getElementById("date-save").onclick = () =>
      doAction({ id: p.id, action: "set_date_written",
        value: document.getElementById("date-in").value.trim() }, "写作时间已标注");
  };
}

/* ---------- 统一编辑（标题 + 正文一个入口） ----------
 * 正文按"当前显示效果"编辑：手工分段侧车先物化成空行再进编辑框，所见即
 * 所得。保存时正文没动 → 只走改标题（不动 content_hash，不触发旧版）；
 * 正文动了 → content_hash 更新，旧读数按 hash 自动标"旧版"（保留不删），
 * 该诗的分段侧车并入正文空行（服务端丢弃旧侧车，避免行号错位覆盖）。 */

function effectiveContent(p) {
  const breaks = S.stanzas[p.id];
  if (!Array.isArray(breaks)) return p.content;
  const lines = p.content.split("\n").filter(l => l.trim());
  const bset = new Set(breaks);
  const out = [];
  lines.forEach((ln, i) => {
    out.push(ln);
    if (bset.has(i) && i < lines.length - 1) out.push("");
  });
  return out.join("\n");
}

function renderEditMode(p) {
  const body = document.getElementById("poem-body");
  const base = effectiveContent(p);
  const inputCss = "font-family:inherit;font-size:.95rem;padding:.5em .8em;border:1px solid var(--line);border-radius:8px;background:var(--panel);width:100%;box-sizing:border-box";
  body.innerHTML = `
    <div style="text-align:left">
      <input id="edit-title" value="${esc(p.title)}" placeholder="标题" style="${inputCss};font-weight:600">
      <div style="display:flex;gap:.5em;flex-wrap:wrap;margin:.5rem 0">
        <button class="btn" id="edit-up" title="把正文第一行剪切为标题（矫正导出时首行被吞进正文的诗）">首行升为标题</button>
        <button class="btn" id="edit-down" title="把标题插入为正文首行，标题改为「无题」（矫正导出时首行被抬成标题的诗）">标题沉为首行</button>
        <button class="btn" id="edit-stanza" title="只调空行分段（存侧车），不改原文、不触发旧版">仅调分段</button>
      </div>
      <textarea id="edit-content" rows="${Math.min(40, base.split("\n").length + 3)}" style="${inputCss};line-height:1.9;resize:vertical">${esc(base)}</textarea>
      <div style="font-size:.75rem;color:var(--ink-3);margin-top:.5rem;line-height:1.7">空行即分段。正文有改动时保存：content_hash 更新，已有读数标「旧版」（保留不删），手工分段侧车并入正文空行；只改标题不触发旧版。写前自动备份到 corpus/.backups/。</div>
      <div style="margin-top:.7rem;display:flex;gap:.6em">
        <button class="btn primary" id="edit-save">保存</button>
        <button class="btn" id="edit-cancel">取消</button>
      </div>
    </div>`;
  const titleIn = document.getElementById("edit-title");
  const ta = document.getElementById("edit-content");
  document.getElementById("edit-cancel").onclick = () => renderPoem(p.id, false);
  document.getElementById("edit-stanza").onclick = () => renderStanzaEditor(p);
  document.getElementById("edit-up").onclick = () => {
    const lines = ta.value.split("\n");
    const i = lines.findIndex(l => l.trim());
    if (i < 0) { toast("正文是空的"); return; }
    titleIn.value = lines[i].trim();
    lines.splice(i, 1);
    while (lines.length && !lines[0].trim()) lines.shift();
    ta.value = lines.join("\n");
  };
  document.getElementById("edit-down").onclick = () => {
    const t = titleIn.value.trim();
    if (!t) { toast("标题是空的"); return; }
    ta.value = t + "\n" + ta.value;
    // 词牌式命名：无题（首句）——取沉下去那句的第一个停顿前的分句，防标题过长
    const clause = t.split(/[，。、；：？！,.;:!?\s]/).find(s => s) || t;
    titleIn.value = `无题（${clause.slice(0, 12)}）`;
  };
  document.getElementById("edit-save").onclick = () => {
    const title = titleIn.value.trim();
    if (!title) { toast("标题不能为空"); return; }
    const txt = ta.value.replace(/\r\n/g, "\n");
    const contentChanged = txt.replace(/^\n+|\n+$/g, "") !== base.replace(/^\n+|\n+$/g, "");
    if (!contentChanged && title === p.title) { toast("没有改动"); return; }
    const req = { id: p.id, action: "edit", title };
    if (contentChanged) req.content = txt;
    doAction(req, contentChanged
      ? "已保存：正文已更新，旧读数将标为「旧版」"
      : `标题已改为《${title}》`);
  };
}

/* ---------- 分段编辑 ----------
 * 空行分段是恢复备忘录导出时丢失的信息，不是修订：存 corpus/分段.json 侧车，
 * 不动 content、不改 content_hash，已有评论不会变旧版；runner 会把分段
 * 应用进今后读者读到的正文。从统一编辑面板的「仅调分段」进入。 */

function currentBreaks(p) {
  if (Array.isArray(S.stanzas[p.id])) return new Set(S.stanzas[p.id]);
  // 无侧车时，老实信任数据：任何空行都算真分段（与 renderPoemBody 一致）
  const lines = p.content.split("\n");
  const set = new Set();
  let idx = -1, blankRun = 0;
  for (const ln of lines) {
    if (!ln.trim()) { blankRun++; continue; }
    if (idx >= 0 && blankRun >= 1) set.add(idx);
    blankRun = 0; idx++;
  }
  return set;
}

function renderStanzaEditor(p) {
  const body = document.getElementById("poem-body");
  const lines = p.content.split("\n").filter(l => l.trim());
  const cur = currentBreaks(p);
  body.classList.add("stanza-edit");
  body.innerHTML = lines.map((ln, i) =>
    `<p>${esc(ln)}</p>` + (i < lines.length - 1
      ? `<div class="gap-toggle${cur.has(i) ? " on" : ""}" data-i="${i}" title="点击：在此分段/取消"></div>` : "")
  ).join("") + `
    <div class="stanza-hint">点行间空隙插入或取消分段（¶）。分段只影响显示与今后读者读到的排版，不改原文、不会让已有评论变旧版。</div>
    <div class="stanza-bar">
      <button class="btn primary" id="stanza-save">保存分段</button>
      <button class="btn" id="stanza-clear">清空</button>
      <button class="btn" id="stanza-cancel">取消</button>
    </div>`;
  body.querySelectorAll(".gap-toggle").forEach(g => g.onclick = () => g.classList.toggle("on"));
  document.getElementById("stanza-cancel").onclick = () => renderPoem(p.id, false);
  document.getElementById("stanza-clear").onclick = () =>
    body.querySelectorAll(".gap-toggle.on").forEach(g => g.classList.remove("on"));
  document.getElementById("stanza-save").onclick = async () => {
    const breaks = [...body.querySelectorAll(".gap-toggle.on")].map(g => +g.dataset.i);
    try {
      await post("/api/stanzas", { poem_id: p.id, breaks });
      await loadState();
      renderPoem(p.id, false);
      toast(breaks.length ? `已保存 ${breaks.length} 处分段（不触发旧版）` : "已清除手工分段");
    } catch (e) { toast("失败：" + e.message); }
  };
}

/* 划取正文一段 → 剪入自注 */
function wireCutNote(p) {
  const body = document.getElementById("poem-body");
  if (!body) return;
  let btn = null;
  const clear = () => { if (btn) { btn.remove(); btn = null; } };
  document.addEventListener("mousedown", e => { if (btn && !btn.contains(e.target)) clear(); });
  body.addEventListener("mouseup", () => {
    setTimeout(() => {
      const sel = window.getSelection();
      const text = sel ? sel.toString() : "";
      if (!text.trim() || !sel.rangeCount) { return; }
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      clear();
      btn = document.createElement("button");
      btn.className = "btn primary cut-btn";
      btn.textContent = "✂ 剪入自注";
      btn.style.left = (rect.left + rect.width / 2 + window.scrollX) + "px";
      btn.style.top = (rect.top + window.scrollY) + "px";
      document.body.appendChild(btn);
      btn.onclick = () => {
        const span = findSourceSpan(p.content, text);
        clear();
        if (!span) { toast("没能在原文中定位选中的文本"); return; }
        doAction({ id: p.id, action: "cut_note", text: span },
          "已剪入自注（正文与 content_hash 已更新，旧评论将标为旧版）");
      };
    }, 10);
  });
}

/* 把浏览器选区映射回源文本的精确子串（渲染丢了空行，需按行首尾定位） */
function findSourceSpan(content, selected) {
  if (content.includes(selected)) return selected;
  const lines = selected.split("\n").map(l => l.trim()).filter(Boolean);
  if (!lines.length) return null;
  const start = content.indexOf(lines[0]);
  if (start < 0) return null;
  const last = lines[lines.length - 1];
  const lastIdx = content.indexOf(last, start);
  if (lastIdx < 0) return null;
  const span = content.slice(start, lastIdx + last.length);
  for (const ln of lines) if (!span.includes(ln)) return null;
  return span;
}

/* ---------- 统计页：从盲读记录事后派生的图（纯 SVG，无依赖） ---------- */

let statsCalMode = false;   // 作品四图的口径开关：false=原始分，true=校准分（会话内记忆）

function renderStats() {
  app.className = "wide";
  const reads = S.reads.filter(r => r.context_mode === "blind" && !isHidden(r.read_id));
  const calMap = S.calibration.reads || {};
  const hasCal = Object.keys(calMap).length > 0;
  const useCal = statsCalMode && hasCal;
  /* 口径开关只换原料（每条读数的分），公式不变：统计页始终是描述统计，
     不掺贝叶斯收缩与展示拉伸——那是榜单的事 */
  const sc = useCal ? (r => calMap[r.read_id] != null ? calMap[r.read_id] : r.score)
    : (r => r.score);
  const n = reads.length;
  const scores = reads.map(sc);
  const mean = n ? scores.reduce((a, b) => a + b, 0) / n : 0;
  const sd = n ? Math.sqrt(scores.reduce((a, b) => a + (b - mean) ** 2, 0) / n) : 0;

  app.innerHTML = `
    <h1 class="page-title">统计</h1>
    <p class="page-hint">全部从诚实的单篇盲读里事后派生（已折叠的不计入）。共 ${n} 次盲读 · 总均 ${fmt1(mean)} · σ ${fmt1(sd)}${useCal ? "（校准口径）" : ""}。</p>
    ${hasCal ? `<div class="filter-row" style="display:flex;justify-content:flex-end;align-items:center;gap:.45em;margin:-.4rem 0 .6rem">
      <span style="font-size:.75rem;color:var(--ink-3)">作品四图（全景 / 趋势 / 散点 / 文体）口径：</span>
      <button class="btn${useCal ? "" : " on"}" id="st-mode-raw">原始</button>
      <button class="btn${useCal ? " on" : ""}" id="st-mode-cal" title="每条读数换成校准分（人设×模型分位 → 参考分布），再走同样的统计">校准</button>
    </div>` : ""}
    <div class="boards">
      <section class="board hero"><h2>分数分布全景</h2>
        <p class="board-note">所有盲读评分，0.5 分一档；悬停看档位次数</p>
        <div class="dist-wrap" style="max-width:none" id="st-hist"></div>
        <div style="text-align:center;margin-top:.6rem;font-size:.85rem;color:var(--ink-2)">
          曾有读者给出 ≥8 的诗：<b id="club8-n" style="color:#a4593d"></b> 首
          <button class="btn" id="club8-btn" style="margin-left:.7em">详情</button></div>
        <div id="club8" style="display:none;margin-top:.8rem"></div></section>
      <section class="board hero"><h2>均分趋势</h2>
        <p class="board-note">按诗的写作月份：淡点 = 单月均分（点越大读数越多），实线 = ±4 个月加权滑动平均</p>
        <div class="dist-wrap" style="max-width:none" id="st-year"></div></section>
      <section class="board hero"><h2>两极散点</h2>
        <p class="board-note">每首诗一个点：横轴均分，纵轴 σ（读者分歧）。右上角 = 分高且撕裂的危险好诗；取景掐掉两端极值，出界的离群点钉在图缘画成空心点。悬停看详情，点击直达该诗</p>
        <div class="dist-wrap" style="max-width:none" id="st-polar"></div></section>
      <section class="board hero"><h2>文体对比</h2>
        <p class="board-note">细线 = 该文体得分的 5–95 百分位（个别极端分不拉伸，全距在悬停里），粗段 = 均值 ± σ，实心点 = 均值；按均值排序</p>
        <div class="dist-wrap" style="max-width:none" id="st-genre"></div></section>
      <section class="board hero"><h2>覆盖层数</h2>
        <p class="board-note">读者池内每首诗被盲读的次数分布——派发前看缺口在哪</p>
        <div class="dist-wrap" style="max-width:none" id="st-cov"></div></section>
      <section class="board hero"><h2>读者的松紧</h2>
        <p class="board-note">细线 = 该读者给分的 5–95 百分位，粗段 = 均值 ± σ，实心点 = 均值（数值在行尾）；从松到紧排。始终原始口径——这一页看的就是松紧本身</p>
        <div class="dist-wrap" style="max-width:none" id="st-readers"></div></section>
      <section class="board hero"><h2>评分门槛</h2>
        <p class="board-note">每位读者给出 ≥X 分的比例；X 可调，虚线是不分读者的总体比例，从高到低排</p>
        <div style="display:flex;align-items:center;gap:.8em;margin-bottom:.6em">
          <input type="range" id="st-thresh-x" min="0" max="10" step="0.5" value="8" style="flex:1">
          <span id="st-thresh-label" style="font-weight:600;min-width:3.4em;text-align:right"></span>
        </div>
        <div class="dist-wrap" style="max-width:none" id="st-thresh"></div></section>
      <section class="board hero"><h2>幕后演员</h2>
        <p class="board-note">同一位读者由不同模型扮演时，手势不同。细线 = 5–95 百分位，粗段 = 均值 ± σ，行尾 = 均值与 ≥8 发放率；只列 ≥30 读的模型。始终原始口径</p>
        <div class="dist-wrap" style="max-width:none" id="st-models"></div></section>
      <section class="board hero"><h2>松紧修正</h2>
        <p class="board-note">每个模型的全部读数经正式校准（人设×模型分位 → 参考分布，与榜单"质"分同一套）后的均分位移：Δ 为正 = 手紧被抬回，为负 = 手松被压回。始终双列并示，不随口径开关。</p>
        <div class="dist-wrap" style="max-width:none" id="st-cal"></div></section>
    </div>`;
  histChart(document.getElementById("st-hist"), scores);
  // ≥8 俱乐部：曾有任一读者给出 ≥8 的诗——用"曾得高分"而非"均分过线"，
  // 不会因为评委团里进来一位手紧的读者而整批除名
  const club = pool().map(p => {
    const rs = statReads(p.id).map(sc);
    const cMean = rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : 0;
    return { p, mean: cMean, hi: rs.filter(s => s >= 8).length, max: rs.length ? Math.max(...rs) : 0 };
  }).filter(o => o.hi > 0).sort((a, b) => b.max - a.max || b.hi - a.hi);
  const btn8 = document.getElementById("club8-btn"), box8 = document.getElementById("club8");
  document.getElementById("club8-n").textContent = club.length;
  btn8.onclick = () => {
    if (!box8.innerHTML) box8.innerHTML = boardList(club.map(o => o.p), p => {
      const o = club.find(c => c.p.id === p.id);
      return `最高 ${fmt1(o.max)} · ${o.hi} 次 ≥8 · 均 ${fmt1(o.mean)}`;
    });
    box8.style.display = box8.style.display === "none" ? "" : "none";
    btn8.classList.toggle("on");
  };
  trendChart(document.getElementById("st-year"), reads, sc);
  polarChart(document.getElementById("st-polar"), sc);
  genreChart(document.getElementById("st-genre"), sc);
  covChart(document.getElementById("st-cov"));
  readerChart(document.getElementById("st-readers"));
  const threshSlider = document.getElementById("st-thresh-x");
  const threshLabel = document.getElementById("st-thresh-label");
  const threshEl = document.getElementById("st-thresh");
  const renderThresh = () => {
    const x = parseFloat(threshSlider.value);
    threshLabel.textContent = `≥ ${fmt1(x)}`;
    thresholdChart(threshEl, reads, x);
  };
  threshSlider.addEventListener("input", renderThresh);
  renderThresh();
  modelChart(document.getElementById("st-models"), reads, 30);
  calShiftBoard(document.getElementById("st-cal"), reads, calMap);
  if (hasCal) {
    const setMode = v => { if (statsCalMode !== v) { statsCalMode = v; renderStats(); } };
    document.getElementById("st-mode-raw").onclick = () => setMode(false);
    document.getElementById("st-mode-cal").onclick = () => setMode(true);
  }
}

function histChart(el, scores) {
  if (!scores.length) { el.innerHTML = '<p class="empty">暂无数据</p>'; return; }
  const W = 640, H = 220, padL = 24, padR = 24, axisH = 26;
  const bins = new Array(21).fill(0);
  for (const s of scores) bins[Math.max(0, Math.min(20, Math.round(s * 2)))]++;
  const maxB = Math.max(...bins);
  const x = s => padL + (s / 10) * (W - padL - padR);
  const baseY = H - axisH;
  const bw = (W - padL - padR) / 21;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="分数分布">`;
  svg += `<line x1="${padL - 6}" y1="${baseY}" x2="${W - padR + 6}" y2="${baseY}" stroke="#e3dac7"/>`;
  for (let t = 0; t <= 10; t++)
    svg += `<line x1="${x(t)}" y1="${baseY}" x2="${x(t)}" y2="${baseY + 4}" stroke="#e3dac7"/>` +
      `<text x="${x(t)}" y="${baseY + 17}" text-anchor="middle" font-size="11" fill="#a29786">${t}</text>`;
  bins.forEach((c, i) => {
    if (!c) return;
    const h = Math.max(2, (baseY - 22) * c / maxB);
    svg += `<rect x="${x(i / 2) - bw * .42}" y="${baseY - h}" width="${bw * .84}" height="${h}" rx="2"
      fill="#2f6d62" fill-opacity=".8"><title>${(i / 2).toFixed(1)} 分 · ${c} 次</title></rect>` +
      `<text x="${x(i / 2)}" y="${baseY - h - 4}" text-anchor="middle" font-size="9.5" fill="#a29786">${c}</text>`;
  });
  el.innerHTML = svg + "</svg>";
}

/* 均分趋势：行业标准的「原始点 + 平滑线」叠加——
 * 淡点 = 单月均分（半径随读数），实线 = ±4 个月加权滑动平均（按读数加权，间隙月自然跨过）。 */
function trendChart(el, reads, sc = r => r.score) {
  const byMonth = new Map(); // monthIndex -> {sum, n}
  for (const r of reads) {
    const p = maps.poem.get(r.poem_id);
    const d = p ? (p.date_written || p.created || "") : "";
    if (d.length < 7) continue;
    const mi = (+d.slice(0, 4)) * 12 + (+d.slice(5, 7)) - 1;
    const m = byMonth.get(mi) || { sum: 0, n: 0 };
    m.sum += sc(r); m.n++;
    byMonth.set(mi, m);
  }
  const mis = [...byMonth.keys()].sort((a, b) => a - b);
  if (mis.length < 3) { el.innerHTML = '<p class="empty">月份太少，画不成趋势。</p>'; return; }
  const lo_mi = mis[0], hi_mi = mis[mis.length - 1];

  // 加权滑动平均：窗口 ±4 个月，窗内读数 < 3 时跳过（避免孤月假信号）
  const HALF = 4;
  const smooth = [];
  for (let i = lo_mi; i <= hi_mi; i++) {
    let sum = 0, n = 0;
    for (let j = i - HALF; j <= i + HALF; j++) {
      const m = byMonth.get(j);
      if (m) { sum += m.sum; n += m.n; }
    }
    if (n >= 3) smooth.push({ mi: i, mean: sum / n });
  }
  const monthly = mis.map(mi => ({ mi, n: byMonth.get(mi).n, mean: byMonth.get(mi).sum / byMonth.get(mi).n }));

  const W = 640, H = 260, padL = 40, padR = 24, padT = 16, axisH = 30;
  const allVals = monthly.map(m => m.mean).concat(smooth.map(s => s.mean));
  const lo = Math.max(0, Math.floor(Math.min(...allVals) - .5));
  const hi = Math.min(10, Math.ceil(Math.max(...allVals) + .5));
  const x = mi => padL + (mi - lo_mi) / (hi_mi - lo_mi) * (W - padL - padR);
  const yy = v => padT + (hi - v) / (hi - lo) * (H - padT - axisH);
  const baseY = H - axisH;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="均分趋势">`;
  for (let v = lo; v <= hi; v++)
    svg += `<line x1="${padL}" y1="${yy(v)}" x2="${W - padR}" y2="${yy(v)}" stroke="#e3dac7" stroke-dasharray="2 5"/>` +
      `<text x="${padL - 8}" y="${yy(v) + 4}" text-anchor="end" font-size="11" fill="#a29786">${v}</text>`;
  // 年份刻度：每年 1 月；起点若在年中，也补一个起始年刻度
  for (let i = lo_mi; i <= hi_mi; i++) {
    if (i % 12 === 0) {
      svg += `<line x1="${x(i)}" y1="${baseY}" x2="${x(i)}" y2="${baseY + 4}" stroke="#e3dac7"/>` +
        `<text x="${x(i)}" y="${baseY + 18}" text-anchor="middle" font-size="11" fill="#a29786">${i / 12}</text>`;
    }
  }
  if (lo_mi % 12 !== 0)
    svg += `<line x1="${x(lo_mi)}" y1="${baseY}" x2="${x(lo_mi)}" y2="${baseY + 4}" stroke="#e3dac7"/>` +
      `<text x="${x(lo_mi)}" y="${baseY + 18}" text-anchor="middle" font-size="11" fill="#a29786">${Math.floor(lo_mi / 12)}</text>`;
  // 淡点：单月均分（悬停出提示框）
  for (const m of monthly) {
    const yr = Math.floor(m.mi / 12), mo = String(m.mi % 12 + 1).padStart(2, "0");
    svg += `<circle class="tdot" data-l="${yr}-${mo}" data-m="${fmt1(m.mean)}" data-n="${m.n}"
      cx="${x(m.mi)}" cy="${yy(m.mean)}" r="${2 + Math.min(3.5, m.n / 4)}"
      fill="#2f6d62" fill-opacity=".28" style="cursor:pointer"/>`;
  }
  // 实线：加权滑动平均
  svg += `<polyline points="${smooth.map(s => `${x(s.mi)},${yy(s.mean)}`).join(" ")}"
    fill="none" stroke="#a4593d" stroke-width="2" stroke-linejoin="round"/>`;
  // 首尾端点标数值
  const ends = [smooth[0], smooth[smooth.length - 1]];
  for (const s of ends)
    svg += `<circle cx="${x(s.mi)}" cy="${yy(s.mean)}" r="3.5" fill="#a4593d"/>` +
      `<text x="${x(s.mi)}" y="${yy(s.mean) - 9}" text-anchor="middle" font-size="10.5" fill="#a4593d" font-weight="600">${fmt1(s.mean)}</text>`;
  el.innerHTML = svg + "</svg>";

  let tip = document.querySelector(".tip");
  if (!tip) { tip = document.createElement("div"); tip.className = "tip"; document.body.appendChild(tip); }
  el.querySelectorAll(".tdot").forEach(c => {
    c.addEventListener("mousemove", e => {
      tip.innerHTML = `<b>${c.dataset.l}</b> · 均 ${c.dataset.m} · ${c.dataset.n} 读`;
      tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 200) + "px";
      tip.style.top = (e.clientY + 14) + "px";
      tip.classList.add("show");
    });
    c.addEventListener("mouseleave", () => tip.classList.remove("show"));
  });
}

function readerChart(el) {
  const rows = readerRanking().filter(r => r.n > 0);
  if (!rows.length) { el.innerHTML = '<p class="empty">暂无数据</p>'; return; }
  const W = 640, rowH = 26, padL = 150, padR = 72, axisH = 26, padT = 8;
  const H = padT + rows.length * rowH + axisH;
  const x = s => padL + (s / 10) * (W - padL - padR);
  const baseY = padT + rows.length * rowH;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="读者松紧">`;
  for (let t = 0; t <= 10; t += 2)
    svg += `<line x1="${x(t)}" y1="${padT}" x2="${x(t)}" y2="${baseY}" stroke="#e3dac7" stroke-dasharray="2 5"/>` +
      `<text x="${x(t)}" y="${baseY + 16}" text-anchor="middle" font-size="11" fill="#a29786">${t}</text>`;
  rows.forEach((r, i) => {
    const cy = padT + i * rowH + rowH / 2;
    const ss = r.reads.map(x => x.score);
    const lo = quant(ss, .05), hi = quant(ss, .95);
    svg += `<text x="${padL - 10}" y="${cy + 4}" text-anchor="end" font-size="11" fill="#6b6154">${esc(r.persona.name)}</text>` +
      `<line x1="${x(lo)}" y1="${cy}" x2="${x(hi)}" y2="${cy}" stroke="#2f6d62" stroke-width="1.2" opacity=".5"/>` +
      `<line x1="${x(Math.max(0, r.mean - r.sd))}" y1="${cy}" x2="${x(Math.min(10, r.mean + r.sd))}" y2="${cy}" stroke="#2f6d62" stroke-width="4" opacity=".35"/>` +
      `<circle cx="${x(r.mean)}" cy="${cy}" r="4" fill="#2f6d62"><title>${esc(r.persona.name)} · 均 ${fmt1(r.mean)} · σ ${fmt1(r.sd)} · ${r.n} 读 · ${fmt1(r.min)}–${fmt1(r.max)}</title></circle>` +
      `<text x="${W - 6}" y="${cy + 4}" text-anchor="end" font-size="10.5" fill="#6b6154"><tspan font-weight="600">${fmt1(r.mean)}</tspan><tspan fill="#a29786"> ±${fmt1(r.sd)}</tspan></text>`;
  });
  el.innerHTML = svg + "</svg>";
}

/* 评分门槛：每位读者给出 ≥threshold 分的比例，横条从高到低排；虚线 = 不分读者的总体比例 */
function thresholdChart(el, allReads, threshold) {
  const rows = readerRanking().filter(r => r.n > 0).map(r => {
    const hits = r.reads.filter(x => x.score >= threshold).length;
    return { persona: r.persona, n: r.n, hits, pct: hits / r.n * 100 };
  }).sort((a, b) => b.pct - a.pct || b.n - a.n);
  if (!rows.length) { el.innerHTML = '<p class="empty">暂无数据</p>'; return; }
  const allHits = allReads.filter(r => r.score >= threshold).length;
  const allPct = allReads.length ? allHits / allReads.length * 100 : 0;

  const W = 640, rowH = 22, padL = 150, padR = 56, axisH = 24, padT = 8;
  const H = padT + rows.length * rowH + axisH;
  const x = pct => padL + (pct / 100) * (W - padL - padR);
  const baseY = padT + rows.length * rowH;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="评分门槛比例">`;
  for (let t = 0; t <= 100; t += 25)
    svg += `<line x1="${x(t)}" y1="${padT}" x2="${x(t)}" y2="${baseY}" stroke="#e3dac7" stroke-dasharray="2 5"/>` +
      `<text x="${x(t)}" y="${baseY + 16}" text-anchor="middle" font-size="11" fill="#a29786">${t}%</text>`;
  svg += `<line x1="${x(allPct)}" y1="${padT}" x2="${x(allPct)}" y2="${baseY}" stroke="#a4593d" stroke-width="1.2" stroke-dasharray="4 3" opacity=".8"><title>总体（不分读者）· ${fmt1(allPct)}%</title></line>`;
  rows.forEach((r, i) => {
    const cy = padT + i * rowH + rowH / 2, bh = rowH * .62;
    svg += `<text x="${padL - 10}" y="${cy + 4}" text-anchor="end" font-size="11" fill="#6b6154">${esc(r.persona.name)}</text>` +
      `<rect x="${padL}" y="${cy - bh / 2}" width="${Math.max(0, x(r.pct) - padL)}" height="${bh}" rx="2" fill="#2f6d62" fill-opacity=".8">` +
      `<title>${esc(r.persona.name)} · ${fmt1(r.pct)}% (${r.hits}/${r.n})</title></rect>` +
      `<text x="${x(r.pct) + 6}" y="${cy + 4}" font-size="10.5" fill="#6b6154">${fmt1(r.pct)}%</text>`;
  });
  el.innerHTML = svg + "</svg>";
}

/* 线性插值分位数：t ∈ [0,1] */
function quant(arr, t) {
  const b = [...arr].sort((m, n) => m - n);
  const i = (b.length - 1) * t, lo = Math.floor(i);
  return b[lo] + (b[Math.ceil(i)] - b[lo]) * (i - lo);
}

/* 共享悬停提示框（与趋势图同一个 .tip div） */
function tipFor(el, sel, htmlOf) {
  let tip = document.querySelector(".tip");
  if (!tip) { tip = document.createElement("div"); tip.className = "tip"; document.body.appendChild(tip); }
  el.querySelectorAll(sel).forEach(c => {
    c.addEventListener("mousemove", e => {
      tip.innerHTML = htmlOf(c);
      tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 220) + "px";
      tip.style.top = (e.clientY + 14) + "px";
      tip.classList.add("show");
    });
    c.addEventListener("mouseleave", () => tip.classList.remove("show"));
  });
}

/* 两极散点：x = 均分，y = σ。虚线十字 = 全体中位，右上象限即「分高且撕裂」。
 * σ 至少要 2 读才有意义，单读的诗不上图。 */
function polarChart(el, sc = r => r.score) {
  const pts = pool().map(p => {
    const rs = statReads(p.id);
    if (rs.length < 2) return null;
    const ss = rs.map(sc);
    const mean = ss.reduce((a, b) => a + b, 0) / ss.length;
    const sd = Math.sqrt(ss.reduce((a, b) => a + (b - mean) ** 2, 0) / ss.length);
    return { p, n: ss.length, mean, sd };
  }).filter(Boolean);
  if (pts.length < 3) { el.innerHTML = '<p class="empty">≥2 读的诗太少，画不成散点。</p>'; return; }
  const W = 640, H = 300, padL = 40, padR = 20, padT = 14, axisH = 30;
  const xs = pts.map(o => o.mean), ys = pts.map(o => o.sd);
  // 取景按 2–98 百分位定界，主群不被个别极值挤扁；出界点钉在图缘画成空心点
  const xlo = Math.max(0, Math.floor((quant(xs, .02) - .3) * 2) / 2);
  const xhi = Math.min(10, Math.ceil((quant(xs, .98) + .3) * 2) / 2);
  const yhi = Math.max(.5, Math.ceil((quant(ys, .98) + .2) * 2) / 2);
  const x = v => padL + (v - xlo) / (xhi - xlo) * (W - padL - padR);
  const y = v => padT + (yhi - v) / yhi * (H - padT - axisH);
  const baseY = H - axisH;
  const mx = quant(xs, .5), my = quant(ys, .5);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="两极散点">`;
  for (let v = Math.ceil(xlo); v <= xhi; v++)
    svg += `<line x1="${x(v)}" y1="${baseY}" x2="${x(v)}" y2="${baseY + 4}" stroke="#e3dac7"/>` +
      `<text x="${x(v)}" y="${baseY + 17}" text-anchor="middle" font-size="11" fill="#a29786">${v}</text>`;
  for (let v = 0; v <= yhi; v += .5)
    svg += `<line x1="${padL}" y1="${y(v)}" x2="${W - padR}" y2="${y(v)}" stroke="#e3dac7" stroke-dasharray="2 5"/>` +
      `<text x="${padL - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="#a29786">${v.toFixed(1)}</text>`;
  svg += `<line x1="${x(mx)}" y1="${padT}" x2="${x(mx)}" y2="${baseY}" stroke="#a4593d" stroke-dasharray="4 4" opacity=".4"/>` +
    `<line x1="${padL}" y1="${y(my)}" x2="${W - padR}" y2="${y(my)}" stroke="#a4593d" stroke-dasharray="4 4" opacity=".4"/>` +
    `<text x="${W - padR - 4}" y="${padT + 12}" text-anchor="end" font-size="10.5" fill="#a4593d" opacity=".75">分高且撕裂 →</text>`;
  for (const o of pts) {
    const cxv = Math.min(xhi, Math.max(xlo, o.mean)), cyv = Math.min(yhi, o.sd);
    const out = cxv !== o.mean || cyv !== o.sd;
    svg += `<circle class="sdot" data-id="${o.p.id}" data-t="${esc(o.p.title)}" data-m="${fmt1(o.mean)}" data-s="${fmt1(o.sd)}" data-n="${o.n}"${out ? ' data-o="1"' : ""}
      cx="${x(cxv)}" cy="${y(cyv)}" r="${3 + Math.min(2, o.n / 6)}"
      ${out ? 'fill="none" stroke="#a4593d" stroke-width="1.6"' : 'fill="#2f6d62" fill-opacity=".5"'} pointer-events="all" style="cursor:pointer"/>`;
  }
  el.innerHTML = svg + "</svg>";
  tipFor(el, ".sdot", c => `<b>《${c.dataset.t}》</b> · 均 ${c.dataset.m} · σ ${c.dataset.s} · ${c.dataset.n} 读${c.dataset.o ? "（离群，钉在图缘）" : ""}`);
  el.querySelectorAll(".sdot").forEach(c => c.onclick = () => { location.hash = "#/poem/" + c.dataset.id; });
}

/* 文体对比：与读者松紧同一图式，行 = 文体 */
function genreChart(el, sc = r => r.score) {
  const agg = new Map();
  for (const p of pool()) {
    const rs = statReads(p.id);
    if (!rs.length) continue;
    const g = p.genre || "未分类";
    const a = agg.get(g) || { scores: [], poems: 0 };
    for (const r of rs) a.scores.push(sc(r));
    a.poems++;
    agg.set(g, a);
  }
  const rows = [...agg.entries()].map(([g, a]) => {
    const mean = a.scores.reduce((m, n) => m + n, 0) / a.scores.length;
    const sd = Math.sqrt(a.scores.reduce((m, n) => m + (n - mean) ** 2, 0) / a.scores.length);
    return { g, poems: a.poems, n: a.scores.length, mean, sd,
      min: Math.min(...a.scores), max: Math.max(...a.scores),
      lo: quant(a.scores, .05), hi: quant(a.scores, .95) };
  }).sort((a, b) => b.mean - a.mean);
  if (!rows.length) { el.innerHTML = '<p class="empty">暂无数据</p>'; return; }
  const W = 640, rowH = 26, padL = 150, padR = 72, axisH = 26, padT = 8;
  const H = padT + rows.length * rowH + axisH;
  const x = s => padL + (s / 10) * (W - padL - padR);
  const baseY = padT + rows.length * rowH;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="文体对比">`;
  for (let t = 0; t <= 10; t += 2)
    svg += `<line x1="${x(t)}" y1="${padT}" x2="${x(t)}" y2="${baseY}" stroke="#e3dac7" stroke-dasharray="2 5"/>` +
      `<text x="${x(t)}" y="${baseY + 16}" text-anchor="middle" font-size="11" fill="#a29786">${t}</text>`;
  rows.forEach((r, i) => {
    const cy = padT + i * rowH + rowH / 2;
    svg += `<text x="${padL - 10}" y="${cy + 4}" text-anchor="end" font-size="11" fill="#6b6154">${esc(r.g)}<tspan fill="#a29786">（${r.poems} 首）</tspan></text>` +
      `<line x1="${x(r.lo)}" y1="${cy}" x2="${x(r.hi)}" y2="${cy}" stroke="#2f6d62" stroke-width="1.2" opacity=".5"/>` +
      `<line x1="${x(Math.max(0, r.mean - r.sd))}" y1="${cy}" x2="${x(Math.min(10, r.mean + r.sd))}" y2="${cy}" stroke="#2f6d62" stroke-width="4" opacity=".35"/>` +
      `<circle cx="${x(r.mean)}" cy="${cy}" r="4" fill="#2f6d62"><title>${esc(r.g)} · 均 ${fmt1(r.mean)} · σ ${fmt1(r.sd)} · ${r.poems} 首 ${r.n} 读 · 全距 ${fmt1(r.min)}–${fmt1(r.max)}</title></circle>` +
      `<text x="${W - 6}" y="${cy + 4}" text-anchor="end" font-size="10.5" fill="#6b6154"><tspan font-weight="600">${fmt1(r.mean)}</tspan><tspan fill="#a29786"> ±${fmt1(r.sd)}</tspan></text>`;
  });
  el.innerHTML = svg + "</svg>";
}

/* 松紧修正：正式校准（calibrate.py 下发的单读校准分，与榜单"质"分同一套）
 * 对每个模型读数的均分位移。取代旧的前端 z 分"校准视角"原型——那套独立
 * 算法算出的数字与"质"分对不上，两种"校准"并存只会误导。 */
function calShiftBoard(el, reads, calMap) {
  const byM = new Map();
  for (const r of reads) {
    const c = calMap[r.read_id];
    if (c == null) continue;
    const a = byM.get(modelAlias(r.reader.model)) || { n: 0, raw: 0, cal: 0 };
    a.n++; a.raw += r.score; a.cal += c;
    byM.set(modelAlias(r.reader.model), a);
  }
  if (!byM.size) { el.innerHTML = '<p class="empty">暂无校准数据（calibrate.py 还没跑出 scores.json）。</p>'; return; }
  const rows = [...byM.entries()].map(([m, a]) => {
    const raw = a.raw / a.n, cal = a.cal / a.n;
    return { m, n: a.n, raw, cal, d: cal - raw };
  }).sort((a, b) => b.d - a.d);
  const W = 640, rowH = 26, padL = 150, padR = 178, padT = 8, axisH = 26;
  const H = padT + rows.length * rowH + axisH;
  const dmax = Math.max(.3, ...rows.map(r => Math.abs(r.d))) * 1.15;
  const x = d => padL + (d + dmax) / (2 * dmax) * (W - padL - padR);
  const baseY = padT + rows.length * rowH;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="松紧修正">`;
  for (const t of [-.6, -.4, -.2, .2, .4, .6]) if (Math.abs(t) <= dmax + 1e-9)
    svg += `<line x1="${x(t)}" y1="${padT}" x2="${x(t)}" y2="${baseY}" stroke="#e3dac7" stroke-dasharray="2 5"/>` +
      `<text x="${x(t)}" y="${baseY + 16}" text-anchor="middle" font-size="11" fill="#a29786">${(t > 0 ? "+" : "") + t.toFixed(1)}</text>`;
  svg += `<line x1="${x(0)}" y1="${padT}" x2="${x(0)}" y2="${baseY}" stroke="#a29786" stroke-dasharray="2 4"/>` +
    `<text x="${x(0)}" y="${baseY + 16}" text-anchor="middle" font-size="11" fill="#a29786">0</text>`;
  rows.forEach((r, i) => {
    const cy = padT + i * rowH + rowH / 2, bh = rowH * .56;
    const x0 = x(0), x1 = x(r.d);
    const col = r.d >= 0 ? "#2f6d62" : "#a4593d";
    svg += `<text x="${padL - 10}" y="${cy + 4}" text-anchor="end" font-size="11" fill="#6b6154">${esc(r.m)}<tspan fill="#a29786">（${r.n}）</tspan></text>` +
      `<rect x="${Math.min(x0, x1)}" y="${cy - bh / 2}" width="${Math.max(1.5, Math.abs(x1 - x0))}" height="${bh}" rx="2" fill="${col}" fill-opacity=".75">` +
      `<title>${esc(r.m)} · 原始均 ${fmt2(r.raw)} → 校准均 ${fmt2(r.cal)}（Δ ${(r.d >= 0 ? "+" : "") + fmt2(r.d)}）· ${r.n} 读</title></rect>` +
      `<text x="${W - 6}" y="${cy + 4}" text-anchor="end" font-size="10.5" fill="#6b6154">${fmt2(r.raw)} → <tspan font-weight="600">${fmt2(r.cal)}</tspan><tspan fill="${col}">（${(r.d >= 0 ? "+" : "") + fmt2(r.d)}）</tspan></text>`;
  });
  el.innerHTML = svg + "</svg>";
}

/* 覆盖层数：x = 被盲读次数，y = 有几首诗停在这个层数 */
function covChart(el) {
  const counts = pool().map(p => statReads(p.id).length);
  if (!counts.length) { el.innerHTML = '<p class="empty">暂无数据</p>'; return; }
  const maxC = Math.max(...counts);
  const bins = new Array(maxC + 1).fill(0);
  for (const c of counts) bins[c]++;
  const maxB = Math.max(...bins);
  const W = 640, H = 214, padL = 24, padR = 24, axisH = 44;
  const x = i => padL + (maxC ? i / maxC : 0) * (W - padL - padR);
  const baseY = H - axisH;
  const bw = (W - padL - padR) / (maxC + 1);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="覆盖层数">`;
  svg += `<line x1="${padL - 6}" y1="${baseY}" x2="${W - padR + 6}" y2="${baseY}" stroke="#e3dac7"/>`;
  bins.forEach((c, i) => {
    svg += `<text x="${x(i)}" y="${baseY + 17}" text-anchor="middle" font-size="11" fill="#a29786">${i}</text>`;
    if (!c) return;
    const h = Math.max(2, (baseY - 24) * c / maxB);
    svg += `<rect x="${x(i) - bw * .36}" y="${baseY - h}" width="${bw * .72}" height="${h}" rx="2"
      fill="${i === 0 ? "#a4593d" : "#2f6d62"}" fill-opacity=".8"><title>${i} 读 · ${c} 首</title></rect>` +
      `<text x="${x(i)}" y="${baseY - h - 4}" text-anchor="middle" font-size="9.5" fill="#a29786">${c}</text>`;
  });
  svg += `<text x="${W - padR}" y="${H - 4}" text-anchor="end" font-size="10.5" fill="#a29786">被盲读次数 →</text>`;
  el.innerHTML = svg + "</svg>";
}

/* ---------- 深读页 ---------- */

function renderDeepRead(rid) {
  app.className = "";
  const r = maps.readById.get(rid);
  if (!r || !r.long_form) { app.innerHTML = `<p class="page-hint">没有这篇深读。</p>`; return; }
  const p = maps.poem.get(r.poem_id);
  app.innerHTML = `<div class="deep-read">
    <a class="back" href="#/poem/${r.poem_id}">← 回到《${esc(p ? p.title : r.poem_id)}》</a>
    <h1 class="page-title" style="margin-top:1.2rem">深读 · ${esc(personaName(r.reader.persona_id))}</h1>
    <p class="page-hint">
      <span class="chip">${esc(modelAlias(r.reader.model))}</span>
      <span class="chip">${esc(r.transport)}</span>
      <span class="score-badge">评分 ${fmt1(r.score)}</span>
      ${r.content_hash !== (p && p.content_hash) ? '<span class="chip warm">读的是旧版</span>' : ""}
    </p>
    <div class="long-form">${esc(r.long_form)}</div>
    <div style="margin-top:2.5rem">
      <button class="btn" id="promote">升格进《昼青·诠释》</button>
      <span style="font-size:.78rem;color:var(--ink-3);margin-left:.8em">评论区是流水，诠释册是沉淀；此动作只由作者手动。</span>
    </div></div>`;
  const btn = document.getElementById("promote");
  let armed = false;
  btn.onclick = async () => {
    if (!armed) { armed = true; btn.textContent = "确认升格？再点一次"; btn.classList.add("primary"); return; }
    try { await post("/api/promote", { read_id: rid }); toast("已升格进诠释册"); btn.disabled = true; btn.textContent = "已升格"; }
    catch (e) { toast("失败：" + e.message); }
  };
}

/* ---------- 启动 ---------- */

route();
