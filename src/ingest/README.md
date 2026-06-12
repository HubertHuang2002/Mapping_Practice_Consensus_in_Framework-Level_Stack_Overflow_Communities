# Module A — Stack Overflow 資料收集（React）

## 研究主題

**Mapping Community-Consensus Practice Landscape in Framework-Level Stack Overflow Communities via Multi-Answer Structure**

Case study：`[reactjs]`，2021–2026 window（涵蓋 React 17 → 19、class→functional 轉型、server components 興起的完整時期）。

## 收集策略總覽

| 項目 | 設定 |
|------|------|
| 資料來源 | Stack Overflow API v2.3 |
| 目標框架 | React（SO tag `reactjs`，單一 framework） |
| 時間範圍 | **2021-01 ~ 2026-04（63 個月）**，分 5 個 yearly sub-period 收集 |
| 收集策略 | **無 cap** — 收集 SO 上每一篇有 `reactjs` tag 的問題 |
| 排序 | `sort=votes`（高票優先）|
| 跨框架比較 | 不適用（單一 framework） |
| Troubleshooting 過濾 | 不做（consensus 分析直接用全集，TS 邊界由下游 BERTopic + LLM 判斷） |

### 為什麼是這個策略

1. **時間範圍 2021-01 ~ 2026-04**：涵蓋 React 17 → 18 → 19 完整版本演進、function component 取代 class component 的高峰、以及 server components / RSC 興起的轉折期，足以觀察社群 consensus 的時序變化。
2. **無 cap**：研究目標是 community-consensus landscape，需要全量 vote 與 answer 結構，cap 會把長尾的「分歧意見」也砍掉。
3. **votes 排序**：API backoff 時先收高票，掉的是長尾低票（對 PageRank、within-topic clustering 影響較小）。
4. **單一 framework**：對齊 proposal「framework-level community」定義，避免跨 framework 的混雜效應。

---

## 實際收集結果

> 收集已完成。下列為最終 dataset 的統計（realized window **2021-01-01 ~ 2026-03-31**）。

```
Time range:    2021-01-01 ~ 2026-03-31  (~5 年, realized)
Cap mode:      no cap (collect every question SO has for [reactjs])

DATA COLLECTION SUMMARY (final)
  questions    :   221,212
  answers      :   253,351
  comments     :   604,681
  users        :   187,701
  interactions :   778,676
```

### 多答案核心子集（共識分析的實際分析對象）

「一題多答」是本研究的分析前提，真正進入下游 pipeline 的是多答案子集：

| 子集 | 數量 | 佔比 |
|------|------|------|
| 全部 questions | 221,212 | 100% |
| **≥2 個 answers**（可分析核心）| **53,262** | 24.1% |
| ≥3 個 answers | 14,802 | 6.7% |
| 0 個 answers | 45,323 | 20.5% |
| accepted answer 存在 | 85,612 | 33.8% of answers |

### 每年題數分布

| Year | Questions |
|------|-----------|
| 2021 | 81,317 |
| 2022 | 78,639 |
| 2023 | 41,294 |
| 2024 | 16,531 |
| 2025 | 3,309 |
| 2026 (Q1) | 122 |
| **TOTAL** | **221,212** |

> 2023 起新題量急遽下滑、2025 全年僅 3,309 題 —— 對應 SO 在 ChatGPT 之後的流量崩塌（與 `probe/2026-05-13-window-span-check` 觀察一致）。長尾年份的 trajectory 解讀需謹慎。

### 互動邊類型分布

| 類型 | 邊數 | 佔比 |
|------|------|------|
| `comment` | 378,526 | 48.6% |
| `answer` | 219,418 | 28.2% |
| `comment_reply` | 180,732 | 23.2% |
| **TOTAL** | **778,676** | |

> baseline 圖分析（Module B）只取 `answer`（A2Q）邊；`comment` / `comment_reply` 邊列為 future work（comment-edge 敏感度分析）。self-loop 已排除（0 筆）。

---

## 資料庫結構（SQLite）

資料庫檔案：`so_data_react.db`

### `questions` — 問題（15 欄）

| 欄位 | 類型 | 說明 |
|------|------|------|
| `question_id` | INTEGER PK | SO 問題 ID |
| `title` | TEXT | 問題標題（HTML entity 已 unescape）|
| `body` | TEXT | 原始 HTML 內容 |
| `body_text` | TEXT | 清理後純文字（NLP 用）|
| `tags` | TEXT | 所有 SO 標籤的 JSON array（如 `["reactjs","typescript","next.js"]`）|
| `score` | INTEGER | 投票分數 |
| `view_count` | INTEGER | 瀏覽次數 |
| `answer_count` | INTEGER | 回答數量 |
| `creation_date` | INTEGER | Unix timestamp |
| `owner_user_id` | INTEGER | 提問者 user ID |
| `owner_display_name` | TEXT | 提問者顯示名稱 |
| `link` | TEXT | 問題連結 |
| `is_closed` | INTEGER | 是否已關閉（0/1）|
| `closed_reason` | TEXT | 關閉原因 |
| **`is_react`** | INTEGER | 是否含 `reactjs` tag（0/1）|

### `answers` — 回答（10 欄）

| 欄位 | 類型 | 說明 |
|------|------|------|
| `answer_id` | INTEGER PK | SO 回答 ID |
| `question_id` | INTEGER | 父問題 ID |
| `body` / `body_text` | TEXT | 原始 HTML / 清理後純文字 |
| `score` | INTEGER | 投票分數 |
| `is_accepted` | INTEGER | 是否最佳解答（0/1）|
| `creation_date` | INTEGER | Unix timestamp |
| `owner_user_id` / `owner_display_name` | | 回答者 |
| **`is_react`** | INTEGER | **由父問題繼承**的框架旗標 |

### `comments` — 留言（12 欄）

| 欄位 | 類型 | 說明 |
|------|------|------|
| `comment_id` | INTEGER PK | SO 留言 ID |
| `post_id` | INTEGER | 留言所附貼文（問題或回答）的 ID |
| `post_type` | TEXT | `'question'` 或 `'answer'` |
| `question_id` | INTEGER | **根問題 ID**（即使留言在回答上也指根題）|
| `body` / `body_text` | TEXT | 原始 HTML / 清理後純文字 |
| `score` | INTEGER | 投票分數 |
| `creation_date` | INTEGER | Unix timestamp |
| `owner_user_id` / `owner_display_name` | | 留言者 |
| `reply_to_user_id` | INTEGER | 留言中 @提及的對象（若有）|
| **`is_react`** | INTEGER | **由根問題繼承**的框架旗標 |

### `users` — 使用者（8 欄）

| 欄位 | 類型 | 說明 |
|------|------|------|
| `user_id` | INTEGER PK | SO 使用者 ID |
| `display_name` | TEXT | 顯示名稱 |
| `reputation` | INTEGER | 聲望值（給 PageRank vs reputation 相關性分析用）|
| `badge_gold` / `badge_silver` / `badge_bronze` | INTEGER | 各色徽章數 |
| `creation_date` | INTEGER | 帳號建立時間 |
| `link` | TEXT | 使用者頁面連結 |

### `interactions` — 互動邊（8 欄，圖分析輸入）

| 欄位 | 類型 | 說明 |
|------|------|------|
| `id` | INTEGER PK | 自動遞增 |
| `source_user_id` | INTEGER | 互動發起者（回答者／留言者）|
| `target_user_id` | INTEGER | 互動接收者（提問者／被 @對象 / 貼文作者）|
| `interaction_type` | TEXT | `'answer'` / `'comment_reply'` / `'comment'` |
| `post_id` | INTEGER | 觸發互動的貼文 ID |
| `parent_post_id` | INTEGER | 被互動的父貼文 ID |
| **`is_react`** | INTEGER | **由根問題繼承**的框架旗標 |
| `creation_date` | INTEGER | 互動時間 |

### `_progress` — 斷點續傳記錄

| 欄位 | 類型 | 說明 |
|------|------|------|
| `tag` | TEXT | `'reactjs'` |
| `stage` | TEXT | `'questions'` / `'answers'` / `'comments'` |
| `updated_at` | INTEGER | Unix timestamp |

PK = `(tag, stage)`。重新執行 `collect_data.py` 時，已完成的 stage 會被跳過。

---

## 設計重點

### 1. One-hot 框架編碼

雖然目前只有 `reactjs` 一個 tag，schema 仍保留 `is_react` 0/1 旗標而非完全省略，原因有二：
- schema 採可泛化的 one-hot 設計，未來擴充其他 framework 不需 migrate。
- 下游 SQL 可以一致地用 `WHERE is_react = 1` 篩選，不必為單一 framework 寫特例。

```sql
-- 所有 React 問題
SELECT * FROM questions WHERE is_react = 1;

-- React 問題的回答（含繼承的 is_react 旗標）
SELECT * FROM answers WHERE is_react = 1;

-- 整個 React subgraph
SELECT * FROM interactions WHERE is_react = 1;
```

### 2. 互動邊類型

| 類型 | 方向 | 說明 |
|------|------|------|
| `answer` | 回答者 → 提問者 | 使用者回答了某個問題 |
| `comment_reply` | 留言者 → 被回覆者 | 留言中有明確的 @提及（API 提供的 `reply_to_user`）|
| `comment` | 留言者 → 貼文作者 | 留言無 @提及時，預設指向該貼文的作者 |

排除 self-loop（自己回自己）和 NULL user ID（匿名／已刪除帳號）。

### 3. 文字清理規則（`body_text`）

`clean_html()` 步驟：
1. 將 `<pre>...</pre>` 區塊替換為 ` [CODE] ` token
2. 將 inline `<code>...</code>` 替換為 ` [CODE] ` token
3. 將 `<br>` / `<br/>` / `<br />` / `</p>` / `</li>` 換成換行（保留段落結構）
4. 移除所有其餘 HTML 標籤
5. 解碼 HTML entities（`&amp;` → `&`、`&#39;` → `'`、`&quot;` → `"` 等）
6. 壓縮連續空白為單一空格、3+ 個換行壓縮為兩個
7. 去除首尾空白

**`title` 也經過 step 5 的 entity 解碼**。

> **`body_text` vs `body`**：embedding 與 topic 模型用 `body_text`；原始 HTML（含程式碼）保留在 `body`，需要時可重建。

### 4. SO API has_more bug fix

Stack Exchange API 偶發在中間頁回傳 `has_more=false`，但下一頁仍有資料（已實測 2021 React 第 14、15 頁出現假性 `false`、第 16 頁恢復 `true`，若直接相信旗標會在 1,400 題就停掉，少抓 ~98% 資料）。`api_get()` **完全忽略 `has_more` 旗標**，改以「連續 2 頁回傳 0 筆 items」作為真實 end-of-stream 訊號。

### 5. 反正規化設計

`answers` / `comments` / `interactions` 的 `is_react` 旗標**繼承自根問題**，下游不需 JOIN 即可篩選。`comments.question_id` 不論留言在問題或回答上，都指向根問題 ID（方便追溯）。

### 6. 資料品質保證

- 所有貼文都有 `is_react = 1`
- 所有留言都有 `question_id`，可追溯到原始問題
- Answer 的 `is_react` 跟父問題完全一致（驗證 0 mismatched）
- 互動邊排除 self-loop、NULL user ID
- 所有出現在 Q/A/C 中的 user 都在 `users` 表
- `body_text`、`title` 經過完整清理（HTML entity 殘留率 < 0.01%）

---

## 檔案

| 檔案 | 說明 |
|------|------|
| `config.py` | 共用設定（API key 從 env 讀、TAGS、TAG_COLUMNS、ONEHOT_COLS、時間範圍）|
| `collect_data.py` | 正式資料收集腳本（支援斷點續傳，含 has_more bug fix）|
| `collect_data_test.py` | 整合測試腳本（小樣本驗證完整管線）|
| `phase1_audit.py` | Phase 1 完整 audit |
| `README.md` | 本檔 |
| `so_data_react.db` | 正式資料庫（執行 `collect_data.py` 後產生；repo 已 gitignored）|

---

## 執行方式

### 1. 設定 API key

```bash
export SE_API_KEY=<your_stackapps_key>
# 或複製 .env.example -> .env 後填入
```

`config.py` 透過 `os.environ.get("SE_API_KEY", "")` 讀取，不再 hard-code。

### 2. 跑整合測試（小樣本）

```bash
python collect_data_test.py
```

測試會：
- 對 SO API 發出 ~25 個 calls
- 拉 50 題入測試 DB
- 跑完整 pipeline（questions → answers → comments → users → interactions）
- 跑 14 個 D-checks，verdict 印 `ALL PASS — Ready to run collect_data.py`

### 3. 跑正式收集

```bash
python collect_data.py
```

預估：
- API 配額用量 ~6,000-8,000 calls（1 個 key 的 10,000/天可能剛好夠或需要分兩天，斷點續傳會自動接續）
- 時間 60-120 分鐘
- 最終 DB ~1.6 GB（2021-2026 全 window，221k questions / 253k answers / 605k comments）
- 支援斷點續傳：中途中斷後重跑會跳過已完成的 stage（記錄在 `_progress` 表）

### 4. 跑 audit 確認資料品質

```bash
python phase1_audit.py
```

verdict 應印 `Audit: READY`。

---

## 下游分析用途

本資料集是 community-consensus-practice pipeline 的資料底層（**Module A**）。下游 module 消費它（完整設計見 root `README.md`、`docs/spec.md`、`docs/adr/`）：

- **Module B — User Network & Authority**：用 `interactions`（A2Q 邊）建有向圖，逐年 PageRank + Louvain community，結合 SO 原生 reputation / accept-rate 計算多來源權威。實作見 `src/authority/`。
- **Module C — Question canonicalization**：對 developer query 跑 SBERT retrieval + LLM equivalence gate，把語意等價的問題聚成 canonical group。
- **Module D — Practice breakdown + Dashboard**：每個 canonical group 內對 answers 抽 practice → hierarchical map-reduce 聚類 → 疊加 authority → 呈現 **emergent breakdown shape**（convergent / divergent / shifting / authority-disputed，由 breakdown 自然 emergent，**非預先指定的 typology**）。

> **Pivot 註記**：專案已於 2026-05-29 pivot（`docs/adr/0001`–`0005`），從「corpus-wide topic landscape + 預先指定 Convergent/Divergent/Insufficient/Ambiguous typology」改為「query-driven on-demand retrieval + emergent breakdown shape」。本 README 早期的 Phase 2–6 / hard typology 描述已隨之移除。

---

## Known limitations

1. **單一 framework**：無法做 cross-framework comparison，proposal 設計上即放棄此維度，換取 React 內部更深入的 within-community 分析。
2. **長尾低票題的丟失風險**：`sort=votes desc` 在 API backoff 時優先保住高票。對 consensus 分析影響有限（共識本來就由高票主導），但分析「邊緣意見」時須留意。
3. **時間衰退**：新題量自 2023 起急遽下滑（2021 ~81k → 2025 僅 3.3k），即 ChatGPT 之後 SO 流量崩塌在 React 資料上的反映。長尾年份（2024–2026）樣本稀薄，trajectory 解讀需謹慎。
4. **One-shot user 比例高**：圖分析時建議先做 k-core decomposition（保留 ≥2 互動的 user）再跑 community detection。
