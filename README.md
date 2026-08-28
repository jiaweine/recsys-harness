<div align="center">

# 序枢 · Recsys Harness

### Search / Recommendation Control & Evolution Plane

**把真实搜推系统的“发现问题 → 实验候选 → 业务回放 → 时间留出验证 → 信任/激活 → 监控回滚 → 长期学习”收进一个受权限约束、可审计、可恢复的 Agent Harness。**

<sub>XUSHU · SEARCH / RECOMMENDATION AGENT HARNESS</sub>

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

**Business Reward · Temporal Replay · Mixed Strategy Genome · Independent Guardrails · Durable Recovery**

[产品](#真实产品) · [价值闭环](#生产价值闭环) · [Agent Harness](#agent-harness) · [自进化](#垂直自进化) · [接入](#existing-system-integration) · [启动](#快速启动) · [配置](#配置) · [数据](#生产数据契约) · [架构](#系统架构) · [可靠性](#可靠性与安全)

</div>

---

## 真实产品

<p align="center"><sub>DESKTOP · FULL WORKSPACE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/overview.png" alt="序枢真实运行界面" width="96%">
</p>
<p align="center"><sub>一个任务面：目标、对话、运行状态、证据、附件与可恢复执行保持在同一上下文。</sub></p>

> **Real product, real browser.** 截图来自仓库实际启动的应用。CI 使用真实浏览器验证桌面与移动流程，并把 README 资产固定到不可变 commit。

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <strong>任务工作台</strong><br>
      <sub>目标、附件、联网权限与执行入口保持在同一操作面。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/workbench.png" alt="序枢工作台" width="100%">
    </td>
    <td width="50%" valign="top" align="center">
      <strong>证据与判断</strong><br>
      <sub>真实动作、轨迹、来源、验证和结论集中在检查视图。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/evidence.png" alt="序枢证据面板" width="100%">
    </td>
  </tr>
</table>

### Mobile · task first

<p align="center"><sub>MOBILE · SAME 393 × 852 VIEWPORT · THREE REAL STATES</sub></p>

<table>
  <tr>
    <td width="33%" valign="top" align="center"><strong>01 · 主任务</strong><br><sub>Workspace · 对话 / 结论 / 执行</sub></td>
    <td width="33%" valign="top" align="center"><strong>02 · 执行轨迹</strong><br><sub>Bottom sheet · 状态 / 实际动作</sub></td>
    <td width="33%" valign="top" align="center"><strong>03 · 判断依据</strong><br><sub>Bottom sheet · 证据 / 来源</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/mobile-workspace.png" alt="序枢移动端主任务" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/mobile-progress.png" alt="序枢移动端执行轨迹" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/mobile-evidence.png" alt="序枢移动端判断依据" width="92%"></td>
  </tr>
</table>

---

# 产品价值

序枢不是“又一个推荐模型库”，也不是“让 Agent 多调用几个工具”。

它面向搜推团队最核心的一条工作链：**从异常和机会出发，形成可验证策略，再把验证结果沉淀为可持续复用的系统能力。**

```text
线上 / 离线信号
      ↓
问题定位与证据收集
      ↓
策略候选生成
      ↓
业务回放 + Domain Guardrails
      ↓
Future Temporal Holdout
      ↓
置信度与回归门槛
      ↓
Trusted Strategy Memory
      ↓
Permissioned Activation
      ↓
Revalidation / Retirement
```

传统搜推优化往往分散在日志、Notebook、实验平台、代码仓库和个人经验里。序枢把这些步骤收进同一个可追踪的证据链，让每一次优化都能回答三个问题：

1. **为什么要改？** —— 异常、目标与证据是什么；
2. **为什么这个候选更好？** —— 业务 reward 与独立 guardrail 是否共同支持；
3. **为什么可以信任？** —— 是否经过独立 holdout、回归门槛和权限控制。

### 产品级指标

真正值得跟踪的不是“Agent 有多少功能”，而是：

- **MTTD / MTTDg**：异常出现到发现、诊断需要多久；
- **Experiment lead time**：一个策略想法到可信验证需要多久；
- **Bad-candidate rejection**：多少坏策略在进入线上实验前被拦住；
- **Rollback latency**：退化出现到策略退休需要多久；
- **Validated uplift**：累计产生多少经过业务 reward 与独立验证支持的改进。

---

# 生产价值闭环

序枢把 **业务价值** 与 **体验保护指标** 分开建模。

coverage、diversity、freshness、novelty、cold-start、NDCG、Recall 等指标负责描述相关性、覆盖和体验结构；业务 reward 则负责回答候选策略是否真正改善了目标结果。

```text
Business Reward      → primary routing objective
Domain Guardrails    → relevance / experience constraints
Temporal Holdout     → generalization evidence
Confidence / Gates   → trust criteria
Strategy Memory      → reusable strategy knowledge
```

## 1 · RewardSpec：由业务定义价值

```json
{
  "reward_spec": {
    "weights": {
      "click": 0.5,
      "favorite": 1.5,
      "cart": 2.0,
      "purchase": 5.0,
      "hide": -2.0,
      "refund": -5.0
    },
    "inverse_propensity_cap": 20
  }
}
```

每条 production outcome 的 reward：

```text
configured weight(event) × event.value
```

业务可以用同一份 contract 表达不同目标：

- 电商：purchase / GMV / refund；
- 内容：watch time / completion / skip；
- 社区：follow / dwell / hide；
- 搜索：click / conversion / reformulation penalty。

> **RewardSpec 是业务 contract，而不是 evolution gene。** 优化器搜索策略，业务价值定义保持独立。

## 2 · ExposureEvent：保留真实曝光上下文

生产评估与用户画像行为分开存储。

```text
request_id
 timestamp
 surface         search | recommend
 user_id/query
 item_id
 event
 value
 propensity
 position
 policy_id
 model_version
 experiment_id
 metadata
```

`interactions` 用于构造 profile；`events` 用于描述真实 request、策略版本、曝光位置与最终 outcome。

这让 replay、temporal holdout、策略版本追踪与业务 reward 基于同一套可审计事实工作。

## 3 · Logged replay

候选策略会对历史 request 重新执行排名，并基于记录到的业务 outcome 计算 request-level policy value。

```text
historical request
       ↓
candidate policy
       ↓
new ranking
       ↓
logged rewarded / penalized items
       ↓
rank discount
       ↓
optional capped inverse propensity
       ↓
request-level policy value
```

评估结果显式报告：

```text
business_reward
business_reward_coverage
request_scores
estimator
propensity_rows
```

Estimator 类型保持可见：

```text
logged_replay
propensity_weighted_logged_replay
```

不同 evaluation basis、数据覆盖度和估计方式都会进入 evidence，策略比较始终保留可追踪的统计语义。

## 4 · Temporal future holdout

生产 events 按 request identity 与时间切分 discovery / holdout：

```text
older request identities
       ↓
production discovery
       ↓
candidate routing
       ↓
newer request identities
       ↓
future holdout
```

核心约束：

- 同一个 `request_id` 不跨 discovery / holdout；
- future holdout 时间晚于 discovery；
- business holdout 与 Search relevance / Rec warm-user guardrail holdout 使用独立证据；
- durable trust 需要多 request 的 future evidence。

## 5 · Paired confidence

在 future holdout 上，reference 与 candidate 按相同 `request_id` 做 paired comparison：

```text
candidate(request) - reference(request)
              ↓
paired bootstrap
              ↓
delta + CI95 + probability_positive
```

策略进入 trusted memory，由 uplift、置信度、future evidence 与 domain regression gate 共同决定，而不是只看单次最优分数。

---

# Agent Harness

序枢把搜推优化建模成 **Mission-driven evidence loop**，而不是固定 tool sequence。

```text
Goal + Authority
       ↓
Mission Graph
       ↓
Deliberation
       ↓
Tool / Capability
       ↓
Observation
       ↓
Reflection
       ↓
Hypothesis / Contradiction / Requirement update
       ↓
Trajectory Critic
   ┌───┴────────┐
replan        close
              ↓
       Result Verifier
```

每个 Evidence Requirement 都包含：

```text
key · domain · tool · priority
prerequisites · status · satisfied_by
```

Harness 追踪的是**还缺什么证据才能完成任务**，并根据 observation、contradiction 和 verifier 结果持续更新 mission state。

一次 Search / Recommendation mission 可以同时拥有：

- 明确目标；
- 可见 authority；
- 可恢复 trajectory；
- 结构化 evidence；
- 可验证 conclusion；
- 可复用 strategy memory。

---

# 垂直自进化

## Mixed Strategy Genome

序枢把连续参数与离散 capability 放在同一个 strategy genome 中搜索：

```text
Strategy Genome
├─ continuous genes
│  ├─ Search ranking blend
│  ├─ Recommendation warm ranking blend
│  └─ independent diversity / cold-start genes
└─ capability genes
   ├─ Search: query / candidate / rerank
   └─ Rec: profile / candidate / cold-start / exploration / rerank
```

`CapabilityRegistry` 声明 **what exists**；数据、reward 与 guardrail 决定 **what wins**。

## 两种 evaluation basis

### Offline / demo evaluation

在没有 production events 的工作区，系统使用 domain metrics 构造可重复的离线 evaluation：

```text
evaluation_basis = proxy_metrics
business_trusted = false
```

包括：

- Search NDCG / Recall / MRR；
- Recommendation coverage / freshness / diversity / novelty；
- cold-start probe；
- robustness；
- capability / continuous response surface。

这些指标用于验证排序行为、体验结构与策略响应面。

### Production reward evaluation

当 RewardSpec + production events 存在时，策略进入 production value loop：

```text
Mixed Genome
    ↓
Business Reward Replay ──────┐
                            │ primary routing signal
Domain Guardrails ───────────┘ safety constraints
    ↓
Response Surface
    ↓
Posterior-guided Mixed Arms
    ↓
Population + QD Archive
    ↓
Future Temporal Holdout
    ↓
Paired Confidence + Regression Gates
    ↓
Trusted Strategy Memory
```

业务 reward 负责主要 routing；NDCG / Recall、coverage、freshness、cold-start 与 worst-slice metrics 作为独立 guardrail，避免局部收益破坏整体体验。

---

# Active strategy lifecycle

```text
candidate
   ↓
production discovery + guardrails
   ↓
future holdout
   ↓
trusted
   ↓ explicit user authority
active
   ↓
periodic validation
   ├─ business reward regression → retire
   ├─ relevance / coverage / cold regression → retire
   └─ pass → keep active
```

Production-aware strategy memory 保存：

- strategy score；
- `evaluation_basis`；
- `business_validation`；
- guardrail evidence；
- activation authority；
- revalidation / retirement state。

**Trusted 与 Active 分层。** 策略先通过证据门槛进入 durable trust，再根据显式 authority 进入 active lifecycle。

---

# Existing system integration

序枢内建 Search / Recommendation reference engine，同时支持把已有 serving system 作为真实评估对象接入。

```python
from lingjing_harness import (
    AdapterSearchEngine,
    CallableSearchAdapter,
    RewardSpec,
)
from lingjing_harness.production import evaluate_logged_policy

adapter = CallableSearchAdapter(
    lambda query, limit: my_search_service(query=query, limit=limit)
)
engine = AdapterSearchEngine(adapter)

report = evaluate_logged_policy(
    events,
    surface="search",
    reward_spec=RewardSpec(weights={"click": 1, "purchase": 5}),
    search_engine=engine,
)
```

Serving adapter 会对外部返回结果执行统一约束：

- 统一成 `id`；
- 去重；
- 拒绝非有限 score；
- 保持确定排名；
- 进入同一套 business replay 与 evidence loop。

因此 Elasticsearch、OpenSearch、Vespa、内部 rank API、已有 recommendation service 都可以通过 adapter 纳入统一评估流程，而不需要先迁移 serving architecture。

---

# 快速启动

要求：**Python 3.11+**。

## 本地运行

```bash
git clone https://github.com/jiaweine/recsys-harness.git
cd recsys-harness
python -m venv .venv
```

### macOS / Linux

```bash
source .venv/bin/activate
python -m pip install -e .
python -m uvicorn lingjing_harness.api:app --host 127.0.0.1 --port 8765
```

### Windows PowerShell

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m uvicorn lingjing_harness.api:app --host 127.0.0.1 --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

CLI：

```bash
xushu-harness "做一次全局体检"
```

## Docker

```bash
docker build -t xushu-recsys-harness .
docker run --rm -p 8765:8765 xushu-recsys-harness
```

## 开发验证

```bash
python -m pip install -e ".[dev]"
make check
make test
make demo
python scripts/probe_harness_contract.py
```

> 「序枢 / Xushu」是产品品牌；`lingjing_harness` Python import namespace 与 `LINGJING_*` 环境变量作为兼容接口保留。

---

# 配置

复制 `.env.example` 后按需要设置：

```text
LINGJING_ENV
LINGJING_DATA_DIR
LINGJING_WEB_SEARCH_URL
LINGJING_WEB_SEARCH_KEY
LINGJING_VISION_BASE_URL
LINGJING_VISION_MODEL
LINGJING_VISION_API_KEY
LINGJING_ACCESS_TOKEN
LINGJING_SESSION_TTL_SECONDS
LINGJING_COOKIE_SECURE
LINGJING_TRUST_PROXY_IP
```

外部搜索和本地多模态感知按需启用；production access token、secure cookie 与 trusted proxy 配置用于部署访问控制。

---

# 生产数据契约

完整格式见 [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)。

最小 production value 数据：

```json
{
  "items": [
    {"id": "sku-1", "title": "商品 A"}
  ],
  "reward_spec": {
    "weights": {
      "click": 1,
      "purchase": 5,
      "hide": -2
    }
  },
  "events": [
    {
      "request_id": "r-1",
      "timestamp": 100,
      "surface": "recommend",
      "user_id": "u-1",
      "item_id": "sku-1",
      "event": "click",
      "position": 1,
      "propensity": 0.5,
      "policy_id": "prod-v1"
    }
  ]
}
```

`Catalog.summary()` 报告：

```text
production_events
production_requests
search_replay_requests
recommend_replay_requests
business_reward_ready
```

`data.inspect` 会把 evaluation basis 与可用 production evidence 暴露给 Harness，作为任务规划与验证依据。

---

# 系统架构

<p align="center"><sub>AGENT HARNESS RUNTIME · CONTROL · EVIDENCE · EVOLUTION · TRUST · STATE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/architecture.svg" alt="序枢 system architecture" width="97%">
</p>

```text
                     ┌─────────────────────────────┐
                     │ Goal + User Authority       │
                     └──────────────┬──────────────┘
                                    ↓
                    Mission Graph / Deliberation
                                    ↓
                      Real Search / RecSys Tools
                                    ↓
                         Observation / Reflection
                                    ↓
                          Trajectory Critic
                                    ↓
──────────────────────────────── TRUST PLANE ────────────────────────────────
                                    ↓
                     Mixed Strategy Candidate Space
                                    ↓
         ┌──────────────────────────┴───────────────────────────┐
         ↓                                                      ↓
Domain Guardrails                                      Production Events
NDCG / Recall / cold / coverage                       + RewardSpec
         ↓                                                      ↓
         └──────────────────── Response Surface ─────────────────┘
                                    ↓
                       Posterior Routing + QD
                                    ↓
                         Future Temporal Holdout
                                    ↓
                       Paired Confidence / Gates
                                    ↓
                      Trusted / Permissioned Active
                                    ↓
                       Revalidation / Retirement
                                    ↓
                       Typed Durable Strategy Memory
```

详细架构：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)  
Harness contract：[`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md)  
Vertical evolution：[`docs/VERTICAL_EVOLUTION.md`](docs/VERTICAL_EVOLUTION.md)  
Acceptance：[`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)

---

# 可靠性与安全

序枢把可靠性约束放在 runtime、evidence、strategy 与 production value loop 中共同执行：

- Mission Graph / checkpoint resume；
- adaptive invocation idempotency；
- strategy schema canonicalization；
- cold-start independent probe；
- query / request identity isolation；
- SQLite worker lease + heartbeat + fencing；
- workspace revision；
- production auth / rate limit / CSP；
- attachment TTL / evidence retention；
- network permission isolation；
- stale worker 不得覆盖较新的 durable state。

Production value invariants：

```text
business reward != proxy quality
request identity cannot cross temporal split
durable trust requires future evidence
business regression can retire active strategy
proxy validation cannot hide business regression
RewardSpec cannot be evolved by optimizer
```

这些规则让每次自动化改进都能回答：**依据是什么、谁允许的、失败后怎么恢复。**

---

# Repository map

```text
frontend/                          序枢产品 UI
lingjing_harness/
  production.py                    RewardSpec / ExposureEvent / temporal replay / bootstrap
  adapters.py                      existing-system serving adapters
  domain.py                        Catalog + training data + production evidence
  algorithms/
    search.py                      Search mixed genome + execution stages
    recommend_core.py              Rec mixed genome + cold-start / exploration / rerank
    capabilities.py                typed capability registry + config validation
    evaluation.py                  domain guardrails + explicit business reward reporting
    evolution_core.py              mixed response surface / posterior / QD primitives
    production_evolution.py        business-routed search / recommend evolution
    evolution.py                   public evolution surface + trust evidence gate
  runtime/
    harness.py                     Agent Harness orchestration / durable loop
    deliberation.py                Mission Graph / hypotheses / reflection / critic
    tools_core.py                  compatibility core lifecycle
    tools_production.py            business-aware memory / active rollback lifecycle
    tools.py                       stable ToolRegistry import surface
    verifier.py                    result / authority verification
    memory.py                      episodic / procedural / policy memory
  api.py                           API / auth / workspace / recovery
  store.py                         runs / leases / revisions / shared rate limit
tests/test_production_value_loop.py
                                   reward / temporal / replay / evolution regressions
tests/test_serving_adapters.py     external serving adapter regressions
docs/ARCHITECTURE.md               production value architecture
docs/DATA_FORMAT.md                production event / reward contract
```

---

# 质量门槛

```bash
make check
make test
make demo
python scripts/probe_harness_contract.py
```

CI 验证：

- Python compile + full pytest；
- Mission / Deliberation / Harness contracts；
- mixed genome / capability stages；
- production reward / temporal request split / bootstrap；
- malformed external adapter output；
- strategy lifecycle / recovery / fencing；
- CLI / wheel clean install；
- frontend syntax / product hygiene；
- desktop / mobile product browser flow。

---

<div align="center">

### Search / Recommendation is the domain. Measurable improvement is the product.

**Observe · Diagnose · Replay · Evolve · Holdout · Verify · Activate · Rollback · Learn**

<sub>Business reward first · Domain guardrails always · Authority explicit</sub>

</div>
