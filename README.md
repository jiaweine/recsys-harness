<div align="center">

# 序枢 · Recsys Harness

### Agent Harness for Search & Recommendation Optimization

**让搜推优化从一次性实验，变成可验证、可授权、可恢复的持续进化闭环。**

<sub>XUSHU · SEARCH / RECOMMENDATION CONTROL & EVOLUTION PLANE</sub>

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

**Observe · Diagnose · Replay · Evolve · Holdout · Verify · Activate · Rollback · Learn**

[快速启动](#快速启动) · [真实产品](#真实产品) · [为什么是序枢](#为什么是序枢) · [核心能力](#核心能力) · [工作原理](#工作原理) · [系统接入](#existing-system-integration) · [架构](#系统架构) · [文档](docs/README.md) · [参与贡献](CONTRIBUTING.md)

</div>

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

也可以使用 Docker：

```bash
docker build -t xushu-recsys-harness .
docker run --rm -p 8765:8765 xushu-recsys-harness
```

CLI：

```bash
xushu-harness "做一次全局体检"
```

---

# 真实产品

<p align="center"><sub>DESKTOP · FULL WORKSPACE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@ed07172ddda7247ab54e2e39801202cbadaf523b/docs/readme-assets/overview.png" alt="序枢真实运行界面" width="96%">
</p>
<p align="center"><sub>目标、对话、运行状态、证据、附件与可恢复执行保持在同一个任务上下文。</sub></p>

> **Real product, real browser.** README 截图来自仓库实际运行界面；CI 使用真实浏览器验证桌面与移动流程，并把展示资产固定到不可变 commit。

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <strong>任务工作台</strong><br>
      <sub>目标、附件、联网权限与执行入口位于同一操作面。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@ed07172ddda7247ab54e2e39801202cbadaf523b/docs/readme-assets/workbench.png" alt="序枢工作台" width="100%">
    </td>
    <td width="50%" valign="top" align="center">
      <strong>证据与判断</strong><br>
      <sub>动作、轨迹、来源、验证结果与结论集中在检查视图。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@ed07172ddda7247ab54e2e39801202cbadaf523b/docs/readme-assets/evidence.png" alt="序枢证据面板" width="100%">
    </td>
  </tr>
</table>

### Mobile · task first

<table>
  <tr>
    <td width="33%" valign="top" align="center"><strong>01 · 主任务</strong><br><sub>Workspace · 对话 / 结论 / 执行</sub></td>
    <td width="33%" valign="top" align="center"><strong>02 · 执行轨迹</strong><br><sub>状态 / 实际动作</sub></td>
    <td width="33%" valign="top" align="center"><strong>03 · 判断依据</strong><br><sub>证据 / 来源</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@ed07172ddda7247ab54e2e39801202cbadaf523b/docs/readme-assets/mobile-workspace.png" alt="序枢移动端主任务" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@ed07172ddda7247ab54e2e39801202cbadaf523b/docs/readme-assets/mobile-progress.png" alt="序枢移动端执行轨迹" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@ed07172ddda7247ab54e2e39801202cbadaf523b/docs/readme-assets/mobile-evidence.png" alt="序枢移动端判断依据" width="92%"></td>
  </tr>
</table>

---

# 为什么是序枢

搜推系统真正困难的部分，通常不只是“再训练一个模型”或“再调一组参数”。

问题更常发生在完整工作链上：异常怎么定位、候选怎么生成、指标和业务价值怎么对齐、验证是否可信、谁有权限激活、退化以后如何恢复，以及一次有效经验能否成为下一次任务的起点。

序枢把这些原本分散在日志、Notebook、实验平台、服务代码和人工经验里的步骤，收进同一条 **evidence-driven improvement loop**：

```text
线上 / 离线信号
      ↓
诊断与证据收集
      ↓
策略候选生成
      ↓
Business Reward + Domain Guardrails
      ↓
Replay / Counterfactual Evaluation
      ↓
Future Temporal Holdout
      ↓
Confidence + Regression Gates
      ↓
Trusted Strategy Memory
      ↓
Permissioned Activation
      ↓
Revalidation / Retirement
```

每一次优化都应该能回答三个问题：

1. **为什么要改？** —— 目标、异常和证据是什么；
2. **为什么这个候选更好？** —— 业务 reward 与独立 guardrail 是否共同支持；
3. **为什么可以信任？** —— 是否经过独立 holdout、置信度判断、回归门槛和权限控制。

### 关注的结果

- **更快诊断**：缩短异常出现到定位原因的时间；
- **更快实验**：缩短策略想法到可信验证的 lead time；
- **更早淘汰坏候选**：在进入更高成本验证前拦住退化策略；
- **更快恢复**：让 revalidation 与 retirement 成为策略生命周期的一部分；
- **更多可复用 uplift**：把有效改进沉淀成带证据的 durable strategy memory。

---

# 核心能力

<table>
  <tr>
    <td width="33%" valign="top">
      <strong>Agent Harness</strong><br><br>
      Mission Graph 驱动任务执行，围绕缺失证据持续规划、调用真实工具、观察、反思与验证，而不是依赖固定 tool sequence。
    </td>
    <td width="33%" valign="top">
      <strong>Business-aware Evaluation</strong><br><br>
      RewardSpec 与独立 guardrail 共同评估候选；具备显式 policy probability contract 时，可进一步运行 IPS / SNIPS / DR、overlap diagnostics 与 experiment eligibility gate。
    </td>
    <td width="33%" valign="top">
      <strong>Mixed Strategy Evolution</strong><br><br>
      连续 ranking 参数与离散 capability 进入同一 strategy genome，通过 response surface、posterior routing 与 QD archive 搜索候选。
    </td>
  </tr>
  <tr>
    <td width="33%" valign="top">
      <strong>Temporal Trust</strong><br><br>
      Discovery 与 future holdout 按 request identity 和时间隔离，paired confidence 与 regression gates 共同形成 trust evidence。
    </td>
    <td width="33%" valign="top">
      <strong>Strategy Lifecycle</strong><br><br>
      Candidate、Trusted、Active、Revalidation、Retirement 是不同状态；策略激活与长期保留都依赖明确 evidence 和 authority。
    </td>
    <td width="33%" valign="top">
      <strong>Durable Runtime</strong><br><br>
      Checkpoint、resume、idempotency、worker lease、heartbeat、fencing、workspace revision 与 strategy memory 共同保证任务可恢复执行。
    </td>
  </tr>
</table>

---

# 工作原理

## 1 · Mission-driven Agent Harness

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
Hypothesis / Requirement Update
       ↓
Trajectory Critic
       ↓
Result Verifier
```

每个 Evidence Requirement 都带有：

```text
key · domain · tool · priority
prerequisites · status · satisfied_by
```

Harness 关注的不是“流程走到第几步”，而是**还缺什么证据才能完成任务**。

## 2 · Business Reward 与 Domain Guardrails

业务价值与体验保护指标分开建模：

```text
Business Reward      → primary routing objective
Domain Guardrails    → relevance / experience constraints
Temporal Holdout     → generalization evidence
Confidence / Gates   → trust criteria
Strategy Memory      → reusable strategy knowledge
```

RewardSpec 由业务定义：

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

同一 contract 可以表达电商、内容、社区和搜索等不同业务目标。优化器负责搜索策略，业务价值定义保持独立。

## 3 · Logged Replay + Counterfactual OPE + Temporal Holdout

候选策略可以先对历史 request 重新排名，并基于记录到的 production outcome 形成 request-level policy value：

```text
historical request
       ↓
candidate policy
       ↓
new ranking
       ↓
logged outcomes
       ↓
rank discount / propensity weighting
       ↓
request-level policy value
```

评估报告保留 `business_reward`、coverage、request scores、estimator 与 propensity 信息，让策略比较始终拥有可追踪的 evidence。

当接入方能够提供 logging policy 与 target policy 对**同一个 logged action** 的显式概率时，序枢还提供 contextual-bandit OPE：

```text
CounterfactualRecord
       ↓
IPS / SNIPS / optional DR
       ↓
raw + clipped ESS
support / overlap diagnostics
       ↓
bootstrap confidence
       ↓
ExperimentCriteria
       ↓
eligible_for_online_test
```

Experiment gate 使用 raw importance-weight ESS、support coverage、clipped share、estimated delta 与 probability-positive 等显式阈值判断候选是否具备进入受控线上实验的证据；这个 decision 不会自动授予 activation authority。详见 [`docs/COUNTERFACTUAL_EXPERIMENTS.md`](docs/COUNTERFACTUAL_EXPERIMENTS.md)。

随后，production events 按 request identity 与时间进入 future holdout：

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

Reference 与 candidate 在同一 future request 上做 paired comparison，并结合 confidence 与 domain regression gate 形成 trust evidence。

## 4 · Mixed Strategy Genome

```text
Strategy Genome
├─ continuous genes
│  ├─ Search ranking blend
│  ├─ Recommendation warm ranking blend
│  └─ diversity / cold-start genes
└─ capability genes
   ├─ Search: query / candidate / rerank
   └─ Rec: profile / candidate / cold-start / exploration / rerank
```

`CapabilityRegistry` 声明 **what exists**；数据、reward 与 guardrail 决定 **what wins**。

有 production reward 时，策略进入完整 production value loop：

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

离线工作区则使用 Search NDCG / Recall / MRR、Recommendation coverage / freshness / diversity / novelty、cold-start probe、robustness 等 domain metrics 形成可重复 evaluation。

## 5 · Active Strategy Lifecycle

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
   ├─ business regression → retire
   ├─ domain regression   → retire
   └─ pass                → keep active
```

Production-aware strategy memory 保存 score、evaluation basis、business validation、guardrail evidence、activation authority 与 revalidation state，让“发现一个好策略”和“长期安全使用这个策略”成为同一生命周期的一部分。

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

Serving adapter 会统一外部返回结果的 identity、去重与 score 约束，并把结果接入同一套 business replay 和 evidence loop。

Elasticsearch、OpenSearch、Vespa、内部 rank API、已有 recommendation service 都可以通过 adapter 纳入统一评估流程，而不需要改变原有 serving architecture。

如果已有策略系统能够导出对 logged action 的 logging / target action probabilities，也可以直接接入 `CounterfactualRecord`，在同一项目里完成 IPS / SNIPS / DR 与 controlled-experiment eligibility evaluation。

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

`Catalog.summary()` 会报告 production events、request 数量、Search / Recommendation replay request 与 business reward readiness；`data.inspect` 将这些 evidence 提供给 Harness 用于任务规划和验证。

---

# 系统架构

<p align="center"><sub>AGENT HARNESS RUNTIME · CONTROL · EVIDENCE · EVOLUTION · TRUST · STATE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@ed07172ddda7247ab54e2e39801202cbadaf523b/docs/readme-assets/architecture.svg" alt="序枢 system architecture" width="97%">
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

进一步阅读：

- [`docs/README.md`](docs/README.md) — documentation index；
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system architecture；
- [`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md) — Harness contract；
- [`docs/COUNTERFACTUAL_EXPERIMENTS.md`](docs/COUNTERFACTUAL_EXPERIMENTS.md) — IPS / SNIPS / DR and experiment gates；
- [`docs/VERTICAL_EVOLUTION.md`](docs/VERTICAL_EVOLUTION.md) — vertical evolution；
- [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md) — production data contract；
- [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) — acceptance criteria。

---

# 可靠性与安全

序枢把可靠性约束贯穿 runtime、evidence、strategy 与 production value loop：

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
- stale worker protection。

Production value invariants：

```text
business reward != proxy quality
request identity cannot cross temporal split
durable trust requires future evidence
business regression can retire active strategy
proxy validation cannot hide business regression
RewardSpec cannot be evolved by optimizer
```

这些 invariant 让自动化改进始终保留三件事：**证据、权限、恢复路径。**

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

> 「序枢 / Xushu」是产品品牌；`lingjing_harness` Python import namespace 与 `LINGJING_*` 环境变量作为兼容接口保留。

---

# 开发与质量门槛

贡献指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)。Issue 与 Pull Request 入口使用结构化模板，鼓励提交可复现步骤、验证 evidence 与受影响 contract。

```bash
python -m pip install -e ".[dev]"
make check
make test
make demo
python scripts/probe_harness_contract.py
```

CI 覆盖：

- Python compile + full pytest；
- Mission / Deliberation / Harness contracts；
- mixed genome / capability stages；
- production reward / temporal request split / bootstrap；
- IPS / SNIPS / DR + experiment eligibility contracts；
- serving adapter validation；
- strategy lifecycle / recovery / fencing；
- CLI / wheel clean install；
- frontend syntax / product hygiene；
- desktop / mobile real-browser flow。

<details>
<summary><strong>Repository map</strong></summary>

```text
frontend/                          序枢产品 UI
lingjing_harness/
  production.py                    RewardSpec / ExposureEvent / temporal replay / bootstrap
  counterfactual.py                explicit IPS / SNIPS / DR + overlap diagnostics
  experiments.py                   counterfactual evidence → controlled experiment gate
  adapters.py                      existing-system serving adapters
  domain.py                        Catalog + training data + production evidence
  algorithms/
    search.py                      Search mixed genome + execution stages
    recommend_core.py              Rec mixed genome + cold-start / exploration / rerank
    capabilities.py                typed capability registry + config validation
    evaluation.py                  domain guardrails + business reward reporting
    evolution_core.py              mixed response surface / posterior / QD primitives
    production_evolution.py        business-routed search / recommend evolution
    evolution.py                   public evolution surface + trust evidence gate
  runtime/
    harness.py                     Agent Harness orchestration / durable loop
    deliberation.py                Mission Graph / hypotheses / reflection / critic
    tools_production.py            business-aware memory / active lifecycle
    verifier.py                    result / authority verification
    memory.py                      episodic / procedural / policy memory
  api.py                           API / auth / workspace / recovery
  store.py                         runs / leases / revisions / shared rate limit
docs/                              architecture / contracts / data / OPE / acceptance
```

</details>

---

<div align="center">

### Search / Recommendation is the domain. Measurable improvement is the product.

**Business reward first · Domain guardrails always · Authority explicit**

<sub>序枢把一次次搜推优化，变成可以验证、记住、激活、回滚并继续学习的系统能力。</sub>

</div>
