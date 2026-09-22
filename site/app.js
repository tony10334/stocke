/* 主動 ETF 觀測站 — 前端（純靜態，讀 data/site-data.json） */
(function () {
  "use strict";

  const DATA_URL = new URLSearchParams(location.search).get("data") || "../data/site-data.json";
  const ACTION_LABEL = { new: "建倉", add: "加碼", reduce: "減碼", exit: "出清", corporate_action: "疑似公司行動" };
  const ACTION_ORDER = ["new", "add", "reduce", "exit", "corporate_action"];
  const FALLBACK_COLORS = ["#167dca", "#1e8f5a", "#d97b1f", "#7c5cd6", "#c2416b"];
  const ICON_UP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7h10v10"/><path d="M7 17 17 7"/></svg>';
  const ICON_CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const ICON_TREND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 7h6v6"/><path d="m22 7-8.5 8.5-5-5L2 17"/></svg>';
  const ICON_DB = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/></svg>';

  // 觀察設定：只存在這個瀏覽器（localStorage），資料本身不變
  const SETTINGS_KEY = "stocketf.settings.v2";  // v2：預設方向改為 both（加碼與減碼一起看）
  const SECTIONS = [
    ["signals", "共同異動雷達"], ["cash", "現金水位"], ["holdings", "持股與調整"],
    ["m-close", "收盤價"], ["m-nav", "基金淨值"], ["m-vol", "成交量"], ["m-chg", "每日漲跌幅"],
    ["m-prem", "折溢價"], ["m-aum", "基金規模"], ["m-units", "流通單位數"], ["m-top10", "前十大集中度"],
  ];
  let settings = { window: 1, basis: "funds", min: null, direction: "both", strength: null, show: Object.fromEntries(SECTIONS.map(([k]) => [k, true])) };
  const loadSettings = () => { try { const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null"); if (s) settings = { ...settings, ...s, show: { ...settings.show, ...(s.show || {}) } }; } catch (e) { /* ignore */ } };
  const saveSettings = () => { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) { /* ignore */ } };
  const showSection = (key) => settings.show[key] !== false;

  let site = null;
  let color = {};                 // etf -> 顏色（依 etfs.json 順序固定，不隨篩選改變）
  let selected = new Set();       // 篩選中的 ETF
  let period = "90";              // 圖表期間
  let detailEtf = null;
  let detailTab = "price";
  let holdingsSort = { key: "weight", dir: -1 };
  const charts = {};

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, attrs, ...children) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "style") n.setAttribute("style", v);
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const c of children.flat(Infinity)) {
      if (c == null || c === false) continue;
      n.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return n;
  };
  const fill = (node, ...children) => node.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false));
  const fmtInt = (v) => (v == null ? "–" : Math.round(v).toLocaleString("zh-Hant-TW"));
  const fmtNum = (v, d = 2) => (v == null ? "–" : Number(v).toLocaleString("zh-Hant-TW", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const fmtPct = (v, d = 2) => (v == null ? "–" : fmtNum(v, d) + "%");
  const fmtYi = (v, d = 1) => (v == null ? "–" : fmtNum(v / 1e8, d));
  const signed = (v, d = 2) => (v == null ? "–" : (v > 0 ? "+" : "") + fmtNum(v, d));
  const per1k = (pu) => (pu == null ? "–" : fmtNum(pu * 1000, 3));
  const cls = (v) => (v == null || v === 0 ? "flat" : v > 0 ? "up" : "down");
  const arrow = (v) => (v == null || v === 0 ? "" : v > 0 ? "▲ " : "▼ ");
  const delta = (v, d = 2, suffix = "") => (v == null ? el("span", { class: "flat" }, "–") : el("span", { class: cls(v) + " num" }, arrow(v) + signed(v, d) + suffix));
  const etfMeta = (code) => site.etfs.find((e) => e.code === code) || { code, name: code };
  const providerName = (e) => e.issuer || (e.provider === "uni" ? "統一投信" : e.provider === "fubon" ? "富邦投信" : e.provider);
  const pill = (code, text, action) => el("span", { class: "pill" + (action ? " action-" + action : ""), style: `--fund-color:${color[code]}` }, el("code", null, code), text ? el("span", null, text) : null);
  const actionPill = (action) => el("span", { class: "pill action-" + action }, ACTION_LABEL[action] || action);
  const lastN = (arr) => (period === "all" ? arr : arr.slice(-Number(period)));
  const activeEtfs = () => site.etfs.filter((e) => selected.has(e.code));

  function table(headers, rows, opts = {}) {
    const thead = el("thead", null, el("tr", null, headers.map((h) => el("th", { class: h.num ? "num" : null }, h.label))));
    const tbody = el("tbody", null, rows.map((r) => el("tr", { class: r.cls }, r.cells.map((c, i) => el("td", { class: headers[i].num ? "num" : null }, c)))));
    return el("div", { class: "table-wrap" + (opts.compact ? " compact" : "") }, el("table", null, thead, tbody));
  }

  // ---------------------------------------------------------------- charts
  const THEME = { ink: "#52686f", muted: "#8b979c", grid: "#e9eef0", axis: "#d7e0e3" };

  function makeChart(id, type, labels, datasets, opts = {}) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    const ctx = document.getElementById(id);
    if (!ctx) return;
    const unit = opts.unit || "";
    const digits = opts.digits ?? 2;
    charts[id] = new Chart(ctx, {
      type,
      data: {
        labels: labels.map((d) => d.slice(5)),
        datasets: datasets.map((d) => ({
          label: d.label, data: d.data,
          borderColor: d.color, backgroundColor: type === "bar" ? d.color : d.color,
          borderWidth: type === "bar" ? 0 : 2, borderRadius: type === "bar" ? 3 : 0,
          pointRadius: 0, pointHoverRadius: 4, pointHitRadius: 12, tension: 0.25, spanGaps: true,
          borderDash: d.dash || undefined, fill: false,
        })),
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: opts.legend ?? datasets.length > 1, position: "bottom", labels: { color: THEME.ink, boxWidth: 8, boxHeight: 8, usePointStyle: true, pointStyle: "circle", font: { size: 11 } } },
          tooltip: {
            backgroundColor: "#102b33", titleColor: "#d8e3e6", bodyColor: "#fff", padding: 10, cornerRadius: 6,
            callbacks: { title: (items) => labels[items[0].dataIndex], label: (c) => ` ${c.dataset.label}: ${c.parsed.y == null ? "–" : fmtNum(c.parsed.y, digits)}${unit}` },
          },
        },
        scales: {
          x: { grid: { display: false }, border: { color: THEME.axis }, ticks: { color: THEME.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 7, font: { size: 11 } } },
          y: { grid: { color: THEME.grid }, border: { display: false }, beginAtZero: !!opts.zero,
            ticks: { color: THEME.muted, font: { size: 11 }, callback: (v) => fmtNum(v, opts.tickDigits ?? (Math.abs(v) >= 100 ? 0 : 1)) } },
        },
      },
    });
  }

  function chartTable(container, labels, datasets, digits = 2, unit = "") {
    fill(container, table(
      [{ label: "日期" }, ...datasets.map((d) => ({ label: d.label, num: true }))],
      labels.map((l, i) => ({ cells: [l, ...datasets.map((d) => (d.data[i] == null ? "–" : fmtNum(d.data[i], digits) + unit))] })),
      { compact: true }));
  }

  // ---------------------------------------------------------------- header / summary
  function renderStatus() {
    const dates = site.dates;
    const last = dates[dates.length - 1];
    const gen = site.generated_at.replace("T", " ").slice(0, 16);
    const stale = (Date.now() - new Date(site.generated_at).getTime()) > 3 * 86400e3;
    $("#live-dot").classList.toggle("stale", stale);
    fill($("#status-line"),
      el("span", null, el("i", { class: stale ? "stale" : "" }), `資料截至 ${last}`),
      el("span", null, `資料產出 ${gen}`),
      el("span", null, `已累積 ${dates.length} 個營業日（${dates[0]} 起）`),
      el("span", null, "每日收盤資料 · 非即時報價"),
      el("span", null, "投信每營業日 16:30 後公告，本站 17:30 抓取"),
    );
    $("#foot-meta").textContent = `site-data schema v${site.schema_version} · 產出時間 ${site.generated_at} · 資料來源：統一投信、富邦投信、臺灣證券交易所`;
  }

  function renderSummary() {
    const cov = site.coverage;
    const comparable = site.etfs.filter((e) => (cov[e.code]?.count || 0) > settings.window).length;
    const pending = site.etfs.length - comparable;
    const iv = computeWindow(site.dates.length - 1);
    const aligned = iv ? iv.participating.length : 0;
    const excluded = iv ? iv.excluded.length : 0;
    const obs = site.etfs.reduce((s, e) => s + (cov[e.code]?.count || 0), 0);
    fill($("#summary-strip"),
      el("div", null, el("span", null, "追蹤標的"), el("strong", null, site.etfs.length, el("small", null, "檔主動式 ETF"))),
      el("div", null, el("span", null, "可比較 / 日期對齊"), el("strong", null, comparable, el("small", null, `檔可比較 · ${aligned} 檔同區間`))),
      el("div", null, el("span", null, "待累積 / 未納入"), el("strong", null, pending, el("small", null, `檔待累積 · ${excluded} 檔未納入`))),
      el("div", null, el("span", null, "資料保存"), el("strong", { class: "text-stat", html: ICON_DB + "持續累積" }, el("small", null, `${obs} 筆每日觀測`))),
    );
  }

  function renderFilters() {
    fill($("#filter-presets"),
      el("button", { type: "button", onclick: () => { selected = new Set(site.etfs.map((e) => e.code)); rerenderFiltered(); } }, `全部 ${site.etfs.length} 檔`),
      ...[...new Set(site.etfs.map(providerName))].map((p) => el("button", { type: "button", onclick: () => { selected = new Set(site.etfs.filter((e) => providerName(e) === p).map((e) => e.code)); rerenderFiltered(); } }, p)),
      el("button", { type: "button", onclick: () => { selected = new Set(); rerenderFiltered(); } }, "清除"),
    );
    fill($("#fund-toggles"), site.etfs.map((e) => {
      const on = selected.has(e.code);
      return el("label", { class: "fund-toggle" + (on ? " checked" : ""), style: `--fund-color:${color[e.code]}` },
        el("input", { type: "checkbox", checked: on || null, onchange: (ev) => { ev.target.checked ? selected.add(e.code) : selected.delete(e.code); rerenderFiltered(); } }),
        el("span", { class: "box", html: ICON_CHECK }), e.code);
    }));
  }

  function rerenderFiltered() { renderFilters(); renderCash(); renderFundGrid(); }

  // ---------------------------------------------------------------- consensus（瀏覽器端依觀察設定即時計算）
  const ADD = new Set(["new", "add"]);
  const REDUCE = new Set(["reduce", "exit"]);

  // 比較全體日期序列上 toIdx 與 toIdx - window 兩天；缺任一天的 ETF 不納入。規則同 analysis/core.py。
  function computeWindow(toIdx) {
    const dates = site.dates;
    const fromIdx = toIdx - settings.window;
    if (fromIdx < 0 || toIdx >= dates.length) return null;
    const from = dates[fromIdx], to = dates[toIdx];
    const cfg = site.config;
    const minPct = settings.strength ?? cfg.min_per_unit_change_pct;
    const participating = [], excluded = [], byStock = {}, names = {};
    for (const e of site.etfs) {
      const h = site.holdings_history[e.code];
      const iTo = h ? h.dates.indexOf(to) : -1, iFrom = h ? h.dates.indexOf(from) : -1;
      if (iTo < 0 || iFrom < 0) { excluded.push(e.code); continue; }
      participating.push(e.code);
      const uTo = h.units[iTo], uFrom = h.units[iFrom];
      for (const [code, st] of Object.entries(h.stocks)) {
        const sp = st.shares[iFrom] || 0, sc = st.shares[iTo] || 0;
        if (sp === sc) continue;                       // 股數沒動就不是經理人決策
        let action, chg = null;
        if (sp && sc) {
          const pp = sp / uFrom, pc = sc / uTo;
          chg = (pc - pp) / pp * 100;
          if (chg > 0 && chg >= minPct) action = "add";
          else if (chg < 0 && -chg >= minPct) action = "reduce";
          else continue;
        } else if (sc) action = "new";
        else { action = "exit"; chg = -100; }
        names[code] = st.name;
        (byStock[code] ||= []).push({ etf: e.code, action, per_unit_change_pct: chg == null ? null : Math.round(chg * 100) / 100, shares_change: sc - sp, ratio: sp && sc ? sc / sp : null });
      }
    }
    const ca = {};
    for (const [code, lst] of Object.entries(byStock)) {
      const rs = lst.filter((x) => x.ratio).map((x) => x.ratio);
      if (rs.length < 2) continue;
      const mn = Math.min(...rs), mx = Math.max(...rs);
      if ((mx - mn) / mn * 100 > cfg.corporate_action_tolerance_pct) continue;
      const mean = rs.reduce((a, b) => a + b, 0) / rs.length;
      if (mean >= cfg.corporate_action_min_ratio || mean <= 1 / cfg.corporate_action_min_ratio) ca[code] = { code, name: names[code], ratio: Math.round(mean * 10000) / 10000, etfs: lst.map((x) => x.etf) };
    }
    const basisKey = (etf) => (settings.basis === "issuers" ? providerName(etfMeta(etf)) : etf);
    const minCount = settings.min ?? cfg.co_signal_min_etfs;
    const signals = (actions) => Object.entries(byStock).filter(([code]) => !ca[code]).map(([code, lst]) => {
      const hits = lst.filter((x) => actions.has(x.action));
      const count = new Set(hits.map((x) => basisKey(x.etf))).size;
      return count >= minCount ? { code, name: names[code], count, etfs: hits.sort((a, b) => a.etf.localeCompare(b.etf)) } : null;
    }).filter(Boolean).sort((a, b) => b.count - a.count || a.code.localeCompare(b.code));
    return { from, to, participating, excluded, co_add: signals(ADD), co_reduce: signals(REDUCE), corporate_actions: Object.values(ca).sort((a, b) => a.code.localeCompare(b.code)) };
  }

  // 標題裡的門檻數字是可直接改的下拉（與觀察設定面板同步）
  function minSelect(cls) {
    const minCount = settings.min ?? site.config.co_signal_min_etfs;
    const maxN = Math.max(2, site.etfs.length);
    const sel = el("select", { class: cls, "aria-label": "共同訊號門檻", onchange: (ev) => { settings.min = Number(ev.target.value); saveSettings(); renderSettings(); renderSummary(); renderConsensus(); } },
      Array.from({ length: maxN - 1 }, (_, i) => el("option", { value: String(i + 2), selected: i + 2 === minCount || null }, String(i + 2))));
    return sel;
  }

  function renderConsensus() {
    const minCount = settings.min ?? site.config.co_signal_min_etfs;
    const unit = settings.basis === "issuers" ? "家投信" : "檔 ETF";
    const dirLabel = settings.direction === "sell" ? "減碼" : settings.direction === "both" ? "加碼／減碼" : "加碼";
    fill($("#consensus-title"), minSelect("inline-select"), ` ${unit} 以上共同${dirLabel}`);
    const body = $("#consensus-body");
    const last = site.dates.length - 1;
    const iv = computeWindow(last);
    if (!iv) {
      $("#consensus-meta").textContent = "";
      fill(body, el("div", { class: "consensus-empty", html: ICON_TREND }, el("div", null, el("b", null, `還沒有足夠的資料日可比較（比較窗口 ${settings.window}）`), el("span", null, "累積更多營業日，或在觀察設定把比較窗口調小。"))));
      return;
    }
    $("#consensus-meta").textContent = `固定比較 ${iv.from} → ${iv.to}`;
    const signalList = (list) => el("div", { class: "signal-list" }, list.map((s) => el("div", { class: "signal-item" },
      el("span", { class: "stock" }, el("code", null, s.code), s.name || ""),
      el("span", { class: "count" }, `${s.count} ${settings.basis === "issuers" ? "家" : "檔"}`),
      el("a", { href: `#stock/${s.code}` }, "反查"),
      el("span", { class: "etfs" }, s.etfs.map((x) => pill(x.etf, `${ACTION_LABEL[x.action]}${x.per_unit_change_pct != null ? " " + signed(x.per_unit_change_pct) + "%" : ""}`, x.action))),
    )));
    const empty = (what) => el("div", { class: "consensus-empty", html: ICON_TREND }, el("div", null, el("b", null, `目前沒有符合門檻的共同${what}`), el("span", null, "只使用相同前後資料日的完整快照；缺日基金不會混入訊號。")));
    const showBuy = settings.direction !== "sell", showSell = settings.direction !== "buy";
    const strength = settings.strength ?? site.config.min_per_unit_change_pct;
    const history = [];
    for (let i = last - 1; i >= 0 && history.length < 6; i--) { const w = computeWindow(i); if (w) history.push(w); }
    fill(body,
      el("div", { class: "coverage-row" },
        el("b", null, `${iv.participating.length} / ${site.etfs.length} 檔日期對齊`),
        el("span", null, `參與：${iv.participating.join("、")}`),
        iv.excluded.length ? el("span", null, `未納入：${iv.excluded.join("、")}`) : el("span", null, "無缺日"),
        el("span", null, `窗口 ${settings.window} 個資料日 · 強度 ≥ ${strength}%`),
      ),
      showBuy ? el("div", { class: "signal-group" }, el("h3", null, el("span", { class: "tag-buy" }, "加碼"), `${minCount} ${unit} 以上同時建倉／加碼`), iv.co_add.length ? signalList(iv.co_add) : empty("加碼")) : null,
      showSell ? el("div", { class: "signal-group" }, el("h3", null, el("span", { class: "tag-sell" }, "減碼"), `${minCount} ${unit} 以上同時減碼／出清`), iv.co_reduce.length ? signalList(iv.co_reduce) : empty("減碼")) : null,
      iv.corporate_actions.length ? el("div", { class: "signal-group" }, el("h3", null, "疑似公司行動（待核對）"), el("ul", null, iv.corporate_actions.map((c) => el("li", null, `${c.code} ${c.name || ""}：股數 ×${c.ratio}（${c.etfs.join("、")}）`)))) : null,
      el("p", { class: "footnote" }, `「每單位」用持有股數除以基金流通單位數，協助排除申購買回造成的規模效果。加減碼需股數確實改變且每單位變動超過強度門檻。多檔出現一致倍數變化時標為公司行動待核對。權重上升本身不算買進。`),
      el("details", { class: "signal-history" },
        el("summary", null, `歷史符合紀錄（最近 ${history.length} 個區間）`),
        el("div", null, history.map((h) => el("div", null,
          el("b", null, `${h.from} → ${h.to}`),
          showBuy ? el("span", null, `共同加碼 ${h.co_add.length}`) : null, showSell ? el("span", null, `共同減碼 ${h.co_reduce.length}`) : null,
          h.excluded.length ? el("span", null, `缺：${h.excluded.join("、")}`) : null,
          (showBuy ? h.co_add : []).concat(showSell ? h.co_reduce : []).map((s) => el("span", null, el("code", null, s.code), ` ${s.name || ""} ×${s.count}`)),
        )), history.length ? null : el("p", { class: "empty" }, "尚無更早的區間。")),
      ),
    );
  }

  // ---------------------------------------------------------------- settings panel
  function renderSettings() {
    const cfg = site.config;
    const minSel = $("#setting-min");
    const maxN = Math.max(2, site.etfs.length);
    fill(minSel, Array.from({ length: maxN - 1 }, (_, i) => el("option", { value: String(i + 2) }, String(i + 2))));
    const current = { window: settings.window, basis: settings.basis, min: settings.min ?? cfg.co_signal_min_etfs, direction: settings.direction, strength: settings.strength ?? cfg.min_per_unit_change_pct };
    for (const sel of document.querySelectorAll("#settings-panel select[data-setting]")) {
      const k = sel.dataset.setting;
      if (![...sel.options].some((o) => o.value === String(current[k]))) sel.append(el("option", { value: String(current[k]) }, String(current[k])));
      sel.value = String(current[k]);
    }
    fill($("#settings-checks"), SECTIONS.map(([k, label]) => el("label", null,
      el("input", { type: "checkbox", checked: showSection(k) || null, onchange: (ev) => { settings.show[k] = ev.target.checked; saveSettings(); applySections(); } }), label)));
    applySections();
  }
  function applySections() {
    $("#signals").toggleAttribute("data-hidden-section", !showSection("signals"));
    $("#cash").toggleAttribute("data-hidden-section", !showSection("cash"));
    const holdingsTab = $("#detail-tabs button[data-tab=holdings]");
    holdingsTab.toggleAttribute("data-hidden-section", !showSection("holdings"));
    if (!showSection("holdings") && detailTab === "holdings") { detailTab = "price"; if (site) renderDetail(); }
    for (const [k] of SECTIONS) { const box = document.getElementById(k + "-box"); if (box) box.toggleAttribute("data-hidden-section", !showSection(k)); }
  }
  function onSettingChange(ev) {
    const k = ev.target.dataset.setting, v = ev.target.value;
    if (k === "window" || k === "min") settings[k] = Number(v);
    else if (k === "strength") settings[k] = Number(v);
    else settings[k] = v;
    saveSettings();
    renderSummary(); renderConsensus();
  }

  // ---------------------------------------------------------------- cash
  function renderCash() {
    const etfs = activeEtfs();
    const labels = lastN(site.dates);
    const datasets = etfs.map((e) => {
      const byDate = Object.fromEntries((site.series[e.code] || []).map((s) => [s.date, s.non_stock_pct]));
      return { label: e.code, data: labels.map((d) => byDate[d] ?? null), color: color[e.code] };
    });
    if (datasets.length) makeChart("cash-chart", "line", labels, datasets, { unit: "%", legend: true, zero: true, tickDigits: 1 });
    else if (charts["cash-chart"]) { charts["cash-chart"].destroy(); delete charts["cash-chart"]; }
    chartTable($("#cash-chart-table"), labels, datasets, 2, "%");
    fill($("#cash-ranking"), etfs.length ? site.overview.filter((o) => selected.has(o.etf)).sort((a, b) => (b.non_stock_pct ?? -1) - (a.non_stock_pct ?? -1)).map((o) => el("div", { class: "cash-rank" },
      el("span", { class: "code" }, el("i", { class: "dot", style: `--fund-color:${color[o.etf]}` }), o.etf),
      el("span", { class: "date" }, o.date + (o.cash_pct == null ? " · 非股票部位" : "")),
      el("span", { class: "value" }, fmtNum(o.non_stock_pct), el("small", null, "%")),
      el("span", { class: "delta" }, o.non_stock_pct_change == null ? "–" : `${signed(o.non_stock_pct_change)} 個百分點`, o.cash_pct != null ? ` · 揭露現金 ${fmtPct(o.cash_pct)}` : ""),
    )) : el("p", { class: "empty" }, "請在上方勾選至少一檔 ETF。"));
  }

  // ---------------------------------------------------------------- stock explorer
  function stockOptions() {
    fill($("#stock-list"), Object.values(site.stocks).map((s) => el("option", { value: `${s.code} ${s.name || ""}` })));
  }
  function findStock(q) {
    q = (q || "").trim();
    if (!q) return null;
    const code = q.split(/\s+/)[0];
    if (site.stocks[code]) return site.stocks[code];
    const lower = q.toLowerCase();
    const all = Object.values(site.stocks);
    return all.find((s) => (s.name || "").toLowerCase() === lower) || all.find((s) => (s.name || "").toLowerCase().includes(lower) || s.code.includes(q)) || null;
  }
  const lastChange = (lc) => !lc ? el("span", { class: "flat" }, "近期無異動") : el("span", null, actionPill(lc.action), ` ${lc.date}`, lc.per_unit_change_pct != null ? ` ${signed(lc.per_unit_change_pct)}%` : "", lc.shares_change ? `（${lc.shares_change > 0 ? "+" : ""}${fmtInt(lc.shares_change)} 股）` : "");

  function renderStock(q) {
    const box = $("#stock-result");
    if (!q) {
      fill(box, el("div", { class: "consensus-empty", style: "margin-top:16px" }, el("div", null, el("b", null, "搜尋一檔股票"), el("span", null, "可查看哪些主動式 ETF 持有、最新權重、股數與規模調整後的每單位變化。"))));
      return;
    }
    const s = findStock(q);
    if (!s) { fill(box, el("p", { class: "empty" }, `找不到「${q}」。目前只收錄追蹤中的 ETF 持有過的股票。`)); return; }
    $("#stock-q").value = `${s.code} ${s.name || ""}`;
    fill(box, el("div", { class: "stock-hit" },
      el("h3", null, el("code", null, s.code), s.name || "", el("span", { class: "count", style: "padding:2px 9px;border-radius:999px;background:#e7f2f3;color:#1d505a;font-size:11px;font-weight:700" }, `${s.etf_count} 檔持有`)),
      s.held_by.length ? table([{ label: "ETF" }, { label: "權重", num: true }, { label: "股數", num: true }, { label: "每千單位股數", num: true }, { label: "市值（億）", num: true }, { label: "最近異動" }, { label: "" }],
        s.held_by.map((h) => ({ cells: [pill(h.etf, etfMeta(h.etf).name), fmtPct(h.weight), fmtInt(h.shares), per1k(h.per_unit), fmtYi(h.amount), lastChange(h.last_change), el("a", { href: `#etf/${h.etf}` }, "持股表")] })))
        : el("p", { class: "empty" }, "目前沒有 ETF 持有。"),
      s.exited_by && s.exited_by.length ? el("p", { class: "footnote" }, "已出清：", s.exited_by.map((x) => el("span", { style: "margin-right:10px" }, pill(x.etf), ` ${x.date}`))) : null,
    ));
  }

  // ---------------------------------------------------------------- fund cards
  function renderFundGrid() {
    fill($("#fund-grid"), site.overview.filter((o) => selected.has(o.etf)).map((o) => {
      const e = etfMeta(o.etf);
      return el("article", { class: "fund-card" + (o.etf === detailEtf ? " focused" : ""), style: `--fund-color:${color[o.etf]}` },
        el("div", { class: "fund-card-top" }, el("span", { class: "code" }, o.etf), e.tag ? el("span", { class: "style-tag" }, e.tag) : null, el("span", { class: "issuer-badge" }, providerName(e))),
        el("h3", null, o.name),
        el("p", { class: "issuer" }, e.full_name || providerName(e)),
        el("p", { class: "goal" }, e.description || ""),
        el("div", { class: "card-numbers" },
          el("div", null, el("span", null, "收盤價"), el("b", null, fmtNum(o.close)), el("small", { class: "date" }, o.date)),
          el("div", null, el("span", null, "現金／非股票"), el("b", null, fmtNum(o.non_stock_pct), el("small", null, "%")), el("small", { class: "date" }, o.date)),
          el("div", null, el("span", null, "持股檔數"), el("b", null, String(o.holdings_count)), el("small", { class: "date" }, o.latest_changes ? summarizeChanges(o.latest_changes) : "")),
        ),
        el("div", { class: "top-holdings" }, "主要持股 ", el("span", null, o.top_holdings.map((h) => h.name).join("、"))),
        el("button", { class: "card-link", type: "button", html: "查看走勢與配置 " + ICON_UP, onclick: () => { location.hash = `#etf/${o.etf}`; } }),
      );
    }));
    if (!selected.size) fill($("#fund-grid"), el("p", { class: "empty" }, "請在上方勾選至少一檔 ETF。"));
  }
  function summarizeChanges(c) {
    const parts = ACTION_ORDER.filter((k) => c[k]).map((k) => `${ACTION_LABEL[k]} ${c[k]}`);
    return parts.length ? parts.join("、") : "無異動";
  }

  // ---------------------------------------------------------------- detail
  function renderDetail() {
    const code = detailEtf;
    const e = etfMeta(code);
    const d = site.etf_detail[code];
    const ov = site.overview.find((o) => o.etf === code) || {};
    fill($("#detail-title"), el("code", null, code), " ", el("span", { class: "sub" }, e.name));
    fill($("#detail-select"), site.etfs.map((x) => el("option", { value: x.code, selected: x.code === code || null }, `${x.code} ${x.name}`)));
    fill($("#detail-direction"), e.description || "", e.info_url ? [" ", el("a", { href: e.info_url, target: "_blank", rel: "noreferrer" }, "投信資料 ↗")] : null);
    for (const b of $("#detail-tabs").querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.tab === detailTab));
    const body = $("#detail-body");
    if (!d) { fill(body, el("p", { class: "empty" }, "沒有這檔的資料。")); return; }
    const c = color[code];
    const series = lastN(site.series[code] || []);
    const prices = lastN(site.price_series[code] || []);
    const sLabels = series.map((s) => s.date);
    const pLabels = prices.map((p) => p.date);

    const metric = (id, title, value, unit, note) => el("div", { class: "metric-box", id: id + "-box", "data-hidden-section": showSection(id) ? null : "" },
      el("div", { class: "metric-head" }, el("h3", null, title), el("span", null, value, unit ? el("small", null, unit) : null)),
      el("div", { class: "chart" }, el("canvas", { id })), note ? el("p", { class: "footnote", style: "margin-top:8px" }, note) : null);
    const latestS = series[series.length - 1] || {};

    if (detailTab === "price") {
      const last = prices[prices.length - 1] || {};
      const pctChange = prices.map((p, i) => (i > 0 && prices[i - 1].close && p.close != null ? (p.close / prices[i - 1].close - 1) * 100 : null));
      fill(body, el("div", { class: "metrics-grid" },
        metric("m-close", "收盤價", fmtNum(ov.close), "元"),
        metric("m-nav", "基金淨值", fmtNum(ov.nav), "元"),
        metric("m-vol", "成交量", fmtYi(last.volume, 2), "億股"),
        metric("m-chg", "每日漲跌幅", signed(ov.close_change_pct), "%"),
        metric("m-prem", "折溢價", signed(ov.premium_pct), "%"),
        metric("m-aum", "基金規模", fmtYi(ov.aum, 1), "億元"),
      ));
      makeChart("m-close", "line", pLabels, [{ label: "收盤價", data: prices.map((p) => p.close), color: c }], { unit: " 元" });
      makeChart("m-nav", "line", sLabels, [{ label: "淨值", data: series.map((s) => s.nav), color: c }, { label: "收盤價", data: series.map((s) => s.close), color: "#9fb0b5", dash: [4, 3] }], { unit: " 元", legend: true });
      makeChart("m-vol", "bar", pLabels, [{ label: "成交量（億股）", data: prices.map((p) => (p.volume == null ? null : p.volume / 1e8)), color: c }], { unit: " 億股", zero: true });
      makeChart("m-chg", "bar", pLabels, [{ label: "漲跌幅", data: pctChange, color: c }], { unit: "%" });
      makeChart("m-prem", "line", sLabels, [{ label: "折溢價", data: series.map((s) => s.premium_pct), color: c }], { unit: "%" });
      makeChart("m-aum", "line", sLabels, [{ label: "規模（億元）", data: series.map((s) => (s.aum == null ? null : s.aum / 1e8)), color: c }], { unit: " 億元", tickDigits: 0 });
    } else if (detailTab === "flow") {
      const items = d.non_stock_items || [];
      fill(body,
        el("div", { class: "metrics-grid" },
          metric("m-units", "流通在外單位數", fmtYi(ov.outstanding_units, 2), "億單位", ov.units_change != null ? `最新一日變動 ${ov.units_change > 0 ? "+" : ""}${fmtInt(ov.units_change)} 單位` : null),
          metric("m-cash", "非股票水位", fmtNum(ov.non_stock_pct), "%", "非股票水位 =（淨資產 − 股票市值）÷ 淨資產；揭露現金為投信公告項目。"),
          metric("m-top10", "前十大集中度", fmtNum(latestS.top10_weight), "%", "前十大持股權重合計，越高代表配置越集中。"),
        ),
        el("h3", { class: "sub-head" }, `非股票部位明細（${d.date}）`),
        items.length ? el("dl", { class: "kv" }, items.flatMap((i) => [el("dt", null, i.name), el("dd", null, fmtYi(i.amount, 2) + " 億")]), el("dt", null, "非股票合計（淨資產 − 股票市值）"), el("dd", null, fmtYi(ov.non_stock_total, 2) + " 億")) : el("p", { class: "empty" }, "此日投信未揭露明細。"),
        d.futures.length ? [el("h3", { class: "sub-head" }, "期貨部位"), table([{ label: "代號" }, { label: "名稱" }, { label: "口數", num: true }, { label: "契約月" }, { label: "名目本金（億）", num: true }, { label: "占淨資產", num: true }],
          d.futures.map((f) => ({ cells: [f.code, f.name, fmtInt(f.contracts), f.month || "–", fmtYi(f.amount, 2), fmtPct(f.weight)] })))] : null,
      );
      makeChart("m-units", "line", sLabels, [{ label: "單位數（億）", data: series.map((s) => (s.outstanding_units == null ? null : s.outstanding_units / 1e8)), color: c }], { unit: " 億", tickDigits: 1 });
      makeChart("m-cash", "line", sLabels, [{ label: "非股票水位", data: series.map((s) => s.non_stock_pct), color: c }, { label: "揭露現金", data: series.map((s) => s.cash_pct), color: "#9fb0b5", dash: [4, 3] }], { unit: "%", legend: true, zero: true, tickDigits: 1 });
      makeChart("m-top10", "line", sLabels, [{ label: "前十大權重合計", data: series.map((s) => s.top10_weight), color: c }], { unit: "%", tickDigits: 0 });
    } else if (detailTab === "holdings") {
      const holdings = [...d.holdings].sort((a, b) => {
        const x = a[holdingsSort.key], y = b[holdingsSort.key];
        if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * holdingsSort.dir;
      });
      const cols = [["code", "代號"], ["name", "名稱"], ["shares", "股數", 1], ["per_unit", "每千單位股數", 1], ["amount", "市值（億）", 1], ["weight", "權重", 1]];
      const thead = el("thead", null, el("tr", null, cols.map(([k, label, num]) => el("th", { class: "sortable" + (num ? " num" : ""), onclick: () => { holdingsSort = { key: k, dir: holdingsSort.key === k ? -holdingsSort.dir : (num ? -1 : 1) }; renderDetail(); } }, label + (holdingsSort.key === k ? (holdingsSort.dir > 0 ? " ▲" : " ▼") : "")))));
      const tbody = el("tbody", null, holdings.map((h) => el("tr", null, el("td", null, el("a", { href: `#stock/${h.code}` }, h.code)), el("td", null, h.name), el("td", { class: "num" }, fmtInt(h.shares)), el("td", { class: "num" }, per1k(h.per_unit)), el("td", { class: "num" }, fmtYi(h.amount, 2)), el("td", { class: "num" }, fmtPct(h.weight)))));
      const changes = d.changes.slice(0, 20).map((ch) => el("div", { class: "changes-day" },
        el("h4", null, `${ch.prev_date} → ${ch.date}`, ch.items.length ? "" : "　無異動"),
        ch.items.length ? table([{ label: "動作" }, { label: "代號" }, { label: "名稱" }, { label: "股數 前 → 後", num: true }, { label: "每單位變動", num: true }, { label: "權重 前 → 後", num: true }],
          ch.items.map((it) => ({ cells: [actionPill(it.action), el("a", { href: `#stock/${it.code}` }, it.code), it.name, `${fmtInt(it.shares_prev)} → ${fmtInt(it.shares_curr)}`, it.per_unit_change_pct == null ? "–" : signed(it.per_unit_change_pct) + "%", `${it.weight_prev == null ? "–" : fmtPct(it.weight_prev)} → ${it.weight_curr == null ? "–" : fmtPct(it.weight_curr)}`] }))) : null));
      fill(body,
        el("h3", { class: "sub-head" }, `持股明細（${d.holdings.length} 檔，資料日 ${d.date}，點欄位標題排序）`),
        el("div", { class: "table-wrap compact" }, el("table", null, thead, tbody)),
        el("h3", { class: "sub-head" }, "每日異動（最近 20 個區間）"),
        changes.length ? changes : el("p", { class: "empty" }, "只有一個資料日，還沒有可比較的異動。"),
      );
    } else {
      const n = site.notes;
      fill(body, el("dl", { class: "kv", style: "grid-template-columns: max-content 1fr" },
        el("dt", null, "資料來源"), el("dd", { style: "font-family:var(--font)" }, providerName(e) + "官網每日公告（持股、淨值、單位數、現金明細）；收盤價與成交量來自臺灣證券交易所。"),
        el("dt", null, "每單位持股數"), el("dd", { style: "font-family:var(--font)" }, n.per_unit),
        el("dt", null, "現金水位"), el("dd", { style: "font-family:var(--font)" }, n.non_stock_pct),
        el("dt", null, "折溢價"), el("dd", { style: "font-family:var(--font)" }, n.prices),
        el("dt", null, "資料涵蓋"), el("dd", { style: "font-family:var(--font)" }, `${site.coverage[code].first} 至 ${site.coverage[code].last}，共 ${site.coverage[code].count} 個營業日` + (site.coverage[code].missing.length ? `，缺日：${site.coverage[code].missing.join("、")}` : "")),
      ));
    }
    renderFundGrid();
  }

  // ---------------------------------------------------------------- about
  function renderAbout() {
    const n = site.notes, cov = site.coverage;
    fill($("#about-body"),
      el("dl", { class: "kv" },
        el("dt", null, "資料來源"), el("dd", { style: "font-family:var(--font)" }, "統一投信申購買回清單與基金投資組合、富邦投信 ETF 投資網基金資產、臺灣證券交易所每日收盤。投信每營業日 16:30 後公告當天持股，本站於 17:30 抓取，21:00 補抓。"),
        el("dt", null, "每單位持股數"), el("dd", { style: "font-family:var(--font)" }, n.per_unit),
        el("dt", null, "現金水位"), el("dd", { style: "font-family:var(--font)" }, n.non_stock_pct),
        el("dt", null, "共同訊號"), el("dd", { style: "font-family:var(--font)" }, n.intervals),
        el("dt", null, "公司行動"), el("dd", { style: "font-family:var(--font)" }, n.corporate_action),
        el("dt", null, "行情"), el("dd", { style: "font-family:var(--font)" }, n.prices),
        el("dt", null, "門檻設定"), el("dd", { style: "font-family:var(--font)" }, `共同訊號 ${site.config.co_signal_min_etfs} 檔以上；每單位變動 ≥ ${site.config.min_per_unit_change_pct}%；公司行動倍數 ≥ ${site.config.corporate_action_min_ratio}（誤差 ${site.config.corporate_action_tolerance_pct}%）`),
      ),
      el("h3", { class: "sub-head" }, "資料涵蓋"),
      table([{ label: "ETF" }, { label: "首日" }, { label: "最新" }, { label: "天數", num: true }, { label: "缺日" }, { label: "行情天數", num: true }],
        site.etfs.map((e) => ({ cells: [pill(e.code, e.name), cov[e.code]?.first || "–", cov[e.code]?.last || "–", String(cov[e.code]?.count ?? 0), (cov[e.code]?.missing || []).join("、") || "無", String(site.prices_coverage?.[e.code]?.count ?? 0)] }))),
    );
  }

  // ---------------------------------------------------------------- routing / boot
  function route() {
    const [kind, arg] = location.hash.slice(1).split("/");
    if (kind === "etf" && arg && site.etf_detail[arg]) { detailEtf = arg; renderDetail(); $("#detail").scrollIntoView({ behavior: "smooth" }); return; }
    if (kind === "stock" && arg) { renderStock(decodeURIComponent(arg)); $("#stock").scrollIntoView({ behavior: "smooth" }); return; }
    if (kind) { const s = document.getElementById(kind); if (s) s.scrollIntoView({ behavior: "smooth" }); }
  }

  function renderAll() {
    color = Object.fromEntries(site.etfs.map((e, i) => [e.code, e.color || FALLBACK_COLORS[i % FALLBACK_COLORS.length]]));
    if (!selected.size) selected = new Set(site.etfs.map((e) => e.code));
    if (!detailEtf || !site.etf_detail[detailEtf]) detailEtf = site.etfs[0].code;
    renderStatus(); renderSettings(); renderSummary(); renderFilters(); renderConsensus(); renderCash(); stockOptions(); renderStock(""); renderFundGrid(); renderDetail(); renderAbout();
    route();
  }

  function toggleSettings(open) {
    const panel = $("#settings-panel");
    const show = open ?? panel.hidden;
    panel.hidden = !show;
    $("#settings-toggle").setAttribute("aria-expanded", String(show));
    if (show) panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  loadSettings();
  $("#settings-toggle").addEventListener("click", () => toggleSettings());
  $("#settings-close").addEventListener("click", () => toggleSettings(false));
  for (const sel of document.querySelectorAll("#settings-panel select[data-setting]")) sel.addEventListener("change", onSettingChange);

  function load() {
    return fetch(DATA_URL + (DATA_URL.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((data) => { site = data; $("#error").hidden = true; renderAll(); })
      .catch((e) => { const p = $("#error"); p.hidden = false; p.textContent = `讀不到資料檔 ${DATA_URL}：${e.message}。請先執行 python analyze.py 產生 data/site-data.json。`; });
  }

  $("#stock-form").addEventListener("submit", (ev) => { ev.preventDefault(); const q = $("#stock-q").value.trim(); if (q) location.hash = `#stock/${encodeURIComponent(q.split(/\s+/)[0])}`; renderStock(q); });
  $("#period").addEventListener("change", (ev) => { period = ev.target.value; renderCash(); renderDetail(); });
  $("#detail-select").addEventListener("change", (ev) => { location.hash = `#etf/${ev.target.value}`; });
  $("#detail-tabs").addEventListener("click", (ev) => { const b = ev.target.closest("button[data-tab]"); if (b) { detailTab = b.dataset.tab; renderDetail(); } });
  $("#reload").addEventListener("click", load);
  window.addEventListener("hashchange", route);
  load();
})();
