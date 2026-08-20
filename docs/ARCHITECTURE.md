# Architecture

## Product definition

**序枢（Xushu）是 Search / Recommendation 的控制与进化平面。**

它不是另一个模型库，也不把「一个更高的离线综合分」等价成业务价值。系统负责把以下链路放在同一个受约束运行时里：

```text
business goal / authority
        ↓
observe production evidence
        ↓
diagnose search / recommendation
        ↓
generate typed strategy candidates
        ↓
replay + domain guardrails
        ↓
future temporal holdout
        ↓
trust / activation
        ↓
active revalidation / rollback
        ↓
durable learning
```

核心原则：

> **Business reward decides value; domain metrics constrain safety; authority constrains action.**

没有 production reward evidence 时，系统仍可做本地诊断、relevance audit、coverage/freshness/cold-start 检查和候选探索，但这些结果会明确标记为 `proxy_metrics`，不会冒充真实业务收益。

---

## 1. Runtime control plane

### 1.1 Goal / authority compiler

`runtime/policy.py` 负责把用户目标解析成：

```text
mode
query / user
explore
allow_adaptation
allow_network
constraints
```

权限只能来自当前用户目标。附件、视觉模型、网页内容和历史 memory 可以提供 observation，但不能扩大 authority。

### 1.2 Mission Graph

`runtime/deliberation.py` 中的 `DeliberationEngine` 建立并维护 Mission Graph：

```text
EvidenceRequirement
├─ key
├─ domain
├─ tool
├─ priority
├─ prerequisites
├─ status
└─ satisfied_by
```

Harness 每获得一个 observation 都会重新 reflection / replan，而不是预先生成一条固定工具链。

当前 controller 仍包含 Search / Recommendation 领域 mission semantics，例如 `search_reproduction`、`recommend_global_quality` 等。这是当前明确保留的 vertical-domain implementation；它还没有完全迁移成 capability-declared mission compiler，因此文档不把这部分描述成“完全无硬编码”。

### 1.3 Trajectory critic + verifier

Trajectory critic 检查：

- critical / high evidence 是否 terminal；
- material contradiction 是否得到调查；
- stagnation 是否持续；
- 是否满足 clean close 条件。

最终 `ResultVerifier` 再独立检查工具失败、证据完整度、适配权限和 mission terminality。

---

## 2. Search / Recommendation execution plane

### Search

当前内建 Search engine 提供：

- 中英文 tokenize；
- field-aware BM25-style lexical evidence；
- bounded semantic signal；
- title / quality / popularity / freshness；
- query strategy；
- candidate strategy；
- diversity-aware rerank。

### Recommendation

当前内建 Recommendation engine 提供：

- implicit feedback profile；
- recency decay；
- co-occurrence graph；
- category / semantic profile fit；
- quality / freshness / popularity / novelty / exploration；
- cold-start policy；
- seen filtering；
- diversity-aware slate rerank。

这些内建算法是 reference execution environment，不是产品边界。序枢的长期定位是控制/评估真实搜推系统，而不是和专业 serving/model framework 比“模型数量”。

---

## 3. Mixed strategy genome

`algorithms/capabilities.py` 与 Search / Recommend config metadata 共同定义 typed genome。

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

`CapabilityRegistry` 只回答 **what exists**。

它不声明：

- 哪个 capability 更好；
- 应该向哪个方向调权重；
- 哪个 capability 必须赢。

具体 credit 来自当前数据、response surface、历史 validated prior 和后续 validation。

---

## 4. Production value contract

### 4.1 Why interactions and exposure logs are separate

`interactions` 仍用于用户 profile / collaborative evidence。

生产评估另外使用 `ExposureEvent`：

```text
request_id
 timestamp
 surface                 search | recommend
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

原因很简单：

> **“用户发生过行为”与“系统在什么策略下把什么展示给用户”不是同一类事实。**

如果把两者混在一起，就无法可靠回答：

- 用户没点击是因为不喜欢，还是根本没看到？
- 这条行为来自哪个 ranking policy？
- 当前候选策略与历史策略如何比较？
- 一个 request 是否泄漏到了 discovery 和 holdout 两侧？

### 4.2 RewardSpec

业务价值由接入方显式提供：

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

实际 reward：

```text
reward(event row) = configured_weight(event) × value
```

序枢不内置“GMV 一定比 CTR 重要”这种产品假设。不同业务可以把 `value` 定义为订单金额、观看时长、完成率贡献或其它明确量纲。

---

## 5. Logged production replay

`production.py` 提供同一套 Search / Recommendation replay contract。

对每个 `request_id`：

1. 使用 request 中的 query 或 user 调用待评估 policy；
2. 获取 candidate ranking；
3. 对有 reward 的历史 exposure/outcome 行寻找 candidate rank；
4. 用 rank discount 计算贡献；
5. 如果存在 propensity，则应用 capped inverse propensity weight；
6. 在 request 内按 absolute reward mass 归一化；
7. 最终按 request 求均值。

输出至少包含：

```text
reward
reward_coverage
requests
estimator
request_scores
propensity_rows
```

### Important statistical boundary

当前 estimator 名称是：

- `logged_replay`
- `propensity_weighted_logged_replay`

**它不声称自己是完整、无偏的 IPS/SNIPS/DR OPE。**

未曝光 item 的真实 outcome 仍然不可观察。Propensity 存在时可以降低一部分 logging-policy bias，但生产级 counterfactual evaluation 仍可以通过 adapter 扩展更严格的 estimator。

这个边界必须保持显式，避免“算了 inverse propensity 就自动等于线上因果收益”的错误产品承诺。

---

## 6. Temporal evaluation

随机/哈希 split 适合 deterministic unit tests，但不应成为 production behavior log 的默认泛化假设。

因此 `ExposureEvent` 的 production path 使用：

```text
all request identities
        ↓ sort by request timestamp
past discovery
        ↓
future holdout
```

约束：

- 同一个 `request_id` 永远不会跨 split；
- future holdout 的时间晚于 discovery；
- Search / Recommendation 分 surface 独立切分；
- sparse production replay 可以用于探索，但不能因为一个 future request 就获得 durable trust。

Public evolution trust gate 额外要求：

- 至少 2 个 paired future request；
- domain guardrail 本身也存在 independent holdout；
- future business reward 没有 material regression。

---

## 7. Confidence

`paired_bootstrap_delta()` 以共同 `request_id` 为配对单位进行 deterministic bootstrap。

```text
candidate(request) - reference(request)
                ↓
paired bootstrap
                ↓
observed delta
95% interval
probability_positive
```

它的作用不是把很小的数据包装成“显著”，恰恰相反：**样本不足会阻止 durable trust。**

---

## 8. Business-routed vertical evolution

### 8.1 No production reward

```text
mixed genome
   ↓
proxy response surface
   ↓
relevance / coverage / freshness / diversity / cold-start
   ↓
independent domain holdout
```

结果明确：

```text
evaluation_basis = proxy_metrics
business_trusted = false
```

这是本地/demo/研究模式。

### 8.2 Production reward available

```text
Mixed Genome
      ↓
Production Discovery Requests
      ↓
Business Reward Replay  ───────┐
                              │ primary routing objective
Domain Guardrails ─────────────┘ bounded safety component
      ↓
Response Surface
      ↓
Posterior-guided mixed routing
      ↓
QD archive / population
      ↓
Future Temporal Holdout
      ↓
paired bootstrap + domain regression gates
      ↓
Trusted Strategy
```

Business reward 是 routing 的主要信号；NDCG/Recall、coverage、freshness、cold-start、worst-slice robustness 仍然负责防止候选为了一个业务 proxy 无限制破坏体验。

因此一个策略可以在业务 reward 更高的情况下接受轻微的 diversity/freshness tradeoff，但不能跨过明确的 regression budget。

---

## 9. Durable strategy lifecycle

当 production reward 存在时：

```text
candidate
  ↓ temporal business holdout
trusted
  ↓ explicit user authority
active
  ↓ periodic revalidation
business reward + proxy/cold-start safety
  ├─ pass → keep active
  └─ fail → retire + owned default
```

`runtime/tools_production.py` 保证：

- durable strategy 的 score 优先使用 business reward；
- memory payload 保留 `evaluation_basis` / `business_validation`；
- active strategy 的 production reward regression 在 proxy refresh 之前检查；
- 一个 proxy-only validation 不能把 business regression 隐藏整个 TTL；
- Harness fork 后仍保持 production-aware registry，而不是退回兼容 core class。

旧版本/损坏 config 的 schema canonicalization、cold-start rollback、permission gate、checkpoint 和 invocation idempotency 继续有效。

---

## 10. Existing-system adapters

`adapters.py` 定义 read-only serving contract：

```python
class SearchServingAdapter:
    def search(query, *, limit): ...

class RecommendServingAdapter:
    def recommend(user_id, *, limit): ...
```

并提供：

- `AdapterSearchEngine`
- `AdapterRecommendationEngine`
- callable adapters
- malformed row validation
- duplicate item ID removal
- non-finite score rejection

所以 Elasticsearch、Vespa、内部 ranking API 或已有 recommender 可以先作为**真实执行/评估对象**接入 production replay，而不必迁移到序枢的内建算法。

当前明确边界：

> adapter-backed engines 已可参与 audit / logged reward replay；自动修改外部 serving policy 仍需要接入方提供显式、安全、typed 的 strategy-write contract。序枢不会为了“自动化”直接猜远端参数并修改生产服务。

---

## 11. Durable execution and concurrency

API 的 run snapshot 持久化：

```text
actions
observations
findings
evidence
decisions
mission
hypotheses
reflections
contradictions
critic
cost
events
```

### Worker lease / fencing

共享 SQLite 保存 `owner_id + lease_until`。

- owner heartbeat；
- lease 过期后新 worker 可以接管；
- stale worker 不能覆盖新 owner terminal state；
- cancel request 写共享状态；
- `cancel_requested` 重启后收敛到 cancelled。

### Workspace revision

Catalog 数据更新位于分布式 critical section：

- active run 存在时阻止更新；
- update lease 期间阻止新任务；
- Catalog 落盘后提交新 revision；
- worker 自动 reload；
- run 绑定 revision，旧 revision 结果不能覆盖新工作区。

---

## 12. Authority / network / perception boundaries

视觉和联网只产生 observation：

```text
attachment / image / web
        ↓
observation + provenance
        ✕
   authority escalation
```

Tool risk：

- `read`
- `simulation`
- `adaptive`
- `network`

只有用户可以允许：

- strategy activation；
- network access。

Production reward log 同样不能授予权限。

---

## 13. What may evolve

允许：

- search / recommendation ranking weights；
- query strategy；
- candidate strategy；
- profile strategy；
- cold-start strategy；
- exploration policy；
- rerank capability；
- response-surface routing prior；
- QD archive / trusted procedural memory。

不允许 optimizer 隐式修改：

- user/network authority；
- RewardSpec；
- evaluation request identity；
- temporal holdout membership；
- trust/verifier semantics；
- tool risk class；
- lease/fencing/recovery rules；
- arbitrary Python source；
- external production system configuration without an explicit typed adapter contract。

---

## 14. Current limitations

当前代码已经修复“proxy metric 被当成 business value”的架构问题，但仍有明确的下一阶段：

1. **完整 OPE stack**：IPS / SNIPS / doubly robust estimator 还没有作为内建 estimator suite；
2. **segment-conditioned portfolio**：当前 durable strategy 仍主要是 domain-global genome；
3. **external write adapter**：已有外部 serving replay contract，但外部策略发布/灰度需要 typed write contract；
4. **online experiment plane**：还没有完整 A/B / interleaving / canary orchestrator；
5. **capability-driven mission compiler**：DeliberationEngine 仍持有部分 Search/RecSys mission semantics；
6. **cost / latency Pareto**：CapabilitySpec 有 complexity，但 latency / infra cost 还没有成为完整 Pareto objective。

这些限制在 README 和代码中必须保持一致，不把未来规划写成已经完成的能力。

---

## 15. Architecture invariants

```text
business reward != proxy quality
request identity never crosses production temporal split
one future request cannot certify durable trust
production reward regression can retire an active strategy
proxy validation cannot hide business regression
RewardSpec is product-owned, not optimizer-owned
holdout precedes trust
activation requires user authority
attachments/network never grant authority
stored strategy must equal effective strategy
cold-start credit requires cold-start evidence
stale workers cannot overwrite current state
external adapters are read-only unless an explicit write contract exists
```
