# Acceptance Criteria

## Product

- 能创建任务并持续对话；
- 能真实执行搜索与个性化推荐；
- 能进行搜索/推荐整体复核；
- 能导入并持久化 JSON 工作区数据；
- 支持文本、图片和文本型文件共同进入任务；
- 联网能力可配置、可显式授权、可关闭；
- 客户界面不要求用户理解内部算法名、模型名或第三方后端名。

## Autonomous Harness

- 每个任务由 Observe / Decide / Execute / Checkpoint / Replan / Verify / Complete 形成动态闭环；
- 新 observation 能改变后续动作；
- OwnedPolicy 必须基于 evidence gap / information gain / anomaly / cost / learned utility 进行项目自有决策；
- 附件或网页内容不得扩大 adaptive / network 权限；
- 每个工具必须声明 risk / cost / side-effect / input schema；
- 工具总数、总 cost 与运行时间受 budget 控制；
- 用户明确的“不改变当前策略”必须不可被 adaptive tool 绕过；
- 最终答案必须能回到真实 tool result / evidence。

## Multimodal

- Composer 支持选择、拖入和粘贴图片；
- 支持 JSON / CSV / Markdown / TXT 上下文；
- 单次最多 8 个附件；
- 单个附件最大 12MB；
- 不支持的 MIME 类型必须 415；
- 文本附件本地解析；
- 图片视觉服务未配置时必须保留附件且禁止臆测；
- 图片视觉服务配置后只作为 perception，不得接管工具决策或权限。

## Network

- 未配置联网端点时不注册 `web.research`；
- 未获得单次授权时 `network` risk 工具必须拒绝执行；
- 外部结果必须保留 URL；
- 外部结果只能进入当前 evidence，不得直接进入策略晋升数据；
- 网络请求失败不得让其他本地工具失去已获得证据。

## Self-evolution

- evolution 不能只评估一个写死候选；
- 必须支持多候选、elite 继续探索；
- discovery 与 holdout 必须分离；
- 晋升前必须做 full regression；
- 证据不足不得 trusted；
- safe 但没有稳定优势不得 trusted；
- 未授权不得 active；
- active strategy 后续出现明显回退必须自动 rollback；
- trusted / active / retired lifecycle 可持久化；
- episodic / skill / policy memory 必须有容量边界。

## Durability & concurrency

- run checkpoint 必须持久化；
- 服务恢复后必须从 checkpoint rehydrate，不重复已完成的非重复工具；
- adaptive side effect 必须使用 invocation id 做幂等保护；
- 重放同一个 adaptive invocation 必须复用已验证结果；
- Catalog 导入后重启仍应保留；
- 同一个 conversation 同时只能有一个 active run；
- 不同 conversation 可以并行运行。

## Algorithms

- 搜索不存在查询不得靠哈希碰撞产生假相关结果；
- 搜索结果不得重复或产生非有限 score；
- 推荐不得重复、不得包含已看/不可展示内容；
- recommendation graph 对超长历史必须有边界；
- evolution 重复候选评估应复用 prepared features，而不是重复建立相同索引/图。

## UI

- 桌面、平板与移动布局可完成核心任务；
- 移动端不得删除证据能力，必须通过抽屉或等价交互访问；
- reduced motion 生效；
- keyboard focus 可见；
- 中文 IME 确认候选时不得误发送；
- 关键移动端触控目标至少 40px，优先 44px；
- 历史任务切换时不能串入另一个会话的完成消息；
- 一个会话运行时，可以切到另一个任务继续工作；
- 拖入/粘贴附件不应阻断普通文本输入；
- 页面不得依赖远程字体；
- 页面不得出现第三方产品名或模型名；
- 浏览器 page error / console error / same-origin 4xx 在真实产品流程中必须为零。

## Repository hygiene

- 不保留编号版本产品目录或编号版本算法文件；
- 不保留旧的独立候选比较产品路径；
- 不提交 build / dist / egg-info 等生成源码副本；
- 压力测试脚本只在验收时临时运行，不写入仓库。

## Engineering gates

- `python -m compileall -q lingjing_harness tests`；
- `node --check frontend/app.js`；
- `pytest -q`；
- `make demo`；
- CLI smoke；
- wheel build + clean install；
- 从安装后的 wheel 加载真实 Web 首页；
- GitHub CI；
- Chromium desktop + mobile real-product workflow。
