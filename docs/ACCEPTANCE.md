# Acceptance Criteria

## Product

- 能创建任务并持续对话；
- 能真实执行搜索与个性化推荐；
- 能进行搜索/推荐整体复核；
- 能导入并持久化 JSON 工作区数据；
- 客户界面不要求用户理解内部算法名。

## Autonomous Harness

- 每个任务由 Observe / Decide / Execute / Replan / Verify / Complete 形成动态闭环；
- 新 observation 能改变后续动作；
- 每个工具必须声明 risk / cost / side-effect / input schema；
- 工具总数、总 cost 与运行时间受 budget 控制；
- 用户明确的“不改变当前策略”必须不可被 adaptive tool 绕过；
- 最终答案必须能回到真实 tool result / evidence。

## Self-evolution

- evolution 不能只比较一个写死候选；
- 必须支持多候选、elite 继续探索；
- discovery 与 holdout 必须分离；
- 晋升前必须做 full regression；
- 证据不足不得 trusted；
- safe 但没有稳定优势不得 trusted；
- 未授权不得 active；
- active strategy 后续出现明显回退必须自动 rollback；
- trusted / active / retired lifecycle 可持久化；
- episodic / skill / policy memory 必须有容量边界。

## Durability

- run checkpoint 必须持久化；
- 服务恢复后必须从 checkpoint rehydrate，不重复已完成的非重复工具；
- adaptive side effect 必须使用 invocation id 做幂等保护；
- 重放同一个 adaptive invocation 必须复用已验证结果；
- Catalog 导入后重启仍应保留。

## Algorithms

- 搜索不存在查询不得靠哈希碰撞产生假相关结果；
- 搜索结果不得重复或产生非有限 score；
- 推荐不得重复、不得包含已看/不可展示内容；
- recommendation graph 对超长历史必须有边界；
- evolution 重复候选评估应复用 prepared features，而不是重复建立相同索引/图。

## UI

- 桌面与移动布局可用；
- reduced motion 生效；
- keyboard focus 可见；
- 历史任务切换时不能串入另一个会话的完成消息；
- 长任务 polling 不应过早误报超时；
- 浏览器 page error / console error 在真实产品流程中必须为零。

## Engineering gates

- `python -m compileall -q lingjing_harness tests`；
- `node --check frontend/app.js`；
- `pytest -q`；
- `make demo`；
- CLI smoke；
- wheel build + clean install；
- 从安装后的 wheel 加载真实 Web 首页；
- GitHub CI；
- Chromium real-product workflow。
