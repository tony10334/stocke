# 偵察紀錄：統一投信 / 富邦投信 PCF 與持股資料來源

偵察日期：2026-09-15（台北時間下午）。對應規格第 7 節步驟 1。
本目錄的樣本皆為當日用 curl 直接抓取的原始檔，未經修改。

---

## 1. 統一投信（00981A、00403A）— `www.ezmoney.com.tw`

### 1.1 基金代碼對照（網站內部 fundCode）

| 交易代號 | 名稱 | fundCode |
|---|---|---|
| 00981A | 主動統一台股增長 | `49YTW` |
| 00403A | 主動統一升級50 | `63YTW` |
| 00411A | 主動統一前沿科技 | `64YTW` |
| 00988A | 主動統一全球創新 | `61YTW` |
| 00987D | 主動統一美債量化 | `65YTW` |

完整清單見 `uni/00_pcf_page_2026-09-15.html` 內 `<select>` 的選項。

### 1.2 資料來源 A：申購買回清單（PCF）JSON API

- 頁面：`https://www.ezmoney.com.tw/ETF/Transaction/PCF`（Vue 前端，資料由 AJAX 載入）
- API：`POST https://www.ezmoney.com.tw/ETF/Transaction/GetPCF`
- Header：`Content-Type: application/json; charset=utf-8`
- Body：
  ```json
  {"fundCode": "49YTW", "date": "115/09/15", "specificDate": true}
  ```
  - `date` 用**民國年** `yyy/mm/dd`，代表 PCF **公告日**（PostDate）。
  - `specificDate: false` 時伺服器忽略 date、回傳最新一筆。
- 回傳 JSON 四個頂層 key：`pcf`（摘要）、`fund`（基金靜態資料）、`asset`（持股分組）、`assetDetailSchema`（欄位定義）。
- `pcf[]` 的 `PCFCode`：`PRE_AMT`、`NAV`、`OUT_UNIT`（流通單位數）、`DIFF_UNIT`、`P_UNIT`（每單位淨值）、`FUND_BASEUNIT`、`BASEUNIT_MRK_VAL`、`DIFF_ACT_AMT`、`ACT_AMT`、`NAV_PEOPLE`（受益人數，`RowVisible=false`）。
- `asset[]`：`AssetCode = GD`（期貨名目本金，Details 可為 `null`）、`ST`（股票）。每筆 Detail 有 `DetailCode`（股票代號）、`DetailName`、`Share`（股數）、`Amount`（市值）、`NavRate`（權重 %）。
- 日期欄位是 .NET 格式 `/Date(1789315200000)/`（毫秒 epoch，UTC 00:00 = 台北 08:00）。`TranDate` = 持股資料日（T-1 營業日），`PostDate` = 公告日（T）。
- **注意：PCF API 沒有現金 / 保證金 / RP 明細**，非股票部位只能用 `NAV − ST.Value` 推得合計。現金拆分要看來源 B。

歷史查詢測試結果（fundCode 49YTW）：

| date 參數 | 結果 |
|---|---|
| 115/09/10、09/11、09/14、09/15 | 有資料，TranDate 各為前一營業日 |
| 115/09/12、09/13（週末） | 全部欄位為 0，`asset` 為空陣列 |
| 115/09/16、09/17（未來） | 全部欄位為 0 |

→ 支援回補歷史（至少數個營業日，上限未測）；非營業日／尚未公告時回傳全零，**fetcher 要把 `NAV == 0` 視為「無資料」**而不是寫入。

### 1.3 資料來源 B：基金投資組合（含現金明細）— 內嵌於基金頁 HTML

- 頁面：`https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW`
- 伺服器直接把資料放在 `<div id="DataAsset" data-content="[...]">`（HTML entity 編碼的 JSON），**不需執行 JS**，curl 抓 HTML 後 regex 取出、`html.unescape` 再 `json.loads` 即可。
- 同頁另有 `DataFund`、`DataFundList`、`DataAssetDetailSchema` 等內嵌 JSON。
- `DataAsset` 的 `AssetCode`：

| AssetCode | 名稱 | Group | 說明 |
|---|---|---|---|
| `NAV` | 淨資產 | 1 | 基金淨資產（元） |
| `OUT_UNIT` | 流通在外單位數 | 1 | 流通單位數 |
| `P_UNIT` | 每單位淨值 | 1 | |
| `GD` | 期貨(名目本金) | 2 | 有 Details |
| `ST` | 股票 | 2 | 有 Details（50 檔） |
| `CASH` | 現金 | 3 | |
| `GDM` | 期貨保證金 | 3 | |
| `RP` | 附買回債券 | 3 | |
| `APAR` | 應收付證券款 | 3 | |

- 日期為 ISO 字串（`"2026-09-14T00:00:00"`），`TranDate` 為資料日。
- **只有最新一天**，沒有日期參數 → 現金水位歷史必須每日累積，無法回補。
- 同資料另有 XLSX 匯出：`GET /ETF/Fund/AssetExcelNPOI?fundCode=49YTW`（檔名 `ETF_Investment_Portfolio_20260914.xlsx`，單一工作表，人類閱讀用，程式解析建議用 DataAsset）。

2026-09-15 抓到的 00981A 數字（資料日 09/14）與規格第 3 節範例完全一致（cash 4,823,473,398 / margin 1,848,386,338 / rp 1,258,097,300 / receivables 443,464,120），表示規格作者參考的就是這個來源。

### 1.4 反爬與存取注意

- 第一次請求會收到 **HTTP 307** 並 `Set-Cookie: __nxquid=...`，Location 指回同一 URL。帶著 cookie 重送即可成功。curl 用 `-c/-b` cookie jar、Python 用 `requests.Session()` 即可；建議先 GET 一次 PCF 頁面暖機再打 API。
- 不需 `__RequestVerificationToken`（GetPCF / Fund/Info 都不用）。
- 不需特殊 UA，但建議帶正常瀏覽器 UA。
- robots.txt 結果見本文末。

### 1.5 其他可用端點（供步驟 2.3 每日行情參考，未深入驗證）

- `POST /ETF/Fund/GetNavHistory`，body `{fundCode, startDate, endDate}`（民國年）— 歷史淨值。
- `GET /ETF/Fund/ValueJson/?fundCode=49YTW` — 近一年淨值走勢（Highcharts 用）。
- `/ETF/Transaction/UnitMarketRatio` — 預估淨值與市價比較。

### 1.6 樣本檔

| 檔案 | 內容 |
|---|---|
| `uni/00_pcf_page_2026-09-15.html` | PCF 頁面原始 HTML（含基金選單與 Vue 程式碼） |
| `uni/00981A_getpcf_2026-09-15.json` | GetPCF 回傳（49YTW，date 115/09/15） |
| `uni/00403A_getpcf_2026-09-15.json` | GetPCF 回傳（63YTW，date 115/09/15） |
| `uni/00981A_pcf_2026-09-15.xlsx` | `PCFExcelNPOI?fundCode=49YTW&date=115/09/15&specificDate=true` |
| `uni/00981A_fundinfo_page_2026-09-15.html` | 基金頁原始 HTML（含 DataAsset） |
| `uni/00403A_fundinfo_page_2026-09-15.html` | 同上，63YTW |
| `uni/00981A_fundinfo_DataAsset_2026-09-15.json` | 從基金頁抽出的 DataAsset JSON |
| `uni/00403A_fundinfo_DataAsset_2026-09-15.json` | 同上，63YTW |
| `uni/00981A_asset_2026-09-15.xlsx` | `AssetExcelNPOI?fundCode=49YTW` 匯出檔 |

---

## 2. 富邦投信（00405A）— `websys.fsit.com.tw`

### 2.1 資料來源 A：基金資產（持股明細 + 現金）— **主要來源**

- URL：`https://websys.fsit.com.tw/FubonETF/Trade/Assets.aspx?stkId=00405A&ddate=2026/09/15&lan=TW`
- **純 GET，不需 ViewState / postback**，curl 直接抓。
- `ddate` 是查詢日（西元 `yyyy/mm/dd`），語意是「**不晚於該日的最新資料**」，頁面的「資料日期」才是真正的資料日：
  - 16:2x 抓 `ddate=2026/09/15` → 資料日期 2026/09/14（當天尚未更新）
  - 17:10 抓 `ddate=2026/09/15` → 資料日期 **2026/09/15**（16:30 後當天持股已揭露）
  - 17:10 抓 `ddate=2026/09/16` → 資料日期 2026/09/15
  - `ddate=2026/09/12`（週六）→ 資料日期 2026/09/11（退到前一營業日，不會回空）
  - 所以 fetcher 用 `ddate=資料日`，再檢查「資料日期」是否相等；小於就是尚未公告或假日。
- 支援歷史回補（上限未測）。
- 頁面結構（ASP.NET WebForms，伺服器端渲染）：
  - 摘要欄位：基金淨資產(新台幣)、基金在外流通單位數(單位)、基金每單位淨值(新台幣)、`資料日期：yyyy/mm/dd`
  - 持股表：`<table class="table1 fix3 darkblue lastdark w1360 blue_t">`，欄位 `股票代碼 | 股票名稱 | 股數 | 金額 | 權重(%)`，最後一列「股票合計」。
  - 注意頁面有兩個同 class 的表格，第一個帶 `cloned` class 是固定欄位用的複製品（只有股票代碼一欄），要取**沒有 `cloned`** 的那個。
  - 非股票表：`<table class="table1 noxscroll fix1 ...">`，欄位 `項目 | 金額`，目前三列：`現金 (TWD)`、`應付受益權單位買回款 (TWD)`、`應收(付)證券款 (TWD)`。
- 數字含千分位逗號，權重是百分比數值（如 `8.7687`）。
- 「下載」按鈕是 postback（需回傳 `__VIEWSTATE`、`__EVENTVALIDATION` 與 `btnDownload`），回傳 `Content-Type: application/vnd.ms-excel`，但**實際內容是 HTML 表格偽裝的 .xls**，資訊與網頁相同，不值得用。

### 2.2 資料來源 B：申購買回清單（PCF）

- URL：`https://websys.fsit.com.tw/FubonETF/Trade/Pcf.aspx?stkId=00405A&lan=TW`（GET；日期查詢是 postback，未測歷史）
- 對 00405A 而言**只有摘要**（預收申購總價金、基金淨資產價值、已發行受益權單位總數、與前日差異、每單位淨值、申購基數、上月受益人數等），**沒有持股表**（被動式 ETF 如 00717 才有）。
- 因此 00405A 的持股與現金一律從來源 A 取得；PCF 頁可選擇性抓「已發行受益權單位總數」交叉驗證。

### 2.3 其他頁面（供步驟 2.3 參考）

- 基金頁 `Fund/Profile.aspx?stkId=00405A`：有市價、淨值（各附日期與漲跌）、基金規模。
- `Trade/PremiumDiscount.aspx`：折溢價查詢。
- `Trade/Estimate.aspx`：即時估計淨值。

### 2.4 樣本檔

| 檔案 | 內容 |
|---|---|
| `fubon/00405A_assets_2026-09-15.html` | Assets.aspx，ddate=2026/09/15，16:2x 抓（資料日 09/14） |
| `fubon/00405A_assets_2026-09-16.html` | Assets.aspx，ddate=2026/09/16，17:10 抓（資料日 09/15，當天已揭露） |
| `fubon/00405A_assets_2026-09-12.html` | Assets.aspx，ddate=2026/09/12（資料日 09/11，驗證週末退回） |
| `fubon/00405A_pcf_2026-09-15.html` | Pcf.aspx（僅摘要） |
| `fubon/00405A_assets_download_2026-09-15.xls` | 「下載」postback 回傳（HTML 偽裝 xls） |

---

## 3. 對規格的修正與補充

1. **規格 2.1 說 PCF 內含 CASH/MARGIN/RP**：不正確。統一的 PCF API 只有期貨與股票；現金拆分在基金頁的 `DataAsset`（AssetCode 為 `CASH`/`GDM`/`RP`/`APAR`，不是 `C_NTD`/`MARGIN`）。統一 fetcher 每天要打兩個來源。
2. **規格 2.2 擔心 ViewState / postback**：富邦持股頁是純 GET，不需處理。
3. **規格 3 schema 的 `non_stock`**：兩家欄位不對稱。統一：cash / margin(GDM) / rp / receivables(APAR)；富邦：cash / 應付買回款 / 應收付證券款。建議 schema 保留原始項目名稱陣列（`non_stock_items: [{name, amount}]`）再加一個 `non_stock_total`，計算現金水位時用 `NAV − 股票市值` 為主、明細為輔，兩家才可比。
4. **資料日與公告日**（2026-09-15 17:00 再驗證後修正）：統一在 T 日 16:30 後就把 **T 日**的持股掛上，公告日標為 **T+1 營業日**（`specificDate=false` 回的最新一筆即為 TranDate=T、PostDate=T+1），基金頁 DataAsset 同時更新為 T 日。所以 17:30 排程抓到的是**當天**持股，不是 T-1。快照檔名用**資料日（TranDate）**，並另存 `posted_date`。富邦同樣在 T 日 16:30 後揭露 T 日持股（17:10 驗證 `ddate=T` 已回 T 日資料）。兩家都是「T 日 17:30 抓當天」。
5. **回補能力**：統一 GetPCF 與富邦 Assets 都能查歷史持股；但統一的現金明細（DataAsset）只有最新一天，錯過就沒了。這是每日排程一定要跑、且 21:00 補抓要保留的主因。
6. **統一非營業日回傳全零**而非錯誤；富邦非營業日自動退到前一營業日。fetcher 對前者要判斷 `NAV == 0`，對後者要比對「資料日期」是否等於預期日，避免把同一天資料存成兩份。
7. **流通單位數**兩家都直接揭露（統一 `OUT_UNIT`，富邦「基金在外流通單位數」），不需用規模÷淨值推算。
8. 統一 fundCode（`49YTW`）與交易代號不同，`etfs.json` 每檔要多一個 `provider_id` 欄位。

## 3b. 2026-09-22 新增：野村、國泰、摩根

### 野村投信（00980A、00985A、00999A）— `www.nomurafunds.com.tw`

- 頁面 `ETFWEB/product-description?fundNo=00980A&tab=Shareholding`（Angular）。
- API：`POST /API/ETFAPI/api/Fund/GetFundAssets`，JSON `{"FundID":"00980A","SearchDate":"2026-09-21"}`。
  - `SearchDate` 必須是 `YYYY-MM-DD`；空字串或 `2026/09/21` 會 400；缺參數或 null 回最新一天。
  - 非營業日／未公告：`StatusCode: 5`「此搜尋條件尚無相關資料」。
  - 回傳 `Entries.Data.FundAsset {Aum, Units, Nav, NavDate}`，`Table[]` 三張：`股票`（代號、名稱、股數、權重%）、`期貨`（代碼、名稱、口數、權重%）、空標題（項目／金額，含 股票、期貨、現金、保證金、應收(付)證券款）。
  - 不需 cookie，curl 直接可打。歷史至少到 2026-06。
- 樣本：`nomura/00980A_GetFundAssets_2026-09-21.json`、`00985A_…`、`00999A_…`。

### 國泰投信（00400A）— `cwapi.cathaysite.com.tw`

- 官網 `www.cathaysite.com.tw/ETF/detail/EEA?tab=etf3` 是前端渲染，資料來自 `cwapi`，全部 GET、無需 cookie：
  - `/api/ETF/GetETFAssets?FundCode=EA&SearchDate=2026-09-21&status=1` → `{preDate, fundNav(淨資產), fundOutstandingShares, fundPerNav}`
  - `/api/ETF/GetETFDetailStockList`（stockCode, stockName, volumn, weights）、`GetETFOptionList`、`GetETFDetailBalList`（現金、保證金、申贖應付款、股票、選擇權）、`GetETFDetailFutureList`。
  - `FundCode` 是內部代碼，`GetETFList` 可查對照（00400A = `EA`）。
  - 查未來日期會退回最新（`preDate` 為準），週日回 `returnCode 4005 查無資料`。歷史至少到 2026-04。
- 樣本：`cathay/00400A_*_2026-09-21.json`（五個端點）。

### 摩根投信（00401A）— `am.jpmorgan.com`

- 產品頁 `…/twetf/products/jpmorgan-taiwan-taiwan-equity-high-income-active-etf-tw00000401a1`，資料由 `FundsMarketingHandler/product-data?cusip=TW00000401A1&country=tw&role=twetf&language=zh` JSON 提供（只有最新一天的持股）。
- 歷史用 Excel：`FundsMarketingHandler/excel?type=m12_pcf&cusip=TW00000401A1&country=tw&role=twetf&locale=zh-TW&date=YYYY-MM-DD`
  - `date` 是 PCF 公告日（資料日的下一營業日）；工作表：現金申購買回清單公告（淨資產、單位數、淨值）、基金資產 - 股票（代碼、名稱、股數、金額、權重）、期貨、選擇權、現金與約當現金。工作表標題括號內是資料日。
  - `type=holding_pcf&date=<資料日>` 只有持股四張表，沒有摘要。
  - `product-data` 的 `stringValueWrapper.m12AvailableDates` 列出有 PCF 的日期。
- 樣本：`jpm/00401A_m12_pcf_2026-09-22.xlsx`、`00401A_holding_pcf_2026-09-21.xlsx`、`00401A_product-data.json`。

## 4. robots.txt（2026-09-15 查詢）

- `websys.fsit.com.tw/robots.txt`：`Disallow: /` 但 `Allow: /FubonETF`、`Allow: /Event` → 我們用的 `/FubonETF/Trade/Assets.aspx` 在允許範圍。
- `www.ezmoney.com.tw/robots.txt`：不存在（回 404 客製頁「您所連結的頁面已不存在」），無限制宣告。仍維持每日 1–2 次的低頻抓取。
