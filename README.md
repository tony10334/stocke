# 主動式 ETF 觀測站

追蹤台灣主動式 ETF 的每日持股與現金水位。規格見 [docs/spec.md](docs/spec.md)，
資料來源偵察紀錄見 [samples/README.md](samples/README.md)。

## 目前進度

- [x] 步驟 1 偵察：統一投信、富邦投信 PCF／持股頁面格式與 URL 規則（`samples/`）
- [x] 步驟 2 統一投信 fetcher（00981A、00403A）
- [x] 步驟 2 富邦投信 fetcher（00405A）
- [ ] 步驟 3 手動跑幾天確認 schema 穩定（與步驟 4 並行）
- [x] 步驟 4 分析層（`analyze.py` → `data/site-data.json`），含證交所行情與折溢價
- [x] 步驟 5 前端（`site/`，純靜態，無建置步驟）
- [x] 步驟 6 GitHub Actions 排程（`.github/workflows/fetch.yml`）+ GitHub Pages 部署
- [ ] 步驟 7 觀察 1–2 週，處理延遲公告與格式變動

## 需求

Python 3.10+，**不需要任何第三方套件**（只用標準函式庫）。

## 使用

```bash
# 抓 etfs.json 全部 ETF，資料日 = 今天（台北時間）；每個營業日 17:30 後執行
python fetch.py

# 只抓一檔 / 只抓一家投信
python fetch.py --etf 00981A
python fetch.py --provider uni

# 回補歷史（資料日區間，自動跳過週末；假日會回報 no_data）
python fetch.py --date 2026-09-08 --date-to 2026-09-14
```

輸出：`data/<ETF代號>/<資料日>.json`（單日快照）、`data/meta/prices.json`（證交所日收盤，依代號與日期）、以及 `data/meta/fetch_log.jsonl`（每次抓取一行：created / updated / unchanged / no_data / error / skipped）。

持股抓完會順便抓證交所 STOCK_DAY 月報表，涵蓋 `--date` 到 `--date-to` 的月份；`--no-prices` 略過，`--prices-only` 只抓行情：

```bash
python fetch.py --prices-only --date 2026-06-01 --date-to 2026-09-15
```

exit code：0 正常（含「尚無資料」）；2 有非預期錯誤（格式改版、網路失敗）。一檔失敗不影響其他檔。

```bash
# 分析：讀全部快照，輸出前端用的彙整檔 data/site-data.json
python analyze.py
```

門檻參數在 `etfs.json` 的 `analysis` 區塊（共同訊號最少檔數、每單位變動門檻、公司行動倍數等）。

```bash
# 本機預覽前端（從 repo 根目錄起一個靜態伺服器，再開 http://localhost:8765/site/）
python -m http.server 8765
```

前端是 `site/` 下的三個檔案（HTML、CSS、JS），透過相對路徑讀 `../data/site-data.json`，所以整個 repo 直接當靜態網站部署即可（GitHub Pages 選 branch 根目錄，根目錄的 `index.html` 會轉到 `site/`）。若資料檔放別處，可用 `?data=<url>` 覆蓋。圖表用 Chart.js 4.4.1（cdnjs），字體用 Google Fonts 的 Geist、Geist Mono、Noto Sans TC，其餘無依賴。

每檔 ETF 的顏色、標籤、簡介與投信連結在 `etfs.json`（`color`、`tag`、`description`、`info_url`），加新檔時一併填寫。

## 測試

```bash
python -m unittest discover -s tests -v
```

測試只用 `samples/` 裡存好的原始檔，不打網路。

## 結構

```
etfs.json            追蹤清單；加新檔只要加一筆設定（provider + provider_id）
fetch.py             CLI 入口，逐檔呼叫 fetcher、寫快照、記 log
fetchers/
  __init__.py        provider 註冊表（get_fetcher）
  common.py          HttpClient、日期/數字工具、快照 schema 與存檔
  uni.py             統一投信：GetPCF API + 基金頁內嵌 DataAsset
  fubon.py           富邦投信：Assets.aspx 伺服器端渲染的 HTML 表格
  prices.py          證交所 STOCK_DAY 月報表（日收盤、成交量）
analyze.py           分析層入口
analysis/core.py     異動偵測、共同訊號、公司行動、現金水位序列、個股反查（純函式）
site/                前端：index.html / style.css / app.js（總覽、共同訊號、現金水位、個股反查、單檔深入、資料說明）
index.html           轉址到 site/
tests/               unittest
samples/             各來源原始樣本 + 偵察筆記
data/                每日快照（由 fetch.py 產生）
```

## 快照 schema

見 `fetchers/common.py` 開頭的說明。重點欄位：

| 欄位 | 說明 |
|---|---|
| `date` | 持股資料日（檔名用這個） |
| `posted_date` | 投信公告日 |
| `nav`, `outstanding_units`, `aum` | 每單位淨值、流通單位數、基金淨資產 |
| `stock_value` | 股票市值合計 |
| `non_stock_total` | `aum - stock_value`，現金水位主算式 |
| `non_stock` | 投信揭露的現金明細。統一：cash / margin / rp / receivables，只有「最新一天」抓得到，回補的歷史日為 `null`。富邦：cash / redemption_payable / receivables，歷史日也有 |
| `holdings[]` | code / name / shares / amount / weight（%），依權重排序 |
| `futures[]` | 期貨部位（名目本金） |
| `extra` | 各投信額外欄位，分析層不依賴 |

## 排程與部署（GitHub）

`.github/workflows/fetch.yml`：每個平日台北 17:30 與 21:00（UTC 09:30、13:00）各跑一次 `fetch.py` 與 `analyze.py`，
快照或行情有變才 commit 回 `main`（訊息 `data: YYYY-MM-DD`）。也可在 Actions 頁手動觸發並填 `date` / `date_to` 回補。
某檔抓取失敗仍會 commit 其他檔的結果，之後再把 job 標成失敗。週末靠 cron 略過，國定假日靠投信回「尚無資料」略過，不需維護假日清單。

`.github/workflows/ci.yml`：push 或 PR 時跑測試（資料 commit 不觸發）。

首次設定：

1. 在 GitHub 建 repo，推上 `main`：
   ```bash
   git remote add origin git@github.com:<你的帳號>/<repo>.git
   ```
   ```bash
   git push -u origin main
   ```
2. Settings → Actions → General → Workflow permissions 選 **Read and write permissions**（workflow 要能 push 資料）。
3. Settings → Pages → Build and deployment：Source 選 **Deploy from a branch**，Branch 選 `main` / `/ (root)`。
   網址會是 `https://<你的帳號>.github.io/<repo>/`，根目錄 `index.html` 會轉到 `site/`。每次資料 commit 後 Pages 會自動重新發布。
4. 第一次可到 Actions → 每日抓取與分析 → Run workflow 手動跑一次確認。

## 分析規則（`data/site-data.json`）

- **每單位持股數** = 股數 ÷ 流通在外單位數。日對日比較用它，排除申贖造成的規模效果。
- **加碼／減碼**：同一檔 ETF 相鄰兩個快照日，股數有變、且每單位變動超過 `min_per_unit_change_pct`（預設 0.5%）。只有單位數變、股數沒變是申贖漂移，不算。新出現為「建倉」，消失為「出清」。
- **共同訊號**：只比較全體日期序列中相鄰兩天，且只納入兩天都有快照的 ETF（缺席者列在 `excluded`）。同一股票被 `co_signal_min_etfs`（預設 2）檔以上建倉／加碼即為共同加碼；減碼／出清同理為共同減碼。
- **疑似公司行動**：同一區間 2 檔以上對同一股票的股數倍數一致（誤差 1% 內）且倍數 ≥ 1.5 或 ≤ 1/1.5，標記 `corporate_action`，不算加減碼，待人工核對。
- **現金水位**：`non_stock_pct` =（淨資產 − 股票市值）÷ 淨資產，三檔口徑一致；`cash_pct` 是投信揭露的現金項目，統一只有最新一天有。
- **個股反查**：`stocks[代號]` 列出持有它的 ETF、股數、權重、每單位股數與最近一次異動；已出清的股票也查得到。
- **行情與折溢價**：`close` 是證交所當日收盤，`premium_pct` =（收盤 − 淨值）÷ 淨值，淨值用投信當日公告值。缺行情的日子為 null。

## 揭露時序

兩家投信都在營業日 T 的 16:30 後揭露 **T 日**的持股：

- 統一：PCF 公告日標為 T+1 營業日，基金頁的現金明細同時更新為 T 日（且只保留最新一天）。
- 富邦：`Assets.aspx?ddate=T` 在 16:30 後就回 T 日資料，歷史日含現金明細。

所以 17:30 跑 `python fetch.py` 抓到的是**當天**持股，且三檔都含現金明細。
統一的現金明細回補不到，錯過就沒了；這是排程不能漏跑、21:00 要補抓的原因。
