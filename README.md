<div align="center">

# 序枢 · Recsys Harness

### Search / Recommendation Control & Evolution Plane

**把真实搜推系统的“发现问题 → 实验候选 → 业务回放 → 未来留出验证 → 信任/激活 → 监控回滚 → 长期学习”收进一个受权限约束、可审计、可恢复的 Agent Harness。**

<sub>XUSHU · SEARCH / RECOMMENDATION AGENT HARNESS</sub>

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

**Business Reward · Temporal Replay · Mixed Strategy Genome · Independent Guardrails · Durable Recovery**

[真实产品](#真实产品) · [产品价值](#产品价值) · [生产价值闭环](#生产价值闭环) · [Agent Harness](#agent-harness) · [垂直自进化](#垂直自进化) · [快速启动](#快速启动) · [数据](#生产数据契约) · [架构](#系统架构) · [边界](#当前边界)

</div>

---

## 真实产品

<p align="center"><sub>DESKTOP · FULL WORKSPACE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1ee9612ad33f86b46672830a3834ed355637bd46/docs/readme-assets/overview.png" alt="序枢真实运行界面" width="96%">
</p>
<p align="center"><sub>一个任务面：目标、对话、运行状态、证据、附件与可恢复执行保持在同一上下文。</sub></p>

> **Real product, real browser.** 截图来自仓库实际启动的应用。CI 使用真实浏览器验证桌面/移动流程，并把 README 资产固定到不可变 commit。

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <strong>任务工作台</strong><br>
      <sub>目标、附件、联网权限与执行入口保持在同一操作面。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1ee9612ad33f86b46672830a3834ed355637bd46/docs/readme-assets/workbench.png" alt="序枢工作台" width="100%">
    </td>
    <td width="50%" valign="top" align="center">
      <strong>证据与判断</strong><br>
      <sub>真实动作、轨迹、来源、验证和结论集中在检查视图。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1ee9612ad33f86b46672830a3834ed355637bd46/docs/readme-assets/evidence.png" alt="序枢证据面板" width="100%">
    </td>
  </tr>
</table>

### Mobile · task first

<p align="center">
  <sub>MOBILE · SAME 393 × 852 VIEWPORT · THREE REAL STATES</sub>
</p>

<table>
  <tr>
    <td width="33%" valign="top" align="center"><strong>01 · 主任务</strong><br><sub>Workspace · 对话 / 结论 / 执行</sub></td>
    <td width="33%" valign="top" align="center"><strong>02 · 执行轨迹</strong><br><sub>Bottom sheet · 状态 / 实际动作</sub></td>
    <td width="33%" valign="top" align="center"><strong>03 · 判断依据</strong><br><sub>Bottom sheet · 证据 / 来源</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1ee9612ad33f86b46672830a3834ed355637bd46/docs/readme-assets/mobile-workspace.png" alt="序枢移动端主任务" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1ee9612ad33f86b46672830a3834ed355637bd46/docs/readme-assets/mobile-progress.png" alt="序枢移动端执行轨迹" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1ee9612ad33f86b46672830a3834ed355637bd46/docs/readme-assets/mobile-evidence.png" alt="序枢移动端判断依据" width="92%"></td>
  </tr>
</table>

---

# 产品价值

序枢不是“又一个推荐模型库”，也不是“让 Agent 多调用几个工具”。

它要解决的是搜推团队长期反复发生、但通常散落在日志、Notebook、实验平台、代码仓库和人工经验里的工作：

```text
线上/离线异常
    ↓
到底是 query、候选、排序、冷启动、探索还是数据问题？
    ↓
应该先试哪种改动？
    ↓
这个候选是 NDCG 更高，还是业务真的更好？
    ↓
有没有污染 future holdout？
    ↓
能不能安全记住 / 激活？
    ↓
上线后退化谁负责发现和回滚？
```

序枢把这些步骤变成同一个证据链。

### 我们真正关心的产品指标

不是“Agent 有多少功能”，而是：

- **MTTD / MTTDg**：搜推异常出现到发现、诊断需要多久；
- **Experiment lead time**：一个策略想法到可信验证需要多久；
- **Bad-candidate rejection**：多少坏策略在进入线上实验前被拦住；
- **Rollback latency**：线上/生产回放退化到自动退休策略需要多久；
- **Validated uplift**：累计产生多少经过业务 reward + 独立验证支持的改进。

---

# 生产价值闭环

上一版最大的产品缺口是：推荐 `quality` 主要由 coverage / diversity / freshness / novelty / cold-start 等 proxy 组成。

这些指标很重要，但它们**不等于业务价值**。

例如：

```text
更高 diversity
+ 更高 freshness
+ 更高 coverage
≠ 一定更高 CTR / CVR / GMV / watch time / retention
```

因此当前主线引入了独立的生产价值层。

## 1 · RewardSpec：业务定义价值，optimizer 不替业务拍脑袋

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

所以业务可以自己表达：

- 电商：purchase / GMV / refund；
- 内容：watch time / completion / skip；
- 社区：follow / dwell / hide；
- 搜索：click / conversion / reformulation penalty。

> **RewardSpec 是产品 contract，不是进化 gene。** optimizer 没有权限偷偷把业务目标改成更容易优化的指标。

## 2 · ExposureEvent：知道“当时真正展示了什么”

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

`interactions` 可以构造用户 profile；`events` 则回答：

> 哪个真实 request、由哪个策略、在什么位置展示了什么，最后发生了什么？

这是做生产 replay、未来 holdout、策略版本追踪的基础。

## 3 · Logged replay

候选策略会对历史 request 重新执行排名，并用业务 reward 对排名结果打分。

```text
historical request
       ↓
candidate policy
       ↓
new ranking
       ↓
logged rewarded/penalized items
       ↓
rank discount
       ↓
optional capped inverse propensity
       ↓
request-level policy value
```

返回：

```text
business_reward
business_reward_coverage
request_scores
estimator
propensity_rows
```

### 统计边界

当前 estimator 明确叫：

```text
logged_replay
propensity_weighted_logged_replay
```

它**不是**被包装成“完整无偏 OPE”。没有历史曝光的 item 没有真实 outcome；后续可以接 IPS / SNIPS / DR estimator，但当前 README 不提前宣称已经实现。

## 4 · Temporal future holdout

生产 events 不再用普通 hash/random split 作为默认泛化假设。

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

- 一个 `request_id` 永远不能跨 discovery / holdout；
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

样本不足不会被“漂亮置信区间”掩盖；public trust gate 至少需要两个 paired future request，同时还需要 domain guardrail 有独立 holdout。

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

Harness 追的是**还缺什么证据**，不是“下一个固定步骤是什么”。

## 需要准确说明的一点

当前 `DeliberationEngine` 仍然持有一部分 Search/Recommendation mission semantics，例如搜索复现、推荐 audit、候选验证等 requirement。

所以：

> **Evolution search space 已经 schema/capability-driven；Mission compiler 还不是完全 capability-declared。**

这是当前真实边界，不能因为项目强调“自进化”就把中心 controller 剩余的 domain code 隐去。

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

如果没有 RewardSpec + production events：

```text
evaluation_basis = proxy_metrics
business_trusted = false
```

系统仍可以：

- Search NDCG / Recall / MRR；
- Recommendation coverage / freshness / diversity / novelty；
- cold-start probe；
- robustness；
- capability/continuous response surface。

但不会把这些 proxy 说成“业务增长”。

### Production reward 存在

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

业务 reward 成为主要 routing objective；NDCG/Recall、coverage、freshness、cold-start 和 worst-slice 退回它们更合理的位置：**保护体验的 guardrail**。

---

# Existing system integration

序枢的内建 Search / Recommendation 是 reference engine，不是要求企业迁移的目标架构。

当前提供 read-only serving adapter contract：

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

> 当前 external adapter 是 read-only evaluation contract。自动修改外部 production policy 仍要求接入方提供显式 typed write/experiment contract；序枢不会猜远端参数后直接修改生产服务。

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
- Harness fork 仍保持 production-aware ToolRegistry。

---

# 快速启动

要求：**Python 3.11+**。

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

开发验证：

```bash
python -m pip install -e ".[dev]"
pytest -q
python scripts/probe_harness_contract.py
```

> 「序枢 / Xushu」是产品品牌；`lingjing_harness` Python import namespace 和已有 `LINGJING_*` 环境变量目前只作为历史兼容接口保留。

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

`Catalog.summary()` 会直接报告：

```text
production_events
production_requests
search_replay_requests
recommend_replay_requests
business_reward_ready
```

`data.inspect` 会明确告诉你当前工作区是：

- 已有 production reward evidence；还是
- 只能做 proxy evaluation。

---

# 系统架构

<p align="center"><sub>AGENT HARNESS RUNTIME · CONTROL · EVIDENCE · EVOLUTION · TRUST · STATE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@1ee9612ad33f86b46672830a3834ed355637bd46/docs/readme-assets/architecture.svg" alt="序枢 system architecture" width="97%">
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

---

# 可靠性

已有工程边界继续保留：

- Mission Graph / checkpoint resume；
- adaptive invocation idempotency；
- strategy schema canonicalization；
- cold-start independent probe；
- query/request identity isolation；
- SQLite worker lease + heartbeat + fencing；
- workspace revision；
- production auth / rate limit / CSP；
- attachment TTL / evidence retention；
- network permission isolation；
- stale worker 不得覆盖当前状态。

新增 production value invariants：

```text
business reward != proxy quality
request identity cannot cross temporal split
one future request cannot certify durable trust
business regression can retire active strategy
proxy validation cannot hide business regression
RewardSpec cannot be evolved by optimizer
```

---

# 当前边界

这次更新解决的是最核心的“价值函数错误”，但序枢还没有假装自己已经完成整个生产实验基础设施。

当前仍需继续推进：

1. **完整 OPE estimator suite**：IPS / SNIPS / Doubly Robust；
2. **Segment-conditioned strategy portfolio**：不同 query/user/session pathology 使用不同 verified strategy basin；
3. **Online experiment adapters**：A/B、interleaving、canary、traffic allocation；
4. **External typed write contract**：安全发布外部 serving policy；
5. **Latency / infra cost Pareto objective**：质量、业务收益、P99、成本共同选择；
6. **Capability-driven Mission Compiler**：移除 controller 里剩余的具体 tool/requirement taxonomy。

这些是明确的下一阶段，不写成“已经完成”。

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
    production_evolution.py        business-routed search/recommend evolution
    evolution.py                   stable public evolution surface + trust evidence gate
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
docs/ARCHITECTURE.md               current production value architecture
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

CI 同时验证：

- Python compile + full pytest；
- Mission/Deliberation/Harness contracts；
- mixed genome / capability stages；
- production reward / temporal request split / bootstrap；
- malformed external adapter output；
- strategy lifecycle / recovery / fencing；
- CLI / wheel clean install；
- frontend syntax / product hygiene；
- desktop/mobile product browser flow。

---

<div align="center">

### Search / Recommendation is the domain. Measurable improvement is the product.

**Observe · Diagnose · Replay · Evolve · Holdout · Verify · Activate · Rollback · Learn**

<sub>Business reward first · Domain guardrails always · Authority explicit</sub>

</div>
