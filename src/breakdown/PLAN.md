# Module D — Answer-side practice breakdown + dashboard

> Scope, interface, 待 grill 項。實作前再回頭 grill 完才動手。
> Refs: ADR 0001–0005 / `docs/spec.md § 3.1 Module D` + § 2.5 / 2.8 / 2.9 / 3.6 / 3.7

## Scope（這個 module 負責）

對每個 `canonical_group`（從 Module C 拿）：

1. **Practice extraction**（per answer parallel LLM call）→ `{practice: 1 sentence, conditions: [...], evidence_type: prose|code|both}`
2. **Step-2 aggregator**（one-shot LLM, k=3 voting）→ cluster assignment + cluster name + description
3. **Companion deterministic run**（SBERT short-sentence embed + HDBSCAN）→ ARI agreement vs LLM aggregator as method-defensibility metric
4. **Authority overlay**（deterministic per-cluster aggregation；multi-signal: PageRank / reputation / vote / accept / temporal centroid）
5. **LLM narrative**（per query, 2–3 句 shape description）
6. **Dashboard**（beeswarm + click-to-detail + trajectory 副板 + size/color/sort axis dropdown）

## 對外接口

**輸入**：
- 來自 Module B：`pagerank_full` / `user_pagerank_yearly` / `users.community_id` + multi-source authority lookup function
- 來自 Module C：函式 `query → canonical_group`（或讀 `canonical_group` 表）

**輸出**：
- DB tables：`practice_extractions` / `practice_clusters` / `answer_to_practice_cluster` / `cluster_aggregations` / `query_narrative`（schema 見 `docs/spec.md § 3.2`）
- Streamlit dashboard（reads 上述 tables）

## 待 grill 項（實作前再開 session 細談）

### G8 sub-decisions（spec § 10 + 本 session 大方向已定，細節 deferred）

| Item | Status | 備註 |
|---|---|---|
| 4 角色 model assignment | ✅ 定（gate Nano / extract Mini / aggregator + narrative Standard）| GPT-5.4 family，OpenAI strict；介面契約見下方 ▼Q2 |
| Pydantic schema 4 個 | ✅ 大方向定（已查證 strict 全 tier 支援 + ordering）| reasoning/reason 第一欄；strict（all-required, no default）；見 ▼Q2 |
| Voting 策略（k=1/3/borderline）| ✅ 大方向定 | gate 邊緣 k=3、aggregator 必 k=3、其他 k=1 |
| **Few-shot 範例選哪幾個**（per role） | ⏳ 細談 | gate 用 probe 09/10 confirmed-dup pair；extraction 從 DB 手挑 6 篇 React answer；aggregator 手 craft 1 worked example；narrative 手 craft 2 |
| **Refusal / disagreement fallback 具體 retry 邏輯** | ⏳ 細談 | borderline confidence threshold、k=3 voting fallback、failed answer 排除規則 |
| **Manual review pile 操作** | ⏳ 細談 | 怎麼存、怎麼回 review、review 完怎麼 update DB |
| Prompt 版本 pinning + DB column 記錄 | ⏳ 細談 | `_model_version` / `_prompt_version` / `_voting_agreement` 等 column 規格 |

#### ▼Q2 已定案（2026-05-29 grill）：LLM interface contract

- **Provider：OpenAI first，但 call site 走 provider-neutral seam**：`llm_call(messages, schema: type[BaseModel], tier, k_voting, temperature)`。換 provider = 改 env + 重寫 adapter 內部，不動 call site。近期不做 cross-provider 品質比較，故不為假想可攜把 schema 弄殘。
- **擁抱 OpenAI strict Structured Outputs**（已查證 gpt-5.4 nano/mini/standard 全 tier 支援，constrained decoding）：
  - 換來：漏欄位 / 亂 enum / 型別錯不可能發生；產不出合法物件給明確 error → parse/retry 邏輯整類消失（縮小 G8b refusal / G12 failure 表面積）。
  - 換來：欄位順序被 generation 遵守 → 「reasoning/reason 第一欄」CoT 順序自動成立。
  - schema 硬規則：所有欄位 `required`、`additionalProperties:false`、**不准 default**（Pydantic 別用 `Field(default=...)`，optional 用 nullable union）。
  - **cardinality constraint（如 conditions 0–3）走 Python post-validate**，不寫進 provider schema（版本浮動 + 最不可攜）。
- **Seam blast radius（未來真換 provider 才付）**：schema translation（strict dialect → Anthropic tool-use / Gemini responseSchema）+ CoT 改走 pre-call text thinking。記此即可。
- ⚠️ **Doc drift 待修（批次）**：spec § 5 / § 10 / § 六 + ADR 0002 仍寫 Claude Haiku 4.5 / Gemini；ADR 0002 的 ~$0.05/query 也綁 Haiku 舊估。待 schema 串定案後一次同步。

#### ▼Q3 已定案（2026-05-29 grill）：conditions 欄位角色 = P3

- `conditions` = **recorded + visible annotation，不進 cluster boundary**。Step-2 aggregator 與 companion HDBSCAN **都只吃 practice 句**（輸入對稱 → ARI 純測方法差，乾淨）。
- minority position 靠「被抽出 + 存 DB + detail panel 顯示 + narrative LLM 看得到」保住，不靠 boundary。真正該切開的對立由 practice **句本身**措辭不同承載（extraction prompt 責任）。判斷依據：React answer「同句 practice、conditions 對立」罕見。
- **extraction schema 定型**：`reasoning(第一欄,不存) → practice:str(1句) → conditions:list[str](0–3, Python validate) → evidence_type:enum[prose|code|both]`。
- ⚠️ batch doc：ADR 0002「conditions … prevent minority-position erasure」改寫為「recorded + visible, **not** boundary-forcing」。

#### ▼Q6 已定案（2026-05-29 grill）：query framing 收寬 + extraction schema = M3

- **Query framing 收寬**：query = 開放實作問題「這問題有哪些實作解法？」；產出 = emergent community practice breakdown（authority-weighted）。**不**預設「已命名的 X-vs-Y 選擇」。涵蓋舊 persona，且讓更多 2023–2026 how-to 問題成為合法 demo query（但 selection 仍挑 practice 分散度高的，避開單一修法死 bug）。
- **B 解決**：consensus 物件 = **prescription（實作解法）**。**診斷層收斂 = correctness，不是 consensus 訊號 → 不升成 breakdown 軸**；純解釋型答案 → 空 practice list（排除）。narrative 可順手描述「社群對成因高度一致」。
- **A = M3**：`AnswerExtraction.practices: list[Practice]`（空 = 無實作解法）。一答多解 = 多票（忠於「呈現有哪些 practice」）。default beeswarm 暫用每篇 primary practice（一答一點不動）；**一答多點 viz 延到 D-5**。
- `Practice = {practice:str(1句), conditions:list[str](只記明講但書、Python clamp 3、不進 cluster boundary ▼Q3), evidence_type:Literal[prose|code|both]}`。
- **slice 驗證（N=35 = 21 canonical + 14 dup-spread）**：46 practices / 14 multi；空 practice 7（2 純診斷 + 5 薄答含 len6）校準良好；**evidence_type 有區別力**（both27/code8/prose11，非全 both）→ 留；conditions 收緊後 6/46（多為真但書）。extraction 眼驗忠實、approach landscape 豐富（head: functional updater/useEffect；tail: ref-lib/custom hooks/promise-wrap）。cost 35 call ≈ $0.047 → 全 163 ≈ $0.22。
- ⚠️ batch doc：CONTEXT.md Query framing 釐清；spec §1.1/§2.10 + ADR 0005 persona「implementation choice」收寬為「implementation problem → surface practices」；ADR 0002 加 multi-practice（M3）；ADR 0004 一答多點（D-5 再定）。

### G11 Probe-into-production 迭代計畫（Module D 角度）

避免「整個 pipeline 寫完才發現 Step-2 aggregator 不對」。為 Module D 設 mini-probe milestones：

> **▼資料實況 reality-check（2026-05-29 grill，查 `data/so_data_reactjs.db`）**
> - react 61,256 Q / 62,357 A，**A/Q = 1.02**，window **2023–2026**（2023 獨大）。
> - 單題 answers 最多 **25**；≥20A 僅 3 題、≥30A **0**；≥10A 共 **27** 題，**幾乎全是 debugging/config 類**（vite/babel/NextJS hydration/tailwind），非 implementation-choice 類（choice 辯論多在 2019–2021、不在窗內）。
> - pooled canonical grouping ≥10A = 292 群（@0.75，probe 01），但純 embedding ~86% blind、真 canonical grouping = Module C 未建。
> - ⇒ spec **N=50 / 7y trajectory 綁 2019–2022 backfill**（spec § 10 高優先，Module A）。現階段 Probe D-1/D-2 = 在 2023 debugging 類資料驗 **mechanics**；persona-aligned **choice-query demo blocked on backfill**，誠實設限。

> **▼Q5 已定案（2026-05-29 grill）：D-1/D-2 共用 stub = `q54069253` duplicate chain**
> - **Anchor `q54069253`** "The useState set method is not reflecting a change immediately"（2019, score 908, 21 ans, [reactjs]）—— React 新手第一週必撞的「setState 沒馬上生效」概念牆，光 2023–2026 窗內就有 **132 題**被標 dup。
> - **為何完美 fixture**：同一困惑、社群給**真分歧 practice** —— 解釋分兩派（#1 accepted「非同步」vs #2「不是 async、是 closure」公開反駁）+ 多解法（functional updater / useEffect / useRef / useReducer+Context）。天然 authority-dominant vs 異議 + 多 position，低異質風險。
> - **pooled ~163 answers, multi-year**：canonical 21（2019）+ 132 in-DB dups 的 142 答案（2023:82 / 2024:57 / 2025:3）→ N 夠大讓 **D-2 ARI 成真數字**（非 N=14 plumbing）+ 年份跨度可試 **trajectory（D-5）**，甚至可能是 finding（2019「functional setState」→ 2023「React 18 batching」shift）。
> - **Storage（▼Q5b）**：out-of-window canonical 用 `probe_d/fetch_canonical.py` 拉成 `probe_d/data/canonical_54069253.json`（probe-local）；132 dups + 答案留主 DB。**不污染主 DB 2023–2026 窗 invariant，Module A 仍是乾淨 owner。**
> - **Authority guardrail**：2019 canonical 作者不在 2023–2026 PageRank 圖 → **D-3 authority overlay 需另處理**；D-1/D-2 content-only 不受影響。
> - **策略副產品**：「probe-local 選擇性 backfill」—— D 的 probe 可按需拉特定 chain，不必等 Module A 全 backfill。

- **Probe D-1**：對 stub（▼Q5）的 ~163 pooled answers 跑 Step 1 extraction → 看 extraction 品質 + 人工抽樣對齊度（pass criteria 見 ▼待定 D-1 items）。
- **Probe D-2**：同 group 跑 Step 2 → 看 cluster naming / member assignment 是否 reasonable；ARI vs companion
  - **D-2 結果（2026-05-29，N=46 practices / 3-small embedding stand-in）**：LLM aggregator k=1 分 16–18 群、head 乾淨（useEffect n=10 → functional-updater/ref/compute-direct/custom-helper 各 ~n=5 → reducer n=3）+ 長尾單例 → 漂亮的 breakdown shape。**companion HDBSCAN 退化（44/46 一坨，ARI 0.009）** —— density-from-scratch 對同質短句是錯工具。
  - **改 matched-k（k=LLM 群數）agglomerative/kmeans → ARI 0.38** → embedding 幾何在 matched 粒度下與 LLM 中度一致；失敗的是 HDBSCAN 不是 embedding（已查證 3-small MTEB ~62 > spec SBERT mpnet ~58 / MiniLM 56）。差距主因長尾單例。
  - ⚠️ **spec § 3.6 amendment**：companion 由 `HDBSCAN(min_cluster_size=2)` 改 **matched-k agglomerative**（k 借自 LLM = 共享粒度、grouping 仍獨立）。ARI 0.38 落在 spec § 4「<0.5 → report & discuss」分支（已預留）。正版 companion 仍用 **local SBERT**（理由=獨立於 LLM provider，非品質；▼Q2 精神）。
  - k=1 兩次跑出 16 vs 18 群 → stability 不足,佐證 k=3 voting 必要。
  - **k=3 co-association consensus（= `majority_vote_on_assignments` 的真正定義，已實作 `d2_consensus.py`）**:跑 k=3、對每對 practice 數共現、majority(≥2/3)連通分量為共識群。per-run 群數 17/16/16 不穩,但**共識 pair-level 極穩:overall within-cluster agreement 0.99、97% pairs 三次全同群**;head 全 1.00（useEffect n=10 / compute-local n=6 / ref n=5 / functional n=4 / reducer n=3 / promise-callback n=3），只 read-during-render 0.78、init-defaults 0.67（本就 borderline/debug 的低品質 practice）→ spec § 4「per-cluster > 0.7」幾乎全達標。
  - ⚠️ **兩個 agreement 軸要分開報**（spec 混為一談）:(i) LLM 跨次自我穩定 = **0.99**（reproducibility）;(ii) LLM vs 獨立 embedding = **~0.38**（cross-method defensibility）。答不同問題。
- **Probe D-3**：authority overlay deterministic 算 → 看數字是否符合預期
- **Probe D-4**：LLM narrative 拿 (2) + (3) 結果產 → 看可讀性
- **Probe D-5**：dashboard 視覺化 idiom 探索 → ✅ 完成（2026-05-30，互動雛形在 `probe_d/viz/`）
  - **idiom 定案（詳見 ADR 0004 amendment）**：default = **force-floating circle-pack + 有機 gooey blob**（cluster 邊界順節點長、非圓、可拖 node / 拖整顆 bubble）；**點 practice → 2D 連續時間 trajectory（舊→新，真實日期軸）= 主溫度計**；**時間 = 可展開維度、從顏色降級**；**3D Space-Time Cube = demo showpiece（非量化、orthographic、gated on backfill）**；beeswarm / circle-pack PNG = 靜態 finding 圖。
  - 共通編碼：size ∝ log(vote)（非原值）、accepted = 環、long-tail 收一束、authority = **vote proxy（pre-differentiator + age-confounded）**。
  - 生成器：`viz/make_force.py`（winner：漂浮有機 bubble + 拖移 + 點擊 trajectory）/ `make_traj.py`（純 2D 時間軸）/ `make_stc_cube.py`（3D showpiece）/ `build_data.py`（circlify pack + 真實日期組裝，需主 DB）。production 走 **D3/SVG**；layout / 動畫之後再調。
  - 資料修正：發現 canonical 答案 2019–2025 持續累積（非全 2019）→ trajectory 真有跨年料；但 vote proxy age-confounded、且 stub 早期作者不在 PageRank 圖（▼Q5 guardrail 不變）。
- 每個 probe pass criteria 待定（30 抽樣對齊率？ARI threshold？）

### G12 Failure / reproducibility（D 角度）

- LLM API down 時的處理（rate limit / refusal / timeout）
- Model version pinning 跨 session 重跑能不能還原同結果
- Companion HDBSCAN random_state pinning
- DB record 含足夠 metadata 可審計（model_version、prompt_version、temperature、voting agreement、refusal log）

## 下一步

- ✅ D-1 extraction / D-2 clustering（LLM aggregator + matched-k companion + k=3 co-association）probe 驗完（slice 46 practices）
- ✅ Docs 同步：新增 ADR 0006（query framing）/ 0007（extraction+clustering method）；spec §1.1/2.5/3.6/4/5/6/10 + CONTEXT.md + README + .env.example 已對齊
- ✅ **D-5 viz idiom 探索（2026-05-30）**：互動雛形定案 default-view idiom（見上 Probe D-5 + ADR 0004 amendment）；**force-floating 有機 bubble + 點擊 trajectory = 主互動模型**，3D cube 降為 showpiece
- 🟡 跑滿 163（2026-06-01）：**extraction ✅** — 192 practices / 163 答案、空 35（21.5%，vs slice 20% → 校準穩）、multi-practice 48（29%，vs slice 40%）、cost **$0.203**（估 $0.22 準）。
  - **finding — evidence_type 翻盤**：slice 是 both-dominant（27/46 = 59%）→ 全量變 **prose-dominant（119/192 = 62%）**，both 僅 22%、code 16%。主因：142 dup 長尾多為**低分薄答的純 prose 速解**，code-rich 的是 canonical head；slice 的分層抽樣過採了 head。3 桶仍非退化 → evidence_type **留**,但 viz 的 chip 會以 ¶ prose 為主(誠實反映答案母體)。conditions 15/192（7.8%，長尾更稀,仍「rare but real」）。
  - **② clustering ✅**（2026-06-01，$0.097）：k=3 co-association consensus → **24 multi-member 群 + 15 singleton→長尾**;within-cluster agreement **0.94**（slice 0.99）、unanimous 3/3 pairs 81%。head 乾淨（useEffect 45 › local-computed 26 › functional-updater 16 › read-on-render 11 › useRef/async 8 …），壓低總體 agreement 的全是 borderline debug 群（async 0.68 / array 0.63 / game 0.50）。`d2_consensus.py` 補了 writer 吐 build 格式 → promote（`data/extractions.json` + `clusters.json`）→ rebuild = **`viz_data.json` 192 pts / 23 clusters**（2 對同名被 breakdown 依名併 → 22 head + long-tail），真規模上線。
  - **finding — cross-method ARI「看似」隨規模下滑,實為粒度假象（D-2c 診斷 `d2_head_ari.py`，2026-06-01）**：companion ARI 0.38（slice）→ **0.255（full, k=39）**。A 診斷拆解:把 same k=39 partition restrict 到 head 子集 ARI 仍 ~0.25（n≥2 **0.259** / n≥3 0.255 / n≥5 0.246）→ 下滑**不是長尾拖累**;真因是 **k=39 太細**（15 singletons 把 matched-k 灌大 → agglomerative 過切,連 useEffect(45) 大 head 群都被嵌入端拆碎）。改成**只把 head 177 點 recluster 到 head 粒度 k=24 → ARI 0.422**（≈ 甚至略高於 slice 0.38）。⇒ **head breakdown 在嵌入幾何下可復原(~0.42 中度);0.255 是 singleton-inflated 的悲觀數字,非「規模大→方法崩」。** **方法修正**:defensibility ARI 應**排除 singletons / 對齊 head 粒度**再算（spec §3.6 companion 量法要註明）。reproducibility（LLM 自我一致 **0.94**）獨立守住 → **兩軸分開講;cross-method 數字對 k 極敏感。** caveat:3-small stand-in,非 local SBERT。
  - **finding — head 群內容效度審查（2026-06-01,對 anchor q54069253 逐群讀全文）**：24 head 群裡**前 ~8 群是真‧不同‧成立的解法 landscape**（useEffect-react 45 / use-local-value 26 / functional-updater 16 / read-next-render 11 / useRef 8 / custom-hooks 7 / derived-not-state 5 / reducer-context 3），核心 extract→cluster 在好答案上 work。但 **~1/3 群不是乾淨解法,分兩個來源**：
    - **A｜off-topic 漏入（dup-chain 過度池化）**：controlled-input[9] / event-propagation[17] / date-validation[21][22] / data-shape[20] —— 群內一致但根本是「別的 React 問題」。**決定性證據：這些群幾乎全 `canonical=0` + `score 0–1`。** ⇒ **dup-chain proxy 的相關性精度上限,量化證據,直指真 Module C（canonical grouping + LLM equivalence gate）的必要**。
    - **B｜薄答抽取雜訊**：content-free meta「use the approach shown」[15]、個案 debug（game/check[12][19]）、vague grab-bag（async[5] / set-direct[8]）—— 同一批 score-0 dup 薄答（呼應 prose-dominant drift）。
    - **可用槓桿（不必等 C）**：① 相關性 gate（判 practice 是否在答 anchor）② score/authority 地板（off-topic 群剛好全 score 0–1,一刀很乾淨）③ 濾 meta/個案句 ④ authority-weighted 視圖讓雜訊自然縮小（待 D-3）。
    - **score 地板診斷（2026-06-01,post-hoc 不重分群）**：`score≥1` 殺 4/5 個 A 群、8/8 GOOD 全留 → off-topic 確實集中 score 0–1;但 `≥2` 連 2 個冷門正解（custom-hooks/derived-not-state）也死 → **score 分不出「低分=off-topic」vs「低分=冷門但正確」,故不採 score 硬地板**（呼應 ▼Q3 別抹 minority）。真解是相關性,不是分數。
  - **boundary 修正 + 原則（2026-06-01,A 類來源驗證 → practice-層相關性是 D 份內）**：A 類 13 條拆解 → **10 條來自 5 個「純跑題」question（全 dup、整題不產正解 → C 能整題砍 → C 根因解）;3 條來自 2 個「混合」question（題對、也產 GOOD,C 必須留 → C 結構上碰不到 → 只能 D 處理）**。**原則:off-topic 可發生在 practice 粒度（比 C 的 question 粒度更細）→ practice-層相關性/品質過濾是 D 份內、且獨立於 C 必須有的（不是搶 C,是補 C 看不到的細粒度）。** practice-層 gate 是 superset（一次蓋全部 13 + B）;C 只能處理那 10（其根因仍屬 C,D 不該蓋 question-等價引擎去搶,但可把「全題被濾掉」當 mis-pooled 訊號回饋 C）。**非-D-不可的核心 = B 全部 + A 的 3 條混合題。** 證據 qid — 純跑題:77107721/77504568/78270769/78283173/78784444;混合:75072831/76726065。
  - **D-1b relevance gate 實作 + 套用 ✅（2026-06-01,/goal 自由設計）**：依上述原則做 practice-層 gate（`d1b_gate.py` / schema `RelevanceGate` / prompt `build_gate_messages`）。兩軸:**relevant**（只有 off-topic 到別的 React 子系統才丟）/ **substantive**（只有 content-free placeholder 或個案 one-off 才丟）,保守偏留。**iteration**:nano 第一版把 relevance 框架訂太窄 → 誤殺 34 條正解（連 985 分 functional-updater);**放寬框架成「這困惑下會用到的 state 手法全算」+ 升 mini tier** → GOOD 誤殺剩 **2 條且都站得住**(placeholder 措辭 / TS typing 修補)。**結果 192→146（丟 46:A 12/B 13/TANG 9/TAIL 10/GOOD 2）→ 重分群**:per-run [17,20,20]（前 [34,33,45]）、within-cluster agreement **0.94→0.98**、unanimous **81%→94%**、head **24→13 全是真 state 解法**(5 個 off-topic 假群 + placeholder/個案 junk 全消失;去雜訊後分群更穩)。viz 146 pts / 13 clusters（也順手解掉「群太多/timeline 壓迫」)。成本 gate $0.16 + recluster $0.065。**注:`data/extractions.json` 現為 post-gate 146;raw 192 留在 `probe_d/data/d1_extractions.json`。** 待 grill:hard-drop vs flag-降權（目前 hard-drop）、gate 是否納入正式 pipeline。
  - **D-4 narrative 設計定案 ✅（2026-06-02 grill;4 設計題敲完,先設計不跑、零成本）**:per-query 2–3 句 shape 描述,**骨架 = 把 spec §1.2 的 RQ2/RQ3/RQ4 對這個 query 散文化**（per-cluster gloss 沿用 aggregator name,不另跑）。
    - **① 對象**:per-query 整體 shape（非逐群）。
    - **② 涵蓋**:必出 **RQ2**（convergent/**mixed**/divergent,任何 query 都算得出）;看數字決定 **RQ3**（authority aligned/contrarian/dispersed）+ **RQ4**（settled/contested/converging/diverging,**coverage-gated**）。沿用 spec 既有 typology 詞彙。原則:**只講數字裡真的有的**（資料沒有就閉嘴;RQ4 原文「不對 coverage 不足做 trajectory claim」直接焊成 enum 必選項）。confound 免責（authority=vote-proxy）屬 dashboard/方法註記層,**不塞進 per-query 散文**——narrative 是純函數 `(signals)→prose`,不該管上游準備好沒（2026-06-02 user 切分:上游資料品質不阻塞 prompt 設計）。
    - **③ 輸入 signal 表（Python 預算,LLM 只轉述不重算）**:header（題目 / total practices / distinct answers / **per-year answer histogram** / **CROSS-YEAR COVERAGE 行** / overall agreement）+ 每 head 群（name·size·share·agreement·**distinct_backing_answers**·**peak_score**·**median_yr**）+ 長尾（數）。注:per-cluster 只給 median_yr(span 會被 outlier 拉寬誤導,見下實作 finding)。
    - **authority 歸因原則**:單位 = **answer 不是 practice**;**群內**對同一 answer 去重（一個權威答案在同群多 practice 只算一票）,**跨群保留**（權威作者確實同時背書多 position）。→ `distinct_backing_answers` 量「幾個**獨立**權威聲音撐這群」、`peak_score` 取群內 distinct answer 最高票;丟掉 sum / practice-level peak（會把 size 混進 authority）。文獻錨:multi-topic document decomposition（SCA, arXiv 2410.21054）合法跨群 + author-level aggregation（clust-LDA, arXiv 1810.02717）避免單一多產來源主導。
    - **④ schema（`QueryNarrative`,reasoning-first strict）**:`reasoning → shape[convergent|mixed|divergent] → dominant_approach:str → authority_alignment[aligned|contrarian|dispersed|inconclusive] → temporal[settled|contested|converging|diverging|insufficient_coverage] → headline:str → body:str`。兩護欄焊進 enum:`insufficient_coverage`（RQ4 coverage gate）/ `inconclusive`（票數太平的誠實出口）。**observation（自由有趣觀察）parked 到 v2**——剛鎖好「不亂編」,先不開編輯後門;要加時走 C 方案「每觀察必錨定一個 datum、否則 null」。
    - **runner**:`tier="narrative"`(gpt-5.4),走 `llm_call` seam;schema 入 `probe_d/schemas.py`、prompt 入 `prompts.py`（`build_narrative_messages`,含 RQ 骨架指令）、runner `probe_d/build_narrative.py`。
    - **plumbing 驗證 ✅（2026-06-02,從 viz_data 端到端算出真 signal 表）**:`viz_data.json` 每 point 已帶 `answer_id`/`vote`/`year`/`date`/`authority`/`is_accepted`/`cluster` → authority 群內去重 + year 統計**全程不碰主 DB**。驗證副產:
      - **去重真有作用**:`Reducer/context` 群 size 2 但 **distinct answers = 1**（同一答案兩 practice → 其實零獨立佐證）;functional-updater 17→16、custom-hooks 8→6、derived 6→4。practice-peak 會高估。
      - **RQ3 訊號浮現**:peak 985 同時現於 useEffect(45)/functional(17)/set-direct(5),544 跨 local-value/refs/read-next-render → 權威集中 head,但靠 **2 個 mega-answer（985/544）跨群**撐（正是 RQ3 要辨的「全才答案掃全場」vs「獨立權威各據一方」）。
      - **RQ4 會正確回 `insufficient_coverage`**:per-year answer = {2019:2,2020:4,2021:5,2022:1,**2023:59,2024:39**,2025:2} → 98/112 擠 2023–24（dup 窗偏誤）,median 幾乎全 2023–24,year_span 的 2019 只是 1–2 老答案拉長。跨年趨勢不可觀測 → 護欄第一個真 query 就用上。
      - **唯一 plumbing 缺口 → 實作 TODO**:per-cluster `agreement`（reproducibility）沒存進 viz_data（clusters 只有 id/name/n/x/y/r）,只在 `d2_consensus.py` 暫算就丟。需在其 writer 把 `agreement` 寫進 `clusters.json` → build 透傳到 viz_data,narrative 才吃得到。小改、無 LLM 成本。
  - **D-4 narrative 實作 + 驗證 ✅（2026-06-02,dry-run→eval→修正→重跑）**:(a) agreement plumbing（`d2_consensus.py` writer 存 agreement + 回填 `clusters.json` 13 值,未重跑 LLM)→(b) `QueryNarrative` schema + `build_narrative_messages`（2 手刻 few-shot)+ `build_narrative.py`（預設乾跑、`--run` 才花錢)→(c) 乾跑驗 signal 表 →(d) 真跑 ~$0.01。**第一跑抓到護欄漏洞 → 修 → 重跑**:
    - **漏洞**:per-cluster `year(med·span)` 的寬 span 是 1–2 篇老答案拉出的假象,壓過 query-level histogram → LLM 誤判 `temporal=contested`(正是要擋的「歪斜分布編趨勢」)。
    - **三修**:① header 加中性 `CROSS-YEAR COVERAGE` 行（top-2 busiest year 佔比）;② per-cluster 年份欄 span→**median-only**(去 outlier 假象);③ prompt 收緊 temporal「**只看 query-level coverage、不准看 per-cluster median**」。
    - **shape 改三分（設計反轉,有據）**:第一個真 query 落灰色帶（最大群 45 vs 次 25 = **1.8×**、31% 沒過半),二分太硬 → `convergent/mixed/divergent`,判準用「最大 vs 次大**倍率**」（≥2× 或近過半→conv;<1.5×→div;之間→mixed)而非武斷 % 門檻;body 一律講出 share,標籤不綁架讀者。
    - **重跑結果（忠實)**:`shape=mixed · dominant=useEffect-react · authority=dispersed · temporal=insufficient_coverage`。headline「One explanation leads, but several others stay substantial」;body 講出 31%(45/146)、0.99 群內共識、點名 rival、誠實認「too concentrated in 2023–24 to support a trend」。**護欄第二跑正確觸發。**
    - **留 v2**:① authority=dispersed 是軟判斷（leader 其實握最高票 985 + 最多獨立背書 43,aligned 也成立）——RQ3 現用 `peak_vote`,改「每群獨立高票**數**/票數和」可能變 aligned;② observation 自由欄;③ headline "explanation" 用詞小疵;④ persist narrative→json + 注入 dashboard 頂部大字。
    - **2026-06-02 對齊合併 pipeline ✅**:資料 promote 到 `d1_gated`（合併 extract+gate)→ recluster（agreement writer 自動持久化,手動回填作廢)→ rebuild viz_data → narrative 重驗:**順跑且忠實**(`mixed/dispersed/insufficient_coverage` 同形)。artifacts 已 code==data 自洽。**確切 counts / cluster 數待整體合併那輪再正式記錄(現階段不拘泥數字,只確立能順跑 + 結果理想)。**
  - **D-4 → dashboard 接線 ✅（2026-06-02,v2 ④ 結清）**:narrative 上 dashboard,走 producer→consumer seam:`build_narrative.py --run` 持久化 `data/query_narrative.json` → `build.py` 讀進 `Breakdown.narrative` → `contract.to_dict()` 出 `viz_data.meta.narrative` → `render.py` 渲染。**渲染 idiom = 「The Editorial」magazine 結構**:載入時 narrative 走 **overture**（泡泡場 dim + stagger reveal),點 explore 收成頂部 **standfirst ribbon**;階層 = kicker（context)→ **query（主題,大字 hero)**→ headline（LLM take,斜體 dek)→ lede（2–3 句)→ 3 顆 RQ 判詞 chip（`insufficient_coverage`/`inconclusive` 渲成虛線淡色 = 護欄可見化)。bubbles/timeline 切換移到**底部中央 pill**(空出頂部給 ribbon);timeline 軸上抬讓位。`meta.narrative` 為 null 時整塊 no-op（向後相容)。
    - **Module C 接縫**:`query` + `group_size` 目前是 `build_narrative.py` 的 placeholder（`QUERY_TITLE`/`GROUP_SIZE`;query 取真 canonical 標題自 `canonical_q54069253.json`,group_size=有貢獻答案的 distinct question 數)。整合時 Module C 應供:每 query 的 canonical 標題 + pooled question 數 + 觸發本 narrative step。`query_narrative.json` = 真 D-4 模型輸出（`--run` 持久化、已 committed;~$0.01/query),Module C 整合時換真 query/group_size。
  - **待**:D-3 真 authority（卡 Module B + backfill）、viz production 化（前端 Svelte+D3,已議但 park)。
  - **viz polish backlog（2026-06-01 真資料眼驗,留給前端那輪）**：(i) 23 群偏多 → timeline y 軸 23 排被壓扁、bubbles 標籤重疊;考慮 head 群數上限 / 合併同名 / 可摺疊長尾。(ii) force 太擠,泡內外節點互相壓到重疊 → layout「輕一點」(調 collision radius / charge / size scale 給呼吸空間)。(iii) 群可拖移探索(cluster-level reposition,非只 node drag)。
- ⏭ **D-3 authority overlay**：lite 已在 viz 用 vote/accept proxy（age-confounded，標 pre-differentiator）；真 network authority（yearly PageRank）等 Module B（+ 2019–2022 backfill 才涵蓋 stub 早期答案）
- ⏭ **viz production 化**：D3/SVG 實作 force+trajectory（layout / 動畫調校）；接 DB tables
- ⏭ 仍 deferred：G8（few-shot 擴充 / refusal fallback / manual review pile / prompt pinning）、G12（failure / version pinning / audit metadata）
- 🟢 **整合架構 grill 定案（2026-06-02）→ ADR 0009**（Runtime topology & delivery architecture）：runtime topology / bake→serve→present / SQLite 匯流排 / 冪等可續跑 `bake(query_id)` driver（7 步）/ D 側 schema（answer-keyed `practice_extractions` 快取 + `practice_clusters` 實體 + `query_practice` 合併 gate+歸屬 + `query_viz_cache`，relevance 走 flag）/ 4 endpoint API（含 `POST /queries` 預留 async）/ SvelteKit SPA + live d3-force + d3-zoom 地圖平移 + fit-to-view + SVG / 三幕 UX（query→processing→dashboard）。spec §3.1.2 / §3.2 / §5 已對齊。
  - **fixture 改定位**：`q54069253` = 「**D 機制驗證**」fixture（extraction/cluster/narrative 已驗）。接 authority 後實測它 **~98% vote-weight out-of-window**（substantive 高票答案在 `canonical_q54069253.json`、不在 2021–2026 網絡圖）→ authority **showcase 換 in-window query**，只按 coverage + answer/author 分散度挑、**不按主題預分類**（shape emergent，ADR 0001/0006；見記憶 emergent-over-pre-classification）。
  - **authority reality-check（dry, persist=False, $0, 2026-06-02）**：跑得動 — graph 145k 節點 / 216k 邊、modularity 0.65、scheme=weighted（PR/rep Spearman 0.51）。fixture 92/111 對到 user 但高票全 NULL（canonical 答案 out-of-DB）。authority deps 已補進 `pyproject.toml`（networkx/python-louvain/scipy/pandas）；真 DB = `data/so_data_react_2021_2026.db`（`build.py` 寫死的 `so_data_reactjs.db` 待修）。
  - **下一步**：persist authority（寫 authority 表）→ 寫 `PageRankAuthorityProvider`（answer→`owner_user_id`→該年 yearly PR percentile；out-of-graph 標明確 null、非 size 0）→ 重烤 q54069253 免費驗 plumbing → 挑 in-window showcase query 跑 D pipeline。
- 依賴：Module B authority lookup（D-3 PageRank；`src/authority/`）/ Module C `query→canonical_group`（**已有實作 `src/canonical/`**：embed→vector search→LLM gate、CLI 形狀；待包成 provider 接上，目前仍用 ▼Q5 dup-chain stub）
