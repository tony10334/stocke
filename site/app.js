/* 主動式 ETF 觀測站 — 前端（純靜態，讀 data/site-data.json） */
(function () {
  "use strict";

  const DATA_URL = new URLSearchParams(location.search).get("data") || "../data/site-data.json";
  const ACTION_LABEL = { new: "建倉", add: "加碼", reduce: "減碼", exit: "出清", corporate_action: "疑似公司行動" };
  const ACTION_ORDER = ["new", "add", "reduce", "exit", "corporate_action"];
  const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4"];

  let site = null;          // site-data.json
  let seriesVar = {};       // etf -> css var（固定順序，不隨篩選改色）
  const charts = {};        // id -> Chart

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root) => (root || document).querySelector(sel);
  // replaceChildren 會把 null 變成 "null" 文字，一律先過濾
  const fill = (node, ...children) => node.replaceChildren(...children.flat().filter((c) => c != null && c !== false));
  const el = (tag, attrs, ...children) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null) continue;
      if (k === "class") n.className = v;
      else if (k === "style") n.setAttribute("style", v);
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "html") n.innerHTML = v;
      else n.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      n.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return n;
  };
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtInt = (v) => (v == null ? "–" : Math.round(v).toLocaleString("zh-Hant-TW"));
  const fmtNum = (v, d = 2) => (v == null ? "–" : Number(v).toLocaleString("zh-Hant-TW", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const fmtPct = (v, d = 2) => (v == null ? "–" : fmtNum(v, d) + "%");
  const fmtBillion = (v) => (v == null ? "–" : fmtNum(v / 1e8, 1) + " 億");
  const per1k = (pu) => (pu == null ? "–" : fmtNum(pu * 1000, 3));
  const clsDelta = (v) => (v == null || v === 0 ? "flat" : v > 0 ? "up" : "down");
  const arrow = (v) => (v == null || v === 0 ? "" : v > 0 ? "▲" : "▼");
  // 漲跌：正負號 + 箭頭 + 顏色（台股慣例漲紅跌綠）
  const delta = (v, fmt, suffix = "") => {
    if (v == null) return el("span", { class: "flat" }, "–");
    const s = (v > 0 ? "+" : "") + fmt(v) + suffix;
    return el("span", { class: clsDelta(v) }, arrow(v) + " " + s);
  };
  const etfName = (code) => (site.etfs.find((e) => e.code === code) || {}).name || code;
  const etfBadge = (code, extra) =>
    el("span", { class: "badge etf", style: `--series: var(${seriesVar[code]})` }, code, extra ? ` ${extra}` : "");
  const actionBadge = (action) => el("span", { class: "badge " + action }, ACTION_LABEL[action] || action);

  function table(headers, rows, opts = {}) {
    const thead = el("thead", null, el("tr", null, headers.map((h) => el("th", { class: h.num ? "num" : null }, h.label))));
    const tbody = el("tbody", null, rows.map((r) => el("tr", { class: r.cls }, r.cells.map((c, i) =>
      el("td", { class: headers[i].num ? "num" : null }, c)))));
    return el("div", { class: "table-wrap" + (opts.compact ? " compact" : "") }, el("table", null, thead, tbody));
  }

  // ---------------------------------------------------------------- charts
  function chartTheme() {
    return { ink: cssVar("--ink-2"), muted: cssVar("--muted"), grid: cssVar("--grid"), axis: cssVar("--axis"), surface: cssVar("--surface") };
  }

  function lineChart(id, labels, datasets, opts = {}) {
    const t = chartTheme();
    if (charts[id]) charts[id].destroy();
    const ctx = document.getElementById(id);
    if (!ctx) return;
    charts[id] = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: datasets.map((d) => ({
          label: d.label,
          data: d.data,
          borderColor: cssVar(d.colorVar),
          backgroundColor: cssVar(d.colorVar),
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHitRadius: 12,
          tension: 0,
          spanGaps: true,
          borderDash: d.dash || undefined,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: datasets.length > 1, labels: { color: t.ink, boxWidth: 12, boxHeight: 2 } },
          tooltip: {
            backgroundColor: t.surface, titleColor: t.ink, bodyColor: t.ink, borderColor: t.axis, borderWidth: 1,
            callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y == null ? "–" : fmtNum(c.parsed.y, opts.digits ?? 2)}${opts.unit || ""}` },
          },
        },
        scales: {
          x: { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
          y: {
            grid: { color: t.grid }, border: { display: false },
            ticks: { color: t.muted, callback: (v) => fmtNum(v, opts.digits ?? 2) + (opts.unit || "") },
            beginAtZero: !!opts.zero,
          },
        },
      },
    });
  }

  // 每張圖的表格檢視（無障礙 / 列印）
  function chartTable(container, labels, datasets, digits = 2, unit = "") {
    container.replaceChildren(table(
      [{ label: "日期" }, ...datasets.map((d) => ({ label: d.label, num: true }))],
      labels.map((l, i) => ({ cells: [l, ...datasets.map((d) => (d.data[i] == null ? "–" : fmtNum(d.data[i], digits) + unit))] })),
      { compact: true }
    ));
  }

  // ---------------------------------------------------------------- sections
  function renderMeta() {
    const dates = site.dates;
    $("#site-meta").textContent = `追蹤 ${site.etfs.length} 檔 · 資料 ${dates[0]} 至 ${dates[dates.length - 1]}（${dates.length} 個營業日）· 產出 ${site.generated_at.replace("T", " ").slice(0, 16)}`;
    $("#foot-meta").textContent = `site-data schema v${site.schema_version} · 產出時間 ${site.generated_at}`;
  }

  function renderCards() {
    const cards = site.overview.map((o) => {
      const stat = (k, v, d) => el("div", { class: "stat" }, el("div", { class: "k" }, k), el("div", { class: "v num" }, v), d ? el("div", { class: "d" }, d) : null);
      return el("article", { class: "card", style: `--series: var(${seriesVar[o.etf]})` },
        el("h3", null, el("span", null, `${o.etf} ${o.name}`), el("small", null, o.provider === "uni" ? "統一投信" : o.provider === "fubon" ? "富邦投信" : o.provider)),
        el("div", { class: "date" }, `資料日 ${o.date}${o.prev_date ? `（對比 ${o.prev_date}）` : ""}`),
        el("div", { class: "stats" },
          stat("收盤價", fmtNum(o.close), o.close_change != null ? delta(o.close_change, (v) => fmtNum(v), o.close_change_pct != null ? `（${fmtPct(o.close_change_pct)}）` : "") : "無行情"),
          stat("淨值", fmtNum(o.nav), delta(o.nav_change, (v) => fmtNum(v))),
          stat("折溢價", fmtPct(o.premium_pct), o.premium_pct_change != null ? delta(o.premium_pct_change, (v) => fmtNum(v), " 點") : null),
          stat("非股票水位", fmtPct(o.non_stock_pct), el("span", null, delta(o.non_stock_pct_change, (v) => fmtNum(v), " 點"), o.cash_pct != null ? ` · 揭露現金 ${fmtPct(o.cash_pct)}` : "")),
          stat("持股檔數", String(o.holdings_count), o.latest_changes ? summarizeChanges(o.latest_changes) : null),
          stat("基金規模", fmtBillion(o.aum), o.units_change != null ? el("span", null, "單位數 ", delta(o.units_change, fmtInt)) : null),
        ),
        el("div", { class: "top3" }, o.top_holdings.map((h) => el("span", { class: "chip" }, el("b", null, h.code), ` ${h.name} ${fmtPct(h.weight, 1)}`))),
        el("div", { class: "more" }, el("a", { href: `#etf/${o.etf}` }, "看持股與異動 →")),
      );
    });
    $("#cards").replaceChildren(...cards);
  }

  function summarizeChanges(c) {
    const parts = ACTION_ORDER.filter((k) => c[k]).map((k) => `${ACTION_LABEL[k]} ${c[k]}`);
    return parts.length ? "最新異動：" + parts.join("、") : "最新無異動";
  }

  function signalBlock(title, list) {
    if (!list.length) return el("div", null, el("h4", { style: "margin:8px 0 4px" }, title), el("p", { class: "empty" }, "無"));
    return el("div", null, el("h4", { style: "margin:8px 0 4px" }, title),
      el("div", { class: "signal-list" }, list.map((s) => el("div", { class: "signal" },
        el("div", { class: "head" }, el("b", null, `${s.code} ${s.name || ""}`), el("span", { class: "chip" }, `${s.count} 檔`),
          el("a", { href: `#stock/${s.code}` }, "反查")),
        el("div", { class: "etfs" }, s.etfs.map((x) => etfBadge(x.etf, `${ACTION_LABEL[x.action]}${x.per_unit_change_pct != null ? " " + (x.per_unit_change_pct > 0 ? "+" : "") + fmtPct(x.per_unit_change_pct) : ""}`))),
      ))));
  }

  function renderSignals() {
    const ivs = site.intervals;
    const cfg = site.config;
    $("#signals-hint").textContent = `門檻：${cfg.co_signal_min_etfs} 檔以上在同一區間對同一股票建倉／加碼；每單位持股數變動需超過 ${cfg.min_per_unit_change_pct}% 且股數確實改變。只比較相鄰兩個資料日，缺日的 ETF 不納入該區間。`;
    if (!ivs.length) { $("#signals-latest").replaceChildren(el("p", { class: "empty" }, "還沒有兩個以上的資料日可比較。")); return; }
    const latest = ivs[0];
    fill($("#signals-latest"),
      el("p", { class: "hint" }, `最新區間 ${latest.from} → ${latest.to}，參與：${latest.etfs.join("、")}${latest.excluded.length ? `；缺日未納入：${latest.excluded.join("、")}` : ""}`),
      signalBlock("共同加碼", latest.co_add),
      signalBlock("共同減碼", latest.co_reduce),
      latest.corporate_actions.length ? el("div", null, el("h4", { style: "margin:8px 0 4px" }, "疑似公司行動（待核對）"),
        el("ul", null, latest.corporate_actions.map((c) => el("li", null, `${c.code} ${c.name || ""}：股數 ×${c.ratio}（${c.etfs.join("、")}）`)))) : null,
    );
    const hist = ivs.slice(1);
    $("#signals-history").replaceChildren(hist.length ? el("div", null, hist.map((iv) => el("details", null,
      el("summary", null, `${iv.from} → ${iv.to}　共同加碼 ${iv.co_add.length}、共同減碼 ${iv.co_reduce.length}、疑似公司行動 ${iv.corporate_actions.length}${iv.excluded.length ? `　（缺：${iv.excluded.join("、")}）` : ""}`),
      signalBlock("共同加碼", iv.co_add), signalBlock("共同減碼", iv.co_reduce),
    ))) : el("p", { class: "empty" }, "尚無更早的區間。"));
  }

  function renderCash() {
    const rows = site.overview.map((o) => ({ cells: [
      etfBadge(o.etf, o.name), fmtPct(o.non_stock_pct), delta(o.non_stock_pct_change, (v) => fmtNum(v), " 點"),
      o.cash_pct != null ? fmtPct(o.cash_pct) : "–", fmtBillion(o.non_stock_total), fmtBillion(o.aum), o.date,
    ] }));
    $("#cash-table").replaceChildren(table(
      [{ label: "ETF" }, { label: "非股票水位", num: true }, { label: "日變化", num: true }, { label: "揭露現金", num: true }, { label: "非股票部位", num: true }, { label: "基金淨資產", num: true }, { label: "資料日" }],
      rows));
    const labels = site.dates;
    const datasets = site.etfs.filter((e) => site.series[e.code]).map((e) => {
      const byDate = Object.fromEntries(site.series[e.code].map((s) => [s.date, s.non_stock_pct]));
      return { label: `${e.code} ${e.name}`, data: labels.map((d) => byDate[d] ?? null), colorVar: seriesVar[e.code] };
    });
    lineChart("cash-chart", labels, datasets, { unit: "%", digits: 2, zero: true });
    chartTable($("#cash-chart-table"), labels, datasets, 2, "%");
  }

  // ---------------------------------------------------------------- stock lookup
  function stockOptions() {
    const dl = $("#stock-list");
    dl.replaceChildren(...Object.values(site.stocks).map((s) => el("option", { value: `${s.code} ${s.name || ""}` })));
  }

  function findStock(q) {
    q = (q || "").trim();
    if (!q) return null;
    const code = q.split(/\s+/)[0];
    if (site.stocks[code]) return site.stocks[code];
    const lower = q.toLowerCase();
    return Object.values(site.stocks).find((s) => (s.name || "").toLowerCase() === lower)
      || Object.values(site.stocks).find((s) => (s.name || "").toLowerCase().includes(lower) || s.code.includes(q))
      || null;
  }

  function lastChangeCell(lc) {
    if (!lc) return el("span", { class: "flat" }, "近期無異動");
    return el("span", null, actionBadge(lc.action), ` ${lc.date}`, lc.per_unit_change_pct != null ? ` ${lc.per_unit_change_pct > 0 ? "+" : ""}${fmtPct(lc.per_unit_change_pct)}` : "",
      lc.shares_change ? `（${lc.shares_change > 0 ? "+" : ""}${fmtInt(lc.shares_change)} 股）` : "");
  }

  function renderStock(q) {
    const box = $("#stock-result");
    const s = findStock(q);
    if (!q) { box.replaceChildren(); return; }
    if (!s) { box.replaceChildren(el("p", { class: "empty" }, `找不到「${q}」。目前只收錄 3 檔 ETF 持有過的股票。`)); return; }
    $("#stock-q").value = `${s.code} ${s.name || ""}`;
    const rows = s.held_by.map((h) => ({ cells: [etfBadge(h.etf, etfName(h.etf)), fmtPct(h.weight), fmtInt(h.shares), per1k(h.per_unit), fmtBillion(h.amount), lastChangeCell(h.last_change), el("a", { href: `#etf/${h.etf}` }, "持股表")] }));
    fill(box, el("div", { class: "stock-hit" },
      el("h3", { style: "margin-top:0;color:var(--ink)" }, `${s.code} ${s.name || ""}`, el("span", { class: "chip", style: "margin-left:8px" }, `${s.etf_count} 檔持有`)),
      s.held_by.length ? table([{ label: "ETF" }, { label: "權重", num: true }, { label: "股數", num: true }, { label: "每千單位股數", num: true }, { label: "市值", num: true }, { label: "最近異動" }, { label: "" }], rows)
        : el("p", { class: "empty" }, "目前沒有 ETF 持有。"),
      s.exited_by && s.exited_by.length ? el("p", { class: "hint" }, "已出清：", s.exited_by.map((x) => el("span", { style: "margin-right:8px" }, etfBadge(x.etf), ` ${x.date}`))) : null,
    ));
  }

  function renderStockTop() {
    // JSON 物件的數字鍵在 JS 會被自動排序，這裡重新依持有檔數、合計市值排
    const top = Object.values(site.stocks).filter((s) => s.etf_count > 0)
      .sort((a, b) => b.etf_count - a.etf_count || b.total_amount - a.total_amount).slice(0, 15);
    $("#stock-top").replaceChildren(table(
      [{ label: "代號" }, { label: "名稱" }, { label: "持有檔數", num: true }, { label: "合計市值", num: true }, { label: "持有的 ETF（權重）" }],
      top.map((s) => ({ cells: [el("a", { href: `#stock/${s.code}` }, s.code), s.name || "", String(s.etf_count), fmtBillion(s.total_amount),
        el("span", null, s.held_by.map((h) => etfBadge(h.etf, fmtPct(h.weight, 1))))] }))));
  }

  // ---------------------------------------------------------------- ETF detail
  let detailEtf = null;
  let holdingsSort = { key: "weight", dir: -1 };

  function renderTabs() {
    $("#etf-tabs").replaceChildren(...site.etfs.map((e) => el("button", {
      role: "tab", "aria-selected": String(e.code === detailEtf), style: `--series: var(${seriesVar[e.code]})`,
      onclick: () => { location.hash = `#etf/${e.code}`; },
    }, el("span", { class: "dot" }), `${e.code} ${e.name}`)));
  }

  function renderDetail(code) {
    detailEtf = code;
    renderTabs();
    const d = site.etf_detail[code];
    const box = $("#etf-detail");
    if (!d) { box.replaceChildren(el("p", { class: "empty" }, "沒有這檔的資料。")); return; }
    const ov = site.overview.find((o) => o.etf === code) || {};
    const series = site.series[code];
    const labels = series.map((s) => s.date);
    const v = seriesVar[code];

    // 圖：淨值 vs 收盤（同單位、同軸）、折溢價、非股票水位
    const priceSets = [{ label: "淨值", data: series.map((s) => s.nav), colorVar: v }, { label: "收盤價", data: series.map((s) => s.close), colorVar: "--muted", dash: [4, 3] }];
    const premSets = [{ label: "折溢價", data: series.map((s) => s.premium_pct), colorVar: v }];
    const cashSets = [{ label: "非股票水位", data: series.map((s) => s.non_stock_pct), colorVar: v }, { label: "揭露現金", data: series.map((s) => s.cash_pct), colorVar: "--muted", dash: [4, 3] }];

    const holdings = [...d.holdings].sort((a, b) => {
      const x = a[holdingsSort.key], y = b[holdingsSort.key];
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * holdingsSort.dir;
    });
    const hCols = [["code", "代號"], ["name", "名稱"], ["shares", "股數", 1], ["per_unit", "每千單位股數", 1], ["amount", "市值", 1], ["weight", "權重", 1]];
    const thead = el("thead", null, el("tr", null, hCols.map(([k, label, num]) => el("th", {
      class: "sortable" + (num ? " num" : ""), onclick: () => { holdingsSort = { key: k, dir: holdingsSort.key === k ? -holdingsSort.dir : (num ? -1 : 1) }; renderDetail(code); },
    }, label + (holdingsSort.key === k ? (holdingsSort.dir > 0 ? " ▲" : " ▼") : "")))));
    const tbody = el("tbody", null, holdings.map((h) => el("tr", null,
      el("td", null, el("a", { href: `#stock/${h.code}` }, h.code)), el("td", null, h.name), el("td", { class: "num" }, fmtInt(h.shares)),
      el("td", { class: "num" }, per1k(h.per_unit)), el("td", { class: "num" }, fmtBillion(h.amount)), el("td", { class: "num" }, fmtPct(h.weight)))));

    const nonStock = d.non_stock_items.length ? el("dl", { class: "kv" }, d.non_stock_items.flatMap((i) => [el("dt", null, i.name), el("dd", { class: "num" }, fmtBillion(i.amount))])) : el("p", { class: "empty" }, "此日無揭露明細");
    const futures = d.futures.length ? el("ul", null, d.futures.map((f) => el("li", null, `${f.code} ${f.name}：${fmtInt(f.contracts)} 口（${f.month || ""}），名目本金 ${fmtBillion(f.amount)}，${fmtPct(f.weight)}`))) : null;

    const changes = d.changes.slice(0, 20).map((ch) => el("div", { class: "changes-day" },
      el("h4", null, `${ch.prev_date} → ${ch.date}`, ch.items.length ? "" : "　無異動"),
      ch.items.length ? table([{ label: "動作" }, { label: "代號" }, { label: "名稱" }, { label: "股數 前 → 後", num: true }, { label: "每單位變動", num: true }, { label: "權重 前 → 後", num: true }],
        ch.items.map((it) => ({ cells: [actionBadge(it.action), el("a", { href: `#stock/${it.code}` }, it.code), it.name,
          `${fmtInt(it.shares_prev)} → ${fmtInt(it.shares_curr)}`,
          it.per_unit_change_pct == null ? "–" : (it.per_unit_change_pct > 0 ? "+" : "") + fmtPct(it.per_unit_change_pct),
          `${it.weight_prev == null ? "–" : fmtPct(it.weight_prev)} → ${it.weight_curr == null ? "–" : fmtPct(it.weight_curr)}`] }))) : null,
    ));

    fill(box,
      el("p", { class: "hint" }, `${code} ${etfName(code)}　資料日 ${d.date}　淨值 ${fmtNum(ov.nav)}　收盤 ${fmtNum(ov.close)}　折溢價 ${fmtPct(ov.premium_pct)}　規模 ${fmtBillion(ov.aum)}　單位數 ${fmtInt(ov.outstanding_units)}`),
      el("div", { class: "chart-grid" },
        el("div", { class: "chart-card" }, el("h3", null, "淨值與收盤價"), el("div", { class: "chart" }, el("canvas", { id: "d-price" })), el("details", null, el("summary", null, "表格檢視"), el("div", { id: "d-price-t" }))),
        el("div", { class: "chart-card" }, el("h3", null, "折溢價（%）"), el("div", { class: "chart" }, el("canvas", { id: "d-prem" })), el("details", null, el("summary", null, "表格檢視"), el("div", { id: "d-prem-t" }))),
        el("div", { class: "chart-card" }, el("h3", null, "非股票水位與揭露現金（%）"), el("div", { class: "chart" }, el("canvas", { id: "d-cash" })), el("details", null, el("summary", null, "表格檢視"), el("div", { id: "d-cash-t" }))),
      ),
      el("h3", null, `持股明細（${d.holdings.length} 檔，點欄位標題排序）`),
      el("div", { class: "table-wrap compact" }, el("table", null, thead, tbody)),
      el("h3", null, "非股票部位明細"), nonStock,
      futures ? el("h3", null, "期貨部位") : null, futures,
      el("h3", null, "每日異動（最近 20 個區間）"),
      changes.length ? el("div", null, changes) : el("p", { class: "empty" }, "只有一個資料日，還沒有可比較的異動。"),
    );
    lineChart("d-price", labels, priceSets, { digits: 2 });
    chartTable($("#d-price-t"), labels, priceSets, 2);
    lineChart("d-prem", labels, premSets, { unit: "%", digits: 2 });
    chartTable($("#d-prem-t"), labels, premSets, 2, "%");
    lineChart("d-cash", labels, cashSets, { unit: "%", digits: 2, zero: true });
    chartTable($("#d-cash-t"), labels, cashSets, 2, "%");
  }

  // ---------------------------------------------------------------- about
  function renderAbout() {
    const n = site.notes;
    const cov = site.coverage;
    $("#about-body").replaceChildren(
      el("dl", { class: "kv" },
        el("dt", null, "資料來源"), el("dd", null, "統一投信申購買回清單與基金投資組合（ezmoney.com.tw）、富邦投信 ETF 投資網基金資產（websys.fsit.com.tw）、臺灣證券交易所每日收盤。投信每營業日 16:30 後公告當天持股，本站於 17:30 抓取。"),
        el("dt", null, "每單位持股數"), el("dd", null, n.per_unit),
        el("dt", null, "現金水位"), el("dd", null, n.non_stock_pct),
        el("dt", null, "共同訊號"), el("dd", null, n.intervals),
        el("dt", null, "公司行動"), el("dd", null, n.corporate_action),
        el("dt", null, "行情"), el("dd", null, n.prices),
        el("dt", null, "門檻設定"), el("dd", null, `共同訊號 ${site.config.co_signal_min_etfs} 檔以上；每單位變動 ≥ ${site.config.min_per_unit_change_pct}%；公司行動倍數 ≥ ${site.config.corporate_action_min_ratio}（誤差 ${site.config.corporate_action_tolerance_pct}%）`),
      ),
      el("h3", null, "資料涵蓋"),
      table([{ label: "ETF" }, { label: "首日" }, { label: "最新" }, { label: "天數", num: true }, { label: "缺日" }, { label: "行情天數", num: true }],
        site.etfs.map((e) => ({ cells: [etfBadge(e.code, e.name), cov[e.code]?.first || "–", cov[e.code]?.last || "–", String(cov[e.code]?.count ?? 0), (cov[e.code]?.missing || []).join("、") || "無", String(site.prices_coverage?.[e.code]?.count ?? 0)] }))),
    );
  }

  // ---------------------------------------------------------------- routing
  function route() {
    const h = location.hash.slice(1);
    const [kind, arg] = h.split("/");
    if (kind === "etf" && arg) { renderDetail(arg); document.getElementById("detail").scrollIntoView(); return; }
    if (kind === "stock" && arg) { renderStock(decodeURIComponent(arg)); document.getElementById("stock").scrollIntoView(); return; }
    if (!detailEtf) renderDetail(site.etfs[0].code);
    if (kind) { const s = document.getElementById(kind); if (s) s.scrollIntoView(); }
  }

  function renderAll() {
    seriesVar = Object.fromEntries(site.etfs.map((e, i) => [e.code, SERIES_VARS[i % SERIES_VARS.length]]));
    renderMeta(); renderCards(); renderSignals(); renderCash(); stockOptions(); renderStockTop(); renderAbout();
    route();
  }

  $("#stock-form").addEventListener("submit", (ev) => { ev.preventDefault(); const q = $("#stock-q").value.trim(); location.hash = q ? `#stock/${encodeURIComponent(q.split(/\s+/)[0])}` : "#stock"; renderStock(q); });
  window.addEventListener("hashchange", route);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (site) { renderCash(); if (detailEtf) renderDetail(detailEtf); } });

  fetch(DATA_URL, { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then((data) => { site = data; renderAll(); })
    .catch((e) => { const p = $("#error"); p.hidden = false; p.textContent = `讀不到資料檔 ${DATA_URL}：${e.message}。請先執行 python analyze.py 產生 data/site-data.json。`; $("#site-meta").textContent = "載入失敗"; });
})();
