# Acceptance Criteria

## 功能

- 能创建任务并持续对话；
- 能真实执行搜索；
- 能真实生成个性化推荐；
- 能做搜索整体复核；
- 能做推荐覆盖 / 新鲜度复核；
- 能做候选方案离线比较；
- 候选方案回退时不能给出“建议上线”；
- 支持导入 JSON 工作区数据。

## Harness

- 每次任务必须有 Observe / Plan / Execute / Verify / Complete 记录；
- 每个工具必须有风险等级；
- 用户结论必须来自 tool result；
- 当前实现没有直接线上写操作。

## UI

- 客户页不出现内部算法专有名词；
- 桌面 1180px 以上显示三栏；
- 940px 以下隐藏右侧检查栏；
- 700px 以下进入移动单栏；
- 支持 reduced motion；
- 交互控件有 keyboard focus 状态。

## 质量

- `python -m compileall -q lingjing_harness tests` 通过；
- `node --check frontend/app.js` 通过；
- `pytest -q` 全部通过；
- 构建 wheel 后在干净目录安装，Web API 与前端首页仍可正常加载。
