# Data Format

数据使用一个 JSON 对象，三个数组都可以为空，但 `items` 至少需要一条有效记录。

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

## interactions

- `user_id`
- `item_id`
- `event`: view / click / favorite / cart / purchase
- `weight`: 可覆盖默认事件权重
- `timestamp`: 递增数值即可，第一版用于相对时间衰减

不存在的 item 会被自动过滤。

## query_labels

- `query`
- `relevant`: 相关 item id 数组

这部分用于离线复核搜索表现，不会展示在客户 UI 中。
