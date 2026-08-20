# Architecture

## Product definition

**序枢（Xushu）是 Search / Recommendation 的控制、实验与进化平面。**

它不是另一个推荐模型库，也不把一个更高的离线综合分直接等价成业务价值。产品负责把搜推改动的证据链放进同一个受约束运行时：

```text
business goal / authority
        ↓
intent + scope
        ↓
Capability Registry
        ↓
Mission Compiler
        ↓
evidence-driven Deliberation
        ↓
Search / Recommendation execution
        ↓
production replay + domain guardrails
        ↓
future temporal holdout
        ↓
trust / activation
        ↓
segment policy routing
        ↓
active revalidation / rollback
        ↓
positive + negative durable learning
```

核心原则：

> **Business reward decides value; domain metrics constrain safety; authority constrains action.**

没有 production reward evidence 时，系统仍可以做本地诊断、relevance audit、coverage / freshness / cold-start 检查和候选探索，但这些结果明确属于 `proxy_metrics`，不会冒充真实业务收益。

---

## 1. Runtime control plane

### 1.1 Intent and authority

`runtime/policy.py` 把用户自己的文本解析成：

```text
mode                 search | recommend | both | audit
query / user_id
explore
allow_adaptation
allow_network
constraints
```

这里负责的是 **intent / authority**，不是任务流程。

权限只能来自当前用户目标：

- 附件和视觉信息只能增加 observation；
- history / memory 只能影响有限的决策 prior；
- 网页内容只能提供外部证据；
- 以上内容都不能扩大 adaptation 或 network authority。

因此“用户想做什么”和“为了完成它需要哪些能力”是两层不同的逻辑。

### 1.2 Declarative Capability Registry

`runtime/capabilities.py` 是 runtime capability planning metadata 的 source of truth。

一个 `CapabilityContract` 至少声明：

```text
name
requirement_key
label / domain
provides
requires
diagnoses
priority
information_gain
risk / cost / side_effect
activation scope
plan argument bindings
completion evidence
evidence gates
reflection profile
```

例如，`search.evolve` 不再由 Deliberation 写死“必须先 search.audit”。它声明：

```text
requires: search_global_quality
completion evidence: evaluation_ready
gate: search.audit.queries >= 3
```

`recommend.evolve` 同理声明 recommendation evidence floor。

这意味着：

> **新增 capability 要修改它自己的注册与执行实现，而不需要向 `DeliberationEngine.initialize()` 增加新的任务流程分支。**

多个 capability 可以声明为同一个 `requirement_key` 的兼容实现。Registry 会验证它们拥有一致的 requirement semantics；Mission Graph 保存全部可选实现，Deliberation 再根据当前信息增益、成本、风险、历史 policy bonus 和失败状态进行选择。

### 1.3 Mission Compiler

`runtime/mission_compiler.py` 从 `AgentPlan + CapabilityRegistry` 编译 Mission Graph。

编译过程只做通用工作：

1. 根据 plan scope 激活 capability；
2. 按 `requirement_key` 聚合兼容实现；
3. 根据 `requires` 建 evidence dependency DAG；
4. 创建 capability-declared hypotheses；
5. 对缺失 provider 的 dependency fail closed；
6. 保存本次编译使用的 `capability_snapshot`。

示例：

```text
workspace_facts
      ↓
search_reproduction
      ↓
search_diagnosis      (dormant until observation warrants it)

workspace_facts
      ↓
search_global_quality
      ↓
search_candidate_validation
```

推荐任务使用同一套编译机制，不存在第二个手写 mission pipeline。

Mission 的关键 requirement key 保持稳定，既服务可审计 UI，也保证旧 checkpoint 能恢复。旧 checkpoint 没有 `capability_snapshot` / requirement `capabilities` 时，会回退到历史 `tool` 字段，不要求数据库迁移。

### 1.4 Evidence-driven Deliberation

`runtime/deliberation.py` 不再维护 `_INFORMATION_GAIN = {tool_name: ...}` 这种中央工具表。

候选 action utility 使用：

```text
priority
+ capability.information_gain
+ evidence_gap
+ hypothesis_pressure
+ contradiction_pressure
+ domain_novelty
+ bounded learned policy bonus
- ToolSpec cost pressure
- ToolSpec risk pressure
- previous failure pressure
- stagnation pressure
```

其中：

- `information_gain` 来自 CapabilityContract；
- `cost / risk` 同时受到执行层 ToolSpec 约束；
- Registry 可以检查 capability metadata 与 ToolSpec 是否 drift；
- plan 参数由 contract 的 argument binding 生成；
- evidence floor 由 capability gate 判断；
- 没有可配置 capability 时 requirement 会显式进入 `blocked`，不会悄悄从 mission 消失。

### 1.5 Reflection remains vertical on purpose

任务编译和 action routing 已 capability-driven，但 observation 的含义仍然是 vertical-domain logic。

例如：

- Search reproduction 结果为空意味着什么；
- Search local result 与 global audit 冲突意味着什么；
- Recommendation 是否处于 cold-start；
- coverage 下降是否值得激活 diagnosis。

这些解释通过 contract 的 `reflection_profile` 选择。这样 Mission Compiler 不需要知道具体工具名，同时也不会把真正的 Search / RecSys 领域语义抽象成没有意义的通用字符串。

### 1.6 Trajectory critic + independent verifier

Trajectory critic 检查：

- critical / high evidence 是否 terminal；
- material contradiction 是否得到调查；
- blocked evidence 是否显式可见；
- stagnation 是否持续；
- 是否满足 clean-close 条件。

最终 `ResultVerifier` 独立检查：

- tool failures；
- mission terminality；
- evidence support；
- adaptation / network authority；
- unresolved contradictions。

Action selector 不能自行宣布自己通过验证。

---

## 2. Search / Recommendation execution plane

### Search

内建 Search engine 提供 reference execution environment：

- 中英文 tokenize；
- field-aware BM25-style lexical evidence；
- bounded semantic signal；
- title / quality / popularity / freshness；
- query strategy；
- candidate strategy；
- diversity-aware rerank。

### Recommendation

内建 Recommendation engine 提供：

- implicit feedback profile；
- recency decay；
- co-occurrence graph；
- category / semantic profile fit；
- quality / freshness / popularity / novelty / exploration；
- cold-start policy；
- seen filtering；
- diversity-aware slate rerank。

这些算法是 owned reference engines，不是产品边界。序枢的长期边界是控制和验证真实搜推系统，而不是和专业 model / serving framework 比模型数量。

---

## 3. Mixed strategy genome

`algorithms/capabilities.py` 与 Search / Recommend config metadata 定义 typed strategy genome：

```text
Strategy Genome
├─ continuous genes
│  ├─ normalized ranking blends
│  └─ independent bounded signals
└─ capability genes
   ├─ query / candidate / rerank
   ├─ profile / cold-start
   └─ exploration
```

算法层的 CapabilityRegistry 与 runtime capability registry 解决不同问题：

- **algorithm capability registry**：一个 ranking/recommendation genome 有哪些可替换实现；
- **runtime capability registry**：Agent 为完成任务可以调用哪些可审计能力。

两者都只回答 **what exists**，不声明哪个实现应该获胜。

具体 credit 来自当前数据、response surface、独立 validation、历史正/负 credit 与后续 revalidation。

---

## 4. Production value contract

### 4.1 Interactions and exposure logs are different facts

`interactions` 用于用户 profile / collaborative evidence。

生产评估使用 `ExposureEvent`：

```text
request_id
timestamp
surface                  search | recommend
user_id / query
item_id
event                    impression / click / cart / purchase / hide / ...
value
propensity
position
policy_id
model_version
experiment_id
metadata
```

原因：

> “用户发生过行为”与“系统在什么策略下把什么展示给用户”不是同一类事实。

如果混在一起，就无法可靠回答：

- 用户没点击是因为不喜欢，还是没有看到；
- outcome 来自哪个 ranking policy；
- candidate policy 与 logging policy 如何比较；
- request identity 是否泄漏到了 discovery / holdout 两侧。

### 4.2 RewardSpec

业务价值由接入方显式声明。例如：

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

序枢不内置“GMV 一定比 CTR 重要”这种产品假设。

业务 reward 是 value objective；relevance / coverage / freshness / diversity / cold-start quality 是 safety / behavior guardrails。

---

## 5. Production replay and temporal isolation

`production.py` 对 Search / Recommendation 使用 request-level replay contract。

每个 `request_id`：

1. 使用历史 request context 调用 candidate policy；
2. 获取 candidate ranking；
3. 对历史 exposure / outcome 找 candidate rank；
4. 使用 rank discount；
5. propensity 可用时使用 capped inverse propensity weighting；
6. request 内归一化；
7. 最终按 request 求值。

输出包括：

```text
reward
reward_coverage
requests
estimator
request_scores
propensity_rows
```

### Statistical boundary

当前 estimator 是：

- `logged_replay`
- `propensity_weighted_logged_replay`

它**不声称自己是完整、无偏的 IPS / SNIPS / Doubly Robust OPE**。

这是一个明确产品边界，不把 inverse propensity weighting 包装成线上因果结论。

### Temporal split

production path 按 request identity 的时间顺序切：

```text
past discovery
      ↓ time
future holdout
```

约束：

- 同一个 request 永远不跨 split；
- holdout 晚于 discovery；
- Search / Recommendation 分 surface 切分；
- sparse replay 可以用于探索，但不能直接获得 durable trust。

Public trust gate 还要求：

- 足够 paired future requests；
- independent domain guardrail；
- future business reward 不出现 material regression。

---

## 6. Segment-conditioned strategy portfolio

一个全局 Search / Recommend config 不足以覆盖所有 pathology。

`algorithms/segments.py` 使用 production traffic 的可观测特征和分位数构建 segment：

Search 示例：

```text
no-anchor
candidate-scarce
weak-anchor
strong-anchor
mixed
```

Recommendation 示例：

```text
cold-start
candidate-scarce
sparse-history
established
mixed
```

每个 segment strategy 都必须独立经历：

```text
discovery
  ↓
future holdout
  ↓
business reward
  ↓
domain guardrail
  ↓
trusted
  ↓
optional active routing
```

证据不足、策略失效或 segment 不再稳定时，runtime 回退到 global strategy。

全局策略刚改变时，旧 global coordinate system 下验证的 segment strategy 不会立即作为 active override；必须重新验证。

---

## 7. Durable positive and negative credit

Trusted/active strategy memory 记录被独立验证过的正向经验。

独立的 strategy-credit ledger 记录：

```text
accepted mutation
rejected mutation
holdout regression
guardrail rejection
active rollback
segment/pathology context
```

关键性质：

- event write idempotent，重跑同一 evidence 不会无限刷 credit；
- inconclusive evidence 不会被误记成失败；
- global 与 segment failure credit 隔离；
- repeated net-negative mutation 会降低未来 retry 优先级；
- 新成功证据可以恢复 posterior，不使用永久 blacklist；
- 历史 credit 质量有界，陈旧失败不能永久压过新证据。

因此 evolution 已经从“记住什么成功过”升级为“同时学习什么在什么 pathology 下失败过”。

---

## 8. Active lifecycle and rollback

策略进入 active 不是终点。

Runtime 周期性复核：

```text
stored strategy schema
business reward
proxy/domain guardrails
cold-start quality
segment evidence floor
```

以下情况会 retire / fallback：

- capability 已从当前 schema 移除；
- canonical effective config 与 stored fingerprint 不一致；
- production reward material regression；
- Search relevance / recall regression；
- Recommend quality / coverage / cold-start regression；
- segment production evidence 不再充足。

Rollback 本身会进入 durable negative credit，因此失败不会因“删除 active config”而被遗忘。

---

## 9. External serving adapters

`adapters.py` 提供 read-only serving adapter contract，使已有：

- Elasticsearch / OpenSearch；
- Vespa；
- internal Search API；
- internal Recommendation API；
- proprietary ranking service；

可以返回标准 ranking result，复用 production reward replay 和 evaluation。

当前 adapter 边界是保守的：

> **序枢可以评估外部 serving policy，但不会在没有显式 adapter contract 的情况下任意修改远端模型、索引或参数。**

自动发布 / canary / external rollback 仍需要部署侧 adapter，是后续 production control plane 的扩展面。

---

## 10. Durability and identity

系统区分两类 identity：

### Strategy context identity

由稳定 catalog / behavior contract / RewardSpec 等决定。

新增 outcome 不应把长期 strategy learning 整体清空，但业务 reward contract 改变必须让旧 credit 失效。

### Workspace evidence revision

包含当前 production evidence snapshot。

新增日志会推进 workspace revision，使 worker 重新加载并复核当前证据，而不是误用旧 snapshot。

Checkpoint 还保存：

- Mission Graph；
- capability snapshot；
- hypotheses；
- contradictions；
- reflections；
- critic state；
- actions / observations / decisions；
- spent budget。

Resume 从已完成 action 之后继续；adaptive writes 必须保持 idempotent。

---

## 11. Security and authority boundary

Runtime capability 合同描述“系统能做什么”，不描述“当前用户允许做什么”。

最终执行仍由 ToolSpec risk guard 决定：

```text
read
simulation
network
adaptive
```

- network capability 需要 network authority；
- active strategy change 需要 adaptation authority；
- attachment / memory / external evidence 不得扩大权限；
- 未注册 capability 不得执行；
- invalid stored strategy fail closed。

Capability-driven 不等于 permission-driven-by-capability；用户 authority 始终优先。

---

## 12. Known boundaries

当前系统明确**没有**把下列能力包装成已经完成：

1. `OwnedPolicy.plan()` 仍是 Search / Recommendation 垂直 intent parser；它负责 scope / authority，而不是通用自然语言规划器。
2. Reflection profiles 仍包含 Search / RecSys 领域解释逻辑，这是刻意保留的 vertical semantics。
3. Logged replay 不是完整无偏的 IPS / SNIPS / DR counterfactual evaluator。
4. External serving adapter 目前以安全的 read-only evaluation 为主；自动发布需要显式 deployment adapter。
5. 内建 Search / Recommend engine 是 reference execution environment，不代表企业必须迁移 serving stack。
6. `lingjing_harness` 和 `LINGJING_*` 仍是历史兼容 namespace / runtime interface，不是产品品牌。

这些边界比“所有东西都 autonomous”更重要，因为序枢的产品价值建立在 **可验证、可回滚、可审计**，而不是自动化程度的宣传上。
