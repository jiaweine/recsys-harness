<div align="center">

# 序枢 · Recsys Harness

### Search / Recommendation Control & Evolution Plane

**把真实搜推系统的“发现问题 → 实验候选 → 业务回放 → 时间留出验证 → 信任/激活 → 监控回滚 → 长期学习”收进一个受权限约束、可审计、可恢复的 Agent Harness。**

<sub>XUSHU · SEARCH / RECOMMENDATION AGENT HARNESS</sub>

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

**Business Reward · Temporal Replay · Mixed Strategy Genome · Independent Guardrails · Durable Recovery**

[产品](#真实产品) · [价值闭环](#生产价值闭环) · [Agent Harness](#agent-harness) · [自进化](#垂直自进化) · [接入](#existing-system-integration) · [启动](#快速启动) · [数据](#生产数据契约) · [架构](#系统架构) · [边界](#能力边界与扩展面)

</div>

---

## 真实产品

<p align="center"><sub>DESKTOP · FULL WORKSPACE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1f0651501fe2e29e5c020fd19f377994fc506430/docs/readme-assets/overview.png" alt="序枢真实运行界面" width="96%">
</p>
<p align="center"><sub>一个任务面：目标、对话、运行状态、证据、附件与可恢复执行保持在同一上下文。</sub></p>

> **Real product, real browser.** 截图来自仓库实际启动的应用。CI 使用真实浏览器验证桌面/移动流程，并把 README 资产固定到不可变 commit。

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

它面向搜推团队长期反复发生、却经常散落在日志、Notebook、实验平台、代码仓库和人工经验里的工作：

```text
线上 / 离线异常
    ↓
到底是 query、候选、排序、冷启动、探索还是数据问题？
    ↓
应该先试哪种改动？
    ↓
这个候选只是 proxy metric 更高，还是业务真的更好？
    ↓
验证有没有污染 temporal holdout？
    ↓
证据是否足以进入 durable trust？
    ↓
谁有权限激活？
    ↓
退化以后谁负责发现、退休和恢复？
```

序枢把这些步骤变成同一条可追踪的证据链。

| 序枢负责 | 序枢不假装负责 |
| --- | --- |
| 任务编排、真实工具执行、证据采集 | 用一个“总分”掩盖业务目标 |
| Search / Recommendation reference engine | 强迫企业迁移 serving stack |
| production reward replay 与 temporal holdout | 把 logged replay 包装成完整无偏 OPE |
| mixed strategy evolution 与独立 guardrail | 让 optimizer 改写 RewardSpec |
| trust / activate / revalidate / retire lifecycle | 在没有用户权限时自动改生产策略 |
| durable run / checkpoint / recovery / fencing | 用一次漂亮结果换取永久信任 |

### 产品级指标

真正值得跟踪的不是“Agent 有多少功能”，而是：

- **MTTD / MTTDg**：异常出现到发现、诊断需要多久；
- **Experiment lead time**：一个策略想法到可信验证需要多久；
- **Bad-candidate rejection**：多少坏策略在进入线上实验前被拦住；
- **Rollback latency**：生产回放退化到自动退休策略需要多久；
- **Validated uplift**：累计产生多少经过业务 reward + 独立验证支持的改进。

---

# 生产价值闭环

Coverage、diversity、freshness、novelty、cold-start 等指标很重要，但它们不等于业务价值：

```text
更高 diversity
+ 更高 freshness
+ 更高 coverage
≠ 一定更高 CTR / CVR / GMV / watch time / retention
```

序枢把 **业务 reward** 与 **体验 guardrail** 明确分层：业务定义价值，guardrail 负责阻止局部收益破坏整体体验。

## 1 · RewardSpec：业务定义价值

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

业务可以直接表达自己的价值函数：

- 电商：purchase / GMV / refund；
- 内容：watch time / completion / skip；
- 社区：follow / dwell / hide；
- 搜索：click / conversion / reformulation penalty。

> **RewardSpec 是产品 contract，不是进化 gene。** optimizer 没有权限把业务目标偷偷改成更容易优化的指标。

## 2 · ExposureEvent：记录真实展示与结果

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

`interactions` 可以构造用户 profile；`events` 回答另一类问题：

> 哪个真实 request、由哪个策略、在什么位置展示了什么，最后发生了什么？

这是 production replay、temporal holdout、策略版本追踪和回归判断的基础。

## 3 · Logged replay

候选策略会对历史 request 重新执行排名，并用业务 reward 对排名结果打分。

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

报告包含：

```text
business_reward
business_reward_coverage
request_scores
estimator
propensity_rows
```

### 统计语义

这一层的 estimator 明确命名为：

```text
logged_replay
propensity_weighted_logged_replay
```

它不是“完整无偏 OPE”。没有历史曝光的 item 没有真实 outcome；README 不把 ranking replay 的证据强度夸大成 counterfactual certainty。

## 4 · Temporal future holdout

生产 events 不使用普通 hash/random split 作为默认泛化假设。

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

硬约束：

- 一个 `request_id` 不能跨 discovery / holdout；
- future holdout 时间晚于 discovery；
- business holdout 与 Search relevance / Rec warm-user guardrail holdout 是两套独立证据；
- 一个 future request 不足以获得 durable trust。

## 5 · Paired bootstrap confidence

在 future holdout 上按相同 `request_id` 比较 reference 与 candidate：

```text
candidate(request) - reference(request)
              ↓
paired bootstrap
              ↓
delta + CI95 + probability_positive
```

样本不足不会被“漂亮置信区间”掩盖；public trust gate 至少需要两个 paired future request，同时要求 domain guardrail 有独立 holdout。

---

# Agent Harness

## Mission-driven，而不是固定 tool sequence

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

每个 Evidence Requirement 带：

```text
key · domain · tool · priority
prerequisites · status · satisfied_by
```

Harness 追的是**还缺什么证据**，不是“固定流程走到第几步”。

## Mission compiler 的职责边界

`DeliberationEngine` 承担一部分 Search / Recommendation mission semantics，例如搜索复现、推荐 audit、候选验证等 requirement；Evolution search space 则由 schema / capability 驱动。

这不是隐藏在 README 后面的实现细节，而是架构职责划分：

```text
Mission semantics
    ↓
Deliberation / requirements
    ↓
Capability-registry execution space
    ↓
Evidence-driven selection
```

---

# 垂直自进化

## Mixed Strategy Genome

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

`CapabilityRegistry` 只声明 **what exists**；数据和验证决定 **what wins**。

## 两种 evaluation basis

### 本地 / demo 数据

没有 RewardSpec + production events 时：

```text
evaluation_basis = proxy_metrics
business_trusted = false
```

系统仍可以评估：

- Search NDCG / Recall / MRR；
- Recommendation coverage / freshness / diversity / novelty；
- cold-start probe；
- robustness；
- capability / continuous response surface。

这些 proxy 用于体验质量与算法诊断，不会被写成“业务增长”。

### Production reward 数据

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

业务 reward 是主要 routing objective；NDCG / Recall、coverage、freshness、cold-start 和 worst-slice 回到更合理的位置：**保护体验的 guardrail**。

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

当 production reward 存在时：

- strategy memory 的 score 优先保存 business reward；
- memory payload 保存 `evaluation_basis` 和 `business_validation`；
- business regression 在 proxy validation 之前检查；
- proxy-only refresh 不能把 business regression 隐藏整个 TTL；
- Harness fork 保持 production-aware ToolRegistry。

**Trusted 不等于 Active。** 进入 durable trust 只代表证据达标；真正激活仍需要显式用户 authority。

---

# Existing system integration

序枢的内建 Search / Recommendation 是 reference engine，不是企业必须迁移到的目标 serving 架构。

对既有系统的接入入口是 read-only serving adapter contract：

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

外部返回结果会：

- 统一成 `id`；
- 去重；
- 拒绝非有限 score；
- 保持确定排名；
- 直接进入同一套 business replay。

因此 Elasticsearch、OpenSearch、Vespa、内部 rank API、已有 recommendation service 都可以先作为**真实评估对象**接入。

> external adapter 遵循 read-only evaluation contract。修改外部 production policy 需要接入方提供显式 typed write / experiment contract；序枢不会猜远端参数后直接修改生产服务。

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

外部搜索和本地多模态感知是可选能力；production access token、secure cookie 与 trusted proxy 配置用于部署访问边界。

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

`data.inspect` 会把工作区区分为：

- 已有 production reward evidence；
- 只能做 proxy evaluation。

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

# 可靠性与安全边界

工程层面持续要求这些 invariant 成立：

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

生产价值层额外要求：

```text
business reward != proxy quality
request identity cannot cross temporal split
one future request cannot certify durable trust
business regression can retire active strategy
proxy validation cannot hide business regression
RewardSpec cannot be evolved by optimizer
```

这些规则的目标不是让系统“更保守”，而是让每次自动化改进都能回答：**依据是什么、谁允许的、失败后怎么恢复。**

---

# 能力边界与扩展面

序枢核心聚焦在 **可验证、可授权、可回滚的搜推改进闭环**。有些能力适合通过显式 contract 扩展，而不是在核心里用隐式假设硬编码。

## Counterfactual OPE

`logged_replay` 与 `propensity_weighted_logged_replay` 保持 ranking replay 语义。若业务要使用 IPS / SNIPS / Doubly Robust，需要额外提供能成立的 stochastic-policy / propensity / direct-model contract，而不是从 rank score 或 position 猜概率。

## Online experiments

A/B、interleaving、canary、traffic allocation 应通过 typed experiment adapter 接入。离线可信候选只能获得“值得进入下一层验证”的资格，不自动等价于线上激活。

## External writes

对外部 Elasticsearch / Vespa / rank service 的自动写入必须有显式 schema、authority、版本和 rollback contract。read-only adapter 与 write authority 分离。

## Segment-conditioned portfolio

不同 query / user / session pathology 可以映射到不同 verified strategy basin；这类 routing 应继续遵循独立 holdout、最小证据量与 regression gate。

## Multi-objective optimization

Latency、P99、infra cost 可以与业务 reward、relevance、coverage 一起进入 Pareto 选择，但不能用成本指标覆盖业务价值或 guardrail 失败。

## Capability-driven mission compilation

更多 domain requirement 可以逐步下沉到 capability declaration；无论 compiler 如何扩展，Mission Graph 都必须保留 prerequisite、evidence requirement 与 authority semantics。

> README 只声明仓库能够证明的能力。扩展面写清 contract 和边界，不把计划能力提前包装成已交付能力。

---

# Repository map

```text
frontend/                          序枢产品 UI
lingjing_harness/
  production.py                    RewardSpec / ExposureEvent / temporal replay / bootstrap
  adapters.py                      existing-system read-only serving adapters
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
