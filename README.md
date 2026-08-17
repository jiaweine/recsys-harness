<div align="center">

# Recsys Harness

### 垂直自进化的 Search / Recommendation Agent Harness

**让搜推系统不只会执行和诊断，还能在真实领域指标、隔离验证、策略一致性与权限边界内，自主重组能力、实验、学习、恢复并持续进化。**

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

**Mission Graph · Mixed Strategy Genome · Capability Evolution · Failure-oriented Evaluation · Independent Holdout · Durable Recovery**

[真实产品](#真实产品) · [为什么是 Harness](#为什么是-harness) · [Agent Harness 方法](#agent-harness-方法) · [垂直自进化](#垂直自进化从参数到能力) · [快速启动](#快速启动) · [能力](#能力) · [可靠性](#可靠性) · [系统架构](#系统架构) · [质量门槛](#质量门槛)

</div>

---

## 真实产品

<p align="center"><sub>DESKTOP · FULL WORKSPACE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@bbe47c417b279d6eb65d5758f1f011d6254725e1/docs/readme-assets/overview.png" alt="Recsys Harness 真实运行界面" width="96%">
</p>
<p align="center"><sub>一个任务面：目标、对话、运行状态、证据、附件与可恢复执行保持在同一上下文。</sub></p>

> **Real product, real browser.** 截图来自仓库实际启动的应用。CI 用真实浏览器执行产品任务，并在视觉 QA 通过后刷新 README 资产。

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <strong>任务工作台</strong><br>
      <sub>输入、附件、权限与执行入口保持在同一操作面。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@bbe47c417b279d6eb65d5758f1f011d6254725e1/docs/readme-assets/workbench.png" alt="Recsys Harness 工作台" width="100%">
    </td>
    <td width="50%" valign="top" align="center">
      <strong>证据与判断</strong><br>
      <sub>真实动作、轨迹、依据与结论集中在检查视图。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@bbe47c417b279d6eb65d5758f1f011d6254725e1/docs/readme-assets/evidence.png" alt="Recsys Harness 证据面板" width="100%">
    </td>
  </tr>
</table>

### Mobile · task first

移动端不复刻桌面三栏。主任务永远是第一层，轨迹与证据作为 bottom sheet 进入，关键交互保持可触达尺寸并考虑 safe-area。

<p align="center">
  <sub>MOBILE · SAME 393 × 852 VIEWPORT · THREE REAL STATES</sub><br>
  <strong>三张图使用完全一致的列宽与展示尺度。</strong>
</p>

<table>
  <tr>
    <td width="33%" valign="top" align="center"><strong>01 · 主任务</strong><br><sub>Workspace · 对话 / 结论 / 执行</sub></td>
    <td width="33%" valign="top" align="center"><strong>02 · 执行轨迹</strong><br><sub>Bottom sheet · 状态 / 实际动作</sub></td>
    <td width="33%" valign="top" align="center"><strong>03 · 判断依据</strong><br><sub>Bottom sheet · 证据 / 来源</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@bbe47c417b279d6eb65d5758f1f011d6254725e1/docs/readme-assets/mobile-workspace.png" alt="Recsys Harness 移动端主任务" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@bbe47c417b279d6eb65d5758f1f011d6254725e1/docs/readme-assets/mobile-progress.png" alt="Recsys Harness 移动端执行轨迹" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@bbe47c417b279d6eb65d5758f1f011d6254725e1/docs/readme-assets/mobile-evidence.png" alt="Recsys Harness 移动端判断依据" width="92%"></td>
  </tr>
</table>

---

## 为什么是 Harness

搜索与推荐不是一个“模型调用”问题，而是一套长期运行的决策系统问题：**面对模糊业务目标，系统如何知道还缺什么证据、该执行哪个真实能力、何时改变假设、何时停止、什么结果有资格学习，以及学到的策略能否在下一次任务里安全复用。**

Recsys Harness 把这些问题放进一个持久、可审计、可恢复、受权限和独立验证约束的运行时。

| 普通 tool-calling agent | Recsys Harness |
|---|---|
| 生成一个计划后顺序调用工具 | 先编译 Mission Graph；每个 observation 后重新 deliberation |
| 只记录“调用了什么” | 记录 targeted requirement、utility、hypothesis、alternatives、rationale |
| 工具结果直接进入下一段文本 | observation 先更新 requirement / hypothesis / contradiction |
| 模型觉得“够了”就结束 | Trajectory Critic 判断 terminality，ResultVerifier 再独立验收 |
| 历史对话就是 memory | episodic / procedural / policy memory 分开、受限、可退休 |
| 优化就是调几个权重 | mixed genome 同时探索参数与真实搜推 capability |
| 架构选择靠开发者写死 | registry 声明可用实现；真实领域评估决定哪个实现获胜 |
| 同一批样本既提案又验收 | evaluation identity 先去重；discovery 与 independent holdout 隔离 |
| 冷启动只看 warm-user 汇总指标 | cold-start 有独立 probe、独立 holdout delta 与退化门槛 |
| 历史配置拿来就执行 | active strategy 先 canonicalize；失效 capability / 非法 gene 会退休而不是静默执行 |
| 重启后重新跑 | mission、reflection、critic、actions、observations 一起 checkpoint / resume |
| 联网或附件可能影响权限 | observation 与 authority 严格分离；只有用户能扩大权限 |

> **核心原则：** 自主性不是“让 Agent 想做什么就做什么”。自主性是让它在证据、领域指标、权限、风险、预算、验证和恢复边界内，持续选择并进化当前最有价值的策略；而这些边界本身不属于 optimizer 的搜索空间。

---

## Agent Harness 方法

### 0 · Runtime composition

`AgentHarness` 是运行时主体；`OwnedPolicy` 编译目标与 authority；`DeliberationEngine` 维护任务级推理状态；Search / RecSys 的长期策略优化由受验证约束的 vertical evolver 完成。

<table>
  <tr>
    <td width="33%" valign="top"><strong>MissionGraph</strong><br><sub>Evidence Requirements、依赖、Hypotheses 与 exit criteria。</sub></td>
    <td width="33%" valign="top"><strong>DeliberationEngine</strong><br><sub>按证据缺口、多信号 utility、风险与历史收益决定下一动作。</sub></td>
    <td width="33%" valign="top"><strong>TrajectoryCritic</strong><br><sub>检查关键需求、矛盾、stagnation 与 clean-close 条件。</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top"><strong>ToolRegistry</strong><br><sub>真实 search / recommend / audit / evolve / network 工具及风险契约。</sub></td>
    <td width="33%" valign="top"><strong>CapabilityRegistry + Mixed Genome</strong><br><sub>参数 gene 与结构 capability gene 共用领域实验、credit assignment 与验证流程。</sub></td>
    <td width="33%" valign="top"><strong>Verifier + Memory + Checkpoint</strong><br><sub>独立验收、typed learning、策略生命周期与 durable state。</sub></td>
  </tr>
</table>

对应实现：[`harness.py`](lingjing_harness/runtime/harness.py) · [`deliberation.py`](lingjing_harness/runtime/deliberation.py) · [`capabilities.py`](lingjing_harness/algorithms/capabilities.py) · [`evolution_core.py`](lingjing_harness/algorithms/evolution_core.py) · [`tools_core.py`](lingjing_harness/runtime/tools_core.py) · [`verifier.py`](lingjing_harness/runtime/verifier.py) · [`memory.py`](lingjing_harness/runtime/memory.py)

`evolution.py`、`recommend.py`、`runtime/tools.py` 保持稳定 import surface；复杂实现集中在对应 `*_core.py`，避免兼容 API 和演化逻辑继续耦合成单一巨型文件。

规范性行为契约：[`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md)  
垂直进化设计：[`docs/VERTICAL_EVOLUTION.md`](docs/VERTICAL_EVOLUTION.md)

### 1 · Goal / Authority → Mission Graph

用户输入先被解析为任务目标、领域、实体与权限：

```text
mode · goal · query / user
explore · allow_adaptation · allow_network
constraints
```

运行时在第一次工具调用之前建立 Mission Graph。它不是预计算执行列表，而是“这次任务还需要证明什么”的状态图：

```text
MissionGraph
├─ workspace_facts
├─ search_reproduction
│  └─ search_diagnosis          dormant until evidence asks for it
├─ search_global_quality
├─ search_candidate_validation
└─ hypotheses
   ├─ search_local_mismatch
   └─ search_systemic_gap
```

每个 requirement 带：

```text
key · domain · tool · priority
prerequisites · status · satisfied_by · reason
```

**Mission Graph 定义还缺什么；DeliberationEngine 决定现在用哪个动作补这个缺口。**

附件、图片感知与网页只能改变 observation 和 hypothesis，不能扩大联网或策略激活权限。

### 2 · Evidence Requirements：追缺口，不追固定流程

一次搜索任务可能先执行 `workspace_facts → search_reproduction`。如果结果正常，diagnosis 可以一直 dormant；如果真实结果暴露空召回或弱匹配，它才动态进入 open。

```text
observation A → requirement satisfied
observation B → hypothesis strengthened → open diagnosis
observation C → local/global conflict → contradiction → investigate
```

Recommendation-only 任务不会因为 `explore=true` 顺手做无关 search audit。Scope 是任务级状态，不是工具列表副作用。

### 3 · Hypothesis tracking：解释也必须有状态

Harness 不只保存 observations，还保存可被支持、削弱和淘汰的 hypothesis：

```text
key · domain · confidence · status
supporting_evidence · contradicting_evidence
```

例如：

- `search_local_mismatch`
- `search_systemic_gap`
- `recommend_cold_start`
- `recommend_systemic_gap`

Observation 改变 hypothesis；活跃 hypothesis 又反过来影响 action utility。

### 4 · Multi-signal deliberation

候选动作只来自：

```text
open requirement
+ prerequisites satisfied
+ capability available
+ authority allowed
```

当前 deliberation 综合：

`priority` · `information_gain` · `evidence_gap` · `hypothesis_pressure` · `contradiction_pressure` · `cost_pressure` · `risk_pressure` · `domain_novelty` · `stagnation_pressure` · `learned_policy_bonus`

核心结构：

```text
utility(action)
  = priority + information gain + evidence gap
  + hypothesis / contradiction pressure
  + bounded learned prior
  - cost / risk / failure / stagnation pressure
```

每次 decision 都保留 target requirement、utility decomposition、active hypotheses、rationale 与 top alternatives，因此“为什么选择这个工具”可以被复核。

### 5 · Execute → Reflect → Replan

```text
compile goal + authority
        ↓
build Mission Graph
        ↓
recall bounded memory
        ↓
┌──────────────────────────────────────────────┐
│ deliberate over valid candidates             │
│        ↓                                     │
│ execute one risk-guarded real capability     │
│        ↓                                     │
│ consume observation / evidence               │
│        ↓                                     │
│ REFLECT                                      │
│  · satisfy / block requirements              │
│  · activate dormant requirements             │
│  · update hypotheses                         │
│  · detect / resolve contradictions           │
│  · update stagnation                         │
│        ↓                                     │
│ checkpoint complete deliberation state       │
│        ↓                                     │
│ trajectory critic → replan or close          │
└──────────────────────────────────────────────┘
        ↓
independent final verifier
        ↓
bounded learning / recovery-safe result
```

工具返回不是流程终点，而是新的世界状态。每个 completed / failed action 后先 reflection，再允许下一次 decision。

### 6 · Contradiction & stagnation

复杂任务常常不是“证据太少”，而是证据互相冲突。

例如单 query 看起来正常，但全局 audit 显著偏低，Harness 会记录 material contradiction 并要求诊断。连续动作如果没有改变 requirement、hypothesis 或 contradiction，`stagnation` 会升高，重复路径价值随之降低。

### 7 · Trajectory Critic + Independent Verifier

`TrajectoryCritic` 持续计算：

```text
evidence_coverage · terminal_coverage
unresolved critical/high requirements
blocked requirements
unresolved contradictions
stagnation · confidence
```

只要还有可执行的 critical / high requirement，或 material contradiction 没调查，critic 就不会 clean close。

轨迹闭合后 `ResultVerifier` 再独立检查：

```text
no_failed_tools
evidence_backed
adaptation_respected
mission_terminal
contradictions_resolved
```

“控制器认为做完了”和“系统允许返回完整结果”是两个不同判断。

### 8 · Tool contract + typed memory

每个工具声明：

```text
risk · cost · side_effect · repeatable · input_schema
```

| Risk | Harness 允许什么 |
|---|---|
| `read` | 读取、复现、诊断 |
| `simulation` | 离线 audit / evaluation |
| `adaptive` | 探索并写可信策略记忆；激活仍需授权 |
| `network` | 只有当前 run 明确允许联网时才执行 |

长期记忆分为：

| Memory | 保存什么 | 对未来的作用 |
|---|---|---|
| Episodic | 任务、动作、findings、reward | 启动时召回类似轨迹 |
| Procedural | 通过验证的 Search / RecSys strategy genome | trusted / active / retired 生命周期 |
| Policy | 上下文-动作历史 reward | 有界 prior，不覆盖当前证据 |

**Durable memory 不是可信输入。** 旧版本、损坏或已经失效的 strategy config 在进入引擎之前必须重新通过当前 schema：非有限数值、越界 gene 会被拒绝；已删除 capability 会 canonicalize 到 owned default，而 active fingerprint 如果因此不再描述真实执行策略，会被退休并要求重新评估。

### 9 · Recoverable deliberation

Checkpoint 持久化：

```text
actions · observations · findings · evidence · decisions
mission · hypotheses · reflections · contradictions
critic · stagnation · spent cost · events
```

恢复时 rehydrate 整个 Mission Graph 和 deliberation state，从已完成动作之后继续。Adaptive invocation 使用稳定 ID 保持幂等。

---

## 垂直自进化：从参数到能力

这一部分是 Recsys Harness 与普通“自动调参 Agent”最重要的区别。

### 1 · Mixed Strategy Genome

策略不是一组固定 float，而是：

```text
Strategy Genome
├─ continuous genes
│  ├─ normalized warm-ranking blend
│  │  ├─ Search: lexical / semantic / title / quality / popularity / freshness
│  │  └─ Rec: profile / graph / category / quality / freshness / popularity / novelty / exploration
│  └─ independent bounded genes
│     ├─ slate diversity
│     └─ recommendation cold-start pressure
└─ capability genes
   ├─ Search: query / candidate / rerank
   └─ Rec: profile / candidate / cold-start / exploration / rerank
```

这里有一个重要约束：**只对同一语义作用域的权重做归一化。** `cold_start` 对 warm user 不生效，因此它是独立 gene，不能因为调冷启动而偷偷重分配 profile / graph / freshness 等 warm-ranking 权重。

连续 gene 由 dataclass metadata 声明边界和归一化组；capability gene 只声明 `capability_group`，具体合法实现从 `CapabilityRegistry` 自动发现。

> Registry 定义 **what exists**；domain evaluator 决定 **what wins**。

### 2 · 当前真实可进化结构

<table>
  <tr>
    <td width="50%" valign="top">
      <strong>Search genome</strong><br><br>
      <code>query_strategy</code><br>
      <sub>rare_focus · literal · catalog_expand</sub><br><br>
      <code>candidate_strategy</code><br>
      <sub>postings_union · semantic_rescue</sub><br><br>
      <code>rerank_strategy</code><br>
      <sub>category_mmr · semantic_mmr · hybrid_mmr</sub>
    </td>
    <td width="50%" valign="top">
      <strong>Recommendation genome</strong><br><br>
      <code>profile_strategy</code><br>
      <sub>recency_balanced · recent_intent · long_horizon</sub><br><br>
      <code>candidate_strategy</code><br>
      <sub>full_pool · evidence_union</sub><br><br>
      <code>cold_start_strategy</code><br>
      <sub>quality_freshness · discovery_prior · fresh_explore</sub><br><br>
      <code>exploration_strategy</code><br>
      <sub>stable_fresh · novelty_seek · coverage_seek</sub><br><br>
      <code>rerank_strategy</code><br>
      <sub>category_mmr · semantic_mmr · hybrid_mmr</sub>
    </td>
  </tr>
</table>

这些不是 README 标签。每个选择都真实进入 `SearchEngine` / `RecommendationEngine` 执行路径。

例如 `semantic_rescue` 只能在已有 lexical / catalog anchor 的前提下补充候选；未知 query 不会因为向量哈希碰撞被凭空召回。结构能力返回的 candidate IDs 还会在进入推荐打分前去重，避免 capability 实现细节制造重复 slate 候选。

### 3 · Mixed response surface + constrained projection

每次 evolution 先在 discovery split 上测局部响应面。

连续 gene：

```text
field ↑
field ↓
```

Capability gene：

```text
field = registered alternative A
field = registered alternative B
...
```

每一个 structural neighbor 都重新执行完整 pipeline，而不是只修改配置字符串。

对于需要保持总质量的 blend group，变异后不是简单“clip 一次再平均修正”。当前实现用**有界 capacity redistribution**把剩余质量分配到仍有容量的 gene，直到满足：

```text
∀ gene: min ≤ value ≤ max
Σ blend genes = original group mass
```

因此极端 mutation / clipping 也不能让权重总量悄悄漂移。

Response surface 输出：

```text
arm · kind · objective_delta · robustness
historical_prior · posterior_sample · routing_score
```

### 4 · Posterior routing：历史经验是先验，不是输入真相

Trusted strategy memory 不只记参数，也记 capability selection。

历史经验可以形成这样的 prior：

```text
lexical:up
freshness:down
rerank_strategy=hybrid_mmr
cold_start_strategy=discovery_prior
```

但 durable memory 本身可能来自旧版本。Router 只消费结构有效的 trusted / active rows；坏掉的 numeric value、未知字段或无法投影的旧 config 会被跳过，而不是让新 evolution run 崩溃。

当前 catalog 的 discovery response 权重大于历史 posterior，因此曾经的 winner 不会成为永久规则。

### 5 · Quality-Diversity：不让进化塌成一个局部最优

Archive 按 mutation signature 保留不同机制的最佳策略：

```text
query_strategy=catalog_expand
candidate_strategy=semantic_rescue + rerank_strategy=hybrid_mmr
profile_strategy=recent_intent + exploration_strategy=novelty_seek
lexical:up + semantic:down
```

下一代可以从多个 mechanism parent 继续探索，而不是只围着全局第一名做小幅抖动。Population expansion 还有显式尝试上限，避免低维/重复 genome 因无法产生足够 unique candidate 而无限循环。

### 6 · Evaluation identity：先保证“独立”，再谈 holdout

“discovery / holdout 两个列表”并不自动意味着独立。如果同一个 query 被重复导入两次，它仍可能以两条 row 的形式泄漏到两边。

因此当前数据路径先做两层防护：

```text
Catalog ingest
  → same query labels merge relevance sets
  → one canonical label per query
        ↓
Evolution split
  → defensive unique-by-identity
  → deterministic discovery / holdout split
```

推荐 warm-user split 同样按 user identity 去重。**评价单位的 identity 不能同时出现在 discovery 与 holdout。**

### 7 · Cold-start：独立 probe 必须进入独立 gate

以前仅仅“计算一个 cold-start 指标”还不够。如果 final trust gate 不检查 holdout cold-start regression，这个指标仍然只是旁观数据。

当前推荐 evolution 的路径是：

```text
discovery warm users
+ discovery cold-start identities
        ↓
mixed response surface / routing
        ↓
discovery winner

independent holdout warm users
+ disjoint holdout cold-start identities
        ↓
quality / coverage / robustness
+ explicit cold_start_quality_delta gate
        ↓
trusted strategy memory
```

Synthetic cold identities 会显式避开真实 user IDs；即使真实数据里恰好存在内部 probe 前缀，也会生成新的无历史 identity，防止“冷启动 probe 意外继承真实行为”。

Trust 现在允许**真正的 cold-start-only improvement**获得 credit，但前提是 independent cold holdout 同样没有退化；反过来，即使 warm quality / coverage 明显提升，只要 holdout cold-start 显著回归，也不能通过 safety gate。

### 8 · Active strategy ≠ 永久可信

一个曾经 trusted / active 的策略，在代码 schema 或 capability registry 变化后不能直接继承旧可信度。

启动时：

```text
persisted active config
        ↓
current schema validation
  · numeric finite?
  · within declared bounds?
  · capability still registered?
        ↓
effective config == stored config ?
  ├─ no  → retire old active fingerprint → owned default → re-evaluate later
  └─ yes → run active regression validation
```

Recommendation 的 active regression 不只看 aggregate quality / coverage，还看 `cold_start_quality`。这修正了“整体指标没坏，但冷启动已经明显退化仍继续 active”的漏洞。

### 9 · Evolution ≠ self-modifying code

允许进化：

- ranking/blending weights
- query processing strategy
- candidate generation strategy
- user-profile strategy
- cold-start policy
- exploration policy
- slate reranking strategy
- posterior exploration prior
- bounded QD archive / trusted procedural memory

不允许 optimizer 隐式进化：

- 用户权限
- network authority
- evaluation identity / holdout membership
- verifier / trust gate
- tool risk class
- checkpoint / lease / fencing
- 任意 Python 源码

所以它是**垂直 Search/RecSys self-evolution**，不是一个不受约束的通用代码 Agent。

完整设计：[`docs/VERTICAL_EVOLUTION.md`](docs/VERTICAL_EVOLUTION.md)

---

## 快速启动

**要求：Python 3.11+。核心搜推、控制、验证与 memory 默认本地运行；视觉感知和联网研究是可选能力。**

```bash
git clone https://github.com/jiaweine/recsys-harness.git
cd recsys-harness
python -m venv .venv
```

<details open>
<summary><strong>macOS / Linux</strong></summary>

```bash
source .venv/bin/activate
python -m pip install -e .
python -m uvicorn lingjing_harness.api:app --host 127.0.0.1 --port 8765
```

</details>

<details>
<summary><strong>Windows PowerShell</strong></summary>

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m uvicorn lingjing_harness.api:app --host 127.0.0.1 --port 8765
```

</details>

打开：

```text
http://127.0.0.1:8765
```

CLI：

```bash
lingjing-harness "做一次全局体检"
```

开发与验证：

```bash
python -m pip install -e ".[dev]"
pytest -q
python scripts/probe_harness_contract.py
```

---

## 能力

<table>
  <tr>
    <td width="33%" valign="top"><strong>01 · Real Search</strong><br><sub>真实 query processing、candidate retrieval、rerank、诊断与 audit。</sub></td>
    <td width="33%" valign="top"><strong>02 · Real Recommendation</strong><br><sub>用户 profile、候选池、cold-start、exploration、slate rerank。</sub></td>
    <td width="33%" valign="top"><strong>03 · Capability Evolution</strong><br><sub>参数 + 结构 mixed genome，真实 pipeline response surface。</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top"><strong>04 · Independent Evaluation</strong><br><sub>identity-isolated discovery / holdout、regression、robustness、cold-start gate。</sub></td>
    <td width="33%" valign="top"><strong>05 · Controlled Evidence</strong><br><sub>多模态与网络只能提供 observation / provenance，不扩大 authority。</sub></td>
    <td width="33%" valign="top"><strong>06 · Durable Learning</strong><br><sub>Episodic / procedural / policy memory + canonicalization / retirement + recovery。</sub></td>
  </tr>
</table>

<details>
<summary><strong>搜索 / 推荐实际覆盖</strong></summary>

| Search | Recommendation |
|---|---|
| query evidence / catalog expansion | recency-balanced / recent / long-horizon profile |
| lexical postings / bounded semantic rescue | full-pool / evidence-union candidates |
| field-aware lexical + semantic scoring | graph / content / category / quality / freshness |
| category / semantic / hybrid MMR | cold-start + stable / novelty / coverage exploration |
| Recall / MRR / NDCG | Coverage / Diversity / Freshness / Novelty + collision-safe cold-start slice |

</details>

<details>
<summary><strong>可选联网与视觉感知</strong></summary>

```bash
export LINGJING_WEB_SEARCH_URL=<your-search-endpoint>
export LINGJING_WEB_SEARCH_KEY=<optional-key>
export LINGJING_VISION_BASE_URL=<your-vision-endpoint>
export LINGJING_VISION_MODEL=<your-model-id>
```

联网结果保留 title / URL / snippet；图片感知只产生受限 observation。两者都不能扩大用户授权。

</details>

---

## 可靠性

<table>
  <tr>
    <td width="25%" valign="top"><strong>Guardrails</strong><br><sub>risk / cost / side effect / schema / authority 显式化。</sub></td>
    <td width="25%" valign="top"><strong>Verification</strong><br><sub>critic、verifier、identity-isolated holdout、cold gate 与 robustness 分层。</sub></td>
    <td width="25%" valign="top"><strong>Durability</strong><br><sub>动作、mission、deliberation 与 effective strategy state 可恢复。</sub></td>
    <td width="25%" valign="top"><strong>Fencing</strong><br><sub>lease + heartbeat + revision 阻止 stale worker 覆盖新状态。</sub></td>
  </tr>
</table>

<details open>
<summary><strong>Durable execution</strong></summary>

同一个 conversation 同时只允许一个 active run；不同 conversation 可以并行。Adaptive invocation 幂等；`cancel_requested` 可跨进程安全收敛。

SQLite 同时承担 conversation reservation、worker owner、lease、heartbeat 与 terminal-state fencing。迟到 worker 不能覆盖新 owner 已提交的状态。

</details>

<details>
<summary><strong>Strategy lifecycle hardening</strong></summary>

Active strategy 不是绕过验证的永久缓存。加载时先验证当前 schema / bounds / capability registry；effective config 与旧 fingerprint 不一致就退休。定期 revalidation 会重新比较 owned default，Recommendation 还额外检查 cold-start regression。

</details>

<details>
<summary><strong>Workspace coherence</strong></summary>

Catalog 有共享 revision 和 update lock。数据导入期间不接受新的 run；revision 提交后，其他 worker 会重新加载一致的 Catalog / Harness。

</details>

---

## 系统架构

<p align="center"><sub>AGENT HARNESS RUNTIME · CONTROL · EVIDENCE · EVOLUTION · TRUST · STATE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@bbe47c417b279d6eb65d5758f1f011d6254725e1/docs/readme-assets/architecture.svg" alt="Recsys Harness system architecture" width="97%">
</p>

当前系统可以看成两个受同一 trust plane 约束的闭环。

**Task loop**

```text
Goal + Authority
      ↓
MissionGraph ─────────────────┐
      ↓                       │
DeliberationEngine            │
      ↓                       │
ToolRegistry → Observation    │
      ↓                       │
Reflection → Hypotheses       │
      │       Requirements    │
      │       Contradictions  │
      ↓                       │
TrajectoryCritic ── replan ───┘
      ↓ close
ResultVerifier
```

**Vertical evolution loop**

```text
Current Search / RecSys Strategy
      ↓
Schema + Capability Canonicalization
      ↓
Mixed Genome
  warm blend + independent genes + capability genes
      ↓
Identity-Isolated Discovery Response Surface
      ↓
Posterior-Guided Mixed Routing
      ↓
Population + QD Archive
      ↓
Independent Warm + Cold Holdout
      ↓
Regression / Robustness / Cold-start Gates
      ↓
Trusted Strategy Memory
      ↓
Optional Permissioned Activation
      ↓
Periodic Active Revalidation / Retirement
```

<table>
  <tr>
    <td width="33%" valign="top"><strong>Control plane</strong><br><sub>Mission Graph、Deliberation、Reflection、Trajectory Critic 持有任务级决策权。</sub></td>
    <td width="33%" valign="top"><strong>Evolution plane</strong><br><sub>CapabilityRegistry、mixed genome、bounded projection、response surface、posterior router 与 QD archive 负责领域策略搜索。</sub></td>
    <td width="33%" valign="top"><strong>Trust & state plane</strong><br><sub>identity isolation、Verifier、warm/cold holdout、typed memory、strategy retirement、checkpoint、lease 与 revision 约束长期行为。</sub></td>
  </tr>
</table>

**Architecture invariants**

`attachments never grant permission` · `network evidence never promotes strategy` · `one evaluation identity cannot cross discovery / holdout` · `cold-start credit requires cold-start evidence` · `stored strategy must equal effective strategy` · `holdout precedes trust` · `critic gates clean closure` · `stale workers cannot overwrite current state`

> 架构说明：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Harness contract：[`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md) · Self-evolution：[`docs/VERTICAL_EVOLUTION.md`](docs/VERTICAL_EVOLUTION.md)

---

## 部署

默认本地模式不要求登录。生产暴露时必须显式配置访问密钥：

```bash
export LINGJING_ENV=production
export LINGJING_ACCESS_TOKEN='<a-long-random-secret>'
python -m uvicorn lingjing_harness.api:app --host 0.0.0.0 --port 8765
```

生产边界包括签名 HttpOnly + SameSite 会话、共享 SQLite 限流、安全响应头与可选 trusted-proxy IP 处理。

> 当前多 worker 设计假设 worker 共享同一 SQLite 与数据目录；真正多机 / 多区域部署应把协调存储与对象存储迁移到共享基础设施。

---

## 数据

内置 sample catalog 可以直接体验。导入自己的数据时支持：

`items` · `interactions` · `query labels` · `eligibility` · `quality` · `popularity` · `freshness`

重复 query label 会在 Catalog 边界合并 relevance set；query 本身是 Search evaluation 的 identity，不会以重复 row 的方式跨 discovery / holdout。

完整格式见 [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)。

---

## 质量门槛

```bash
make check
make test
make demo
python scripts/probe_harness_contract.py
```

CI 覆盖：Python 编译与完整回归、Harness contract probe、mixed-genome / capability-stage 测试、CLI smoke、wheel 干净安装、产品 hygiene、真实浏览器桌面 / 移动流程、多 worker lease / fencing / workspace revision，以及生产访问与限流契约。

这次 hardening 不是只补 happy-path，而是增加 failure-oriented regression：

- duplicate query identity 不得跨 discovery / holdout；
- 极端 clipping 后 blend 仍严格满足 bounds + exact group mass；
- `cold_start` mutation 不得改变 warm ranking blend；
- 损坏 / 旧版 trusted memory 不得让 evolution 崩溃；
- synthetic cold identity 与真实用户撞名时必须避让；
- cold-start-only improvement 在独立 holdout 支持时可以获得 trust；
- warm 指标再好也不能掩盖 holdout cold-start regression；
- public recommendation audit 必须实际报告 cold-start slice；
- 已删除 capability / 非法 active config 在执行前退休；
- active recommendation 的 cold-start 回归必须触发 rollback；
- 非默认 query / candidate / profile / cold-start / exploration / rerank 仍真实执行；
- Harness contract、CLI、wheel、resilience 与既有产品测试必须同时通过。

---

## Repository map

```text
frontend/                          产品 UI（本轮 hardening 不修改）
lingjing_harness/
  algorithms/
    capabilities.py                typed registry + config canonicalization
    search.py                      search mixed strategy genome + real stages
    recommend.py                   stable public compatibility surface
    recommend_core.py              hardened recommendation stages / cold-start isolation
    evolution.py                   stable public compatibility surface
    evolution_core.py              response surface / posterior / QD / isolated gates
    evaluation.py                  Search/Rec metrics + collision-safe cold probes
  runtime/
    harness.py                     Agent Harness orchestration / checkpoint loop
    policy.py                      用户目标、scope 与 authority 编译
    deliberation.py                Mission Graph / hypotheses / reflection / critic
    contracts.py                   runtime state / requirement / decision contracts
    tools.py                       stable ToolRegistry import surface
    tools_core.py                  strategy validation / activation / rollback lifecycle
    verifier.py                    独立结果、权限与 trajectory 验证
    memory.py                      episodic / procedural / policy memory
    perception.py                  多模态 observation
    network.py                     可控网络 evidence
  api.py                           API、认证、附件、恢复、工作区运行时
  store.py                         durable run、lease、revision、共享限流
tests/test_deliberation.py         mission / reflection / critic 回归
tests/test_vertical_evolution.py   mixed-genome / holdout / posterior 回归
tests/test_capability_genome.py    real capability-stage 回归
tests/test_evolution_hardening.py  identity / memory / projection / cold-gate 对抗回归
docs/HARNESS_CONTRACT.md           规范性 Harness contract
docs/VERTICAL_EVOLUTION.md         垂直自进化设计与扩展契约
scripts/probe_harness_contract.py  可执行 contract probe
scripts/capture_readme_assets.py   真实浏览器 QA 与 README 截图
```

> 仓库只维护**一条主线实现**。Search / Recommendation 是垂直能力；Agent Harness 是控制、实验、验证、进化、学习与恢复这些能力的产品主体。

---

<div align="center">

### Search and recommendation are the domain. The self-evolving Harness is the product.

**Compile · Deliberate · Execute · Reflect · Evolve · Isolate · Holdout · Verify · Learn · Recover**

<sub>Mission graphs · Mixed genomes · Real capabilities · Failure-oriented gates · Typed memory · Durable state</sub>

</div>