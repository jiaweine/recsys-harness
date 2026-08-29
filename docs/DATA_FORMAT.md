# Data Format

数据使用一个 JSON 对象。`items` 至少需要一条有效记录；其余部分可以按使用场景逐步补齐。

```text
items                 serving / ranking content
interactions          user-profile / collaborative evidence
query_labels          offline search relevance guardrails
events                production exposure / outcome evidence
reward_spec           product-owned business reward contract
```

一个重要边界：**`interactions` 与 `events` 不同。**

`interactions` 可以训练或构造用户画像；`events` 则记录某次真实 request 在某个 policy/model 下向用户展示了什么，以及后续发生了什么，用于 production replay / temporal evaluation。

---

## items

必需：

- `id` 或 `item_id`
- `title` 或 `name`

可选：

- `text`
- `categories`
- `popularity`：非负数
- `quality`：0–1
- `freshness`：0–1
- `eligible`：是否允许展示
- `metadata`

示例：

```json
{
  "id": "sku-1001",
  "title": "轻量露营灯",
  "text": "暖光、磁吸、可充电",
  "categories": ["露营", "灯具"],
  "popularity": 820,
  "quality": 0.93,
  "freshness": 0.88,
  "eligible": true,
  "metadata": {"brand": "demo"}
}
```

---

## interactions

用于用户 profile / collaborative evidence：

- `user_id`
- `item_id`
- `event`: view / click / favorite / cart / purchase / 自定义
- `weight`: 可覆盖默认事件权重
- `timestamp`: 数值时间；当前内建推荐用于相对时间衰减

不存在的 item 会被过滤。

示例：

```json
{
  "user_id": "u-42",
  "item_id": "sku-1001",
  "event": "purchase",
  "weight": 3.2,
  "timestamp": 1710000000
}
```

这类行为记录**不自动等于 production exposure log**。如果不知道用户当时看到了哪些结果、由哪个 policy 产生，就不能把它当作完整的反事实评估证据。

---

## query_labels

用于 Search relevance guardrail：

- `query`
- `relevant`: 相关 item id 数组

```json
{
  "query": "露营灯",
  "relevant": ["sku-1001", "sku-1002"]
}
```

同一个 query 的重复 label 会在 Catalog 边界合并 relevance set，因此一个 query identity 不会因为重复导入而跨 discovery / holdout。

---

# Production value data

## reward_spec

业务价值由接入方显式定义，不由序枢写死。

```json
{
  "reward_spec": {
    "weights": {
      "impression": 0,
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

每条 event 的 reward 为：

```text
configured event weight × event.value
```

因此 `value` 可以承担业务自己的量纲，例如：

- purchase value = 订单价值；
- watch value = 有效观看时长；
- conversion value = 转化价值；
- click value = 1。

如果希望 purchase 只算一次而不按金额加权，直接保持 `value=1`。

### 约束

- weights 必须是有限数值；
- reward 可以为负；
- `inverse_propensity_cap` 必须在 `[1, 100]`；
- 数值字符串可以在 ingestion 边界规范化为数值；布尔值不作为数值接受；
- RewardSpec 属于产品/业务 contract，不属于 optimizer 的搜索空间。

---

## events

每一行代表一个 production exposure / outcome observation。

必需：

- `request_id`
- `timestamp`
- `surface`: `search` 或 `recommend`
- `item_id`

常用字段：

- `event`: impression / click / favorite / cart / purchase / hide / refund / 自定义
- `value`: 默认 1
- `propensity`: `(0, 1]`，如果 logging policy 能提供
- `position`: 从 1 开始
- `user_id`: Recommendation request
- `query`: Search request
- `policy_id`
- `model_version`
- `experiment_id`
- `metadata`

### 数值 ingestion 边界

- `timestamp`、`value`、`propensity` 接受有限数值或可解析的数值字符串；
- `true` / `false` 不作为数值接受，避免被隐式解释成 `1` / `0`；
- `position` 必须是正整数或整数字符串，例如 `2` / `"2"`；
- `position` 不接受 `1.0`、`"1.0"` 或其他浮点形式，也不会做截断；
- `NaN`、`+inf`、`-inf` 不属于有效 production evidence。

### Search 示例

```json
{
  "request_id": "search-2026-0001",
  "timestamp": 1760000000,
  "surface": "search",
  "query": "露营灯",
  "item_id": "sku-1001",
  "event": "click",
  "value": 1,
  "position": 2,
  "propensity": 0.35,
  "policy_id": "search-prod-v17",
  "model_version": "ltr-2026-08-18",
  "experiment_id": "exp-search-47"
}
```

### Recommendation 示例

```json
{
  "request_id": "rec-2026-0042",
  "timestamp": 1760000010,
  "surface": "recommend",
  "user_id": "u-42",
  "item_id": "sku-1009",
  "event": "purchase",
  "value": 1,
  "position": 4,
  "propensity": 0.22,
  "policy_id": "home-feed-v31",
  "model_version": "ranker-83",
  "experiment_id": "exp-home-12"
}
```

同一个 request 通常有多行：impression、click、purchase 等可以共享同一个 `request_id`。

序枢按 `request_id` 做 temporal split，因此同一次真实 request 不会被拆到 discovery 和 future holdout 两边。

---

## events 与 temporal holdout

Production replay 按 request 的时间排序：

```text
older request identities → discovery
newer request identities → future holdout
```

而不是随机把同一时段的数据拆成两半。

当 production request 太少时：

- 仍可以做探索 / replay；
- 但不会因为一个 future request 就获得 durable trust。

当前 public trust 还要求 domain guardrail 本身存在独立 holdout，例如 Search relevance label 或 Recommendation warm-user slice。

---

## estimator 说明

当前内建 production replay 可能返回：

```text
logged_replay
propensity_weighted_logged_replay
```

存在 propensity 时会使用 capped inverse propensity weighting。

但这**不等于完整、无偏的 IPS / SNIPS / Doubly Robust OPE**。没有被历史 policy 曝光的 item，其真实 outcome 仍不可观察。

所以该结果用于：

- candidate routing；
- temporal holdout evidence；
- active regression detection；
- production reward-aware trust。

更严格的 counterfactual estimator 可以通过后续 production adapter 扩展。

---

## 完整示例

```json
{
  "items": [
    {
      "id": "sku-1",
      "title": "轻量露营灯",
      "categories": ["露营", "灯具"],
      "quality": 0.92,
      "freshness": 0.88
    }
  ],
  "interactions": [
    {
      "user_id": "u-1",
      "item_id": "sku-1",
      "event": "click",
      "timestamp": 100
    }
  ],
  "query_labels": [
    {
      "query": "露营灯",
      "relevant": ["sku-1"]
    }
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
      "timestamp": 200,
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
