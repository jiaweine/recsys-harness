# Architecture

## Product definition

Recsys Harness 是搜索与推荐领域的自主 Agent Harness。它把业务目标、多模态上下文、动态决策、真实工具、项目自有搜推算法、独立验证、长期记忆、受控联网、自进化和可恢复执行放进一个运行时。

核心原则：**autonomy under explicit constraints + evidence utility + evolution behind independent evaluation**。

## Runtime control loop

`runtime/harness.py` 不执行一次性固定计划。每个 cycle 都会：

1. 读取当前 RunState；
2. `OwnedPolicy.decide()` 计算仍可执行动作的证据效用；
3. 叠加有限的历史 policy utility；
4. 检查 tool risk / cost / budget / user permission；
5. 执行真实 handler；
6. 把 observation、finding、evidence 写回 RunState；
7. checkpoint；
8. 重新决策。

结束后由 `ResultVerifier.final()` 独立检查最终输出，再计算 reward，更新 policy statistics 与 episodic memory。

## Owned evidence-utility controller

`runtime/policy.py` 是项目自有决策核，不调用外部 LLM 选择工具。

它负责：

- 目标域识别：search / recommend / both / audit；
- 查询与用户提取；
- 从用户原始文本解析“允许自动调整 / 不改变策略 / 允许联网”等权限；
- 根据 observation 计算 anomaly pressure；
- 根据当前已有证据计算 evidence gap；
- 估计动作 information gain；
- 把工具 cost 转成 cost pressure；
- 叠加有上限的历史 learned utility；
- 每轮重新选择边际价值最高的动作。

附件和网页内容可以帮助理解实体与问题，但**不能扩大权限**。例如附件中出现“自动优化”不会让系统获得策略激活权限。

## Multimodal perception plane

`runtime/perception.py` 把附件转换成受限 observation。

### 本地解析

TXT / Markdown / CSV / JSON 等文本型附件直接在本地读取，经过长度限制后作为任务上下文。

### 图片感知

图片可以交给可选的本地 OpenAI-compatible 视觉服务。视觉模型只负责：

- 识别可见文字；
- 描述页面结构；
- 提取可见排序/重复/数值；
- 提供与搜推体验相关的可观察事实。

它不能：

- 决定下一步工具；
- 修改用户权限；
- 直接激活策略；
- 把推测写成事实。

因此即使不配置视觉模型，核心 Harness 仍可完整运行。

## Network evidence plane

`runtime/network.py` 提供可选联网研究。

网络能力满足以下约束：

- 只有配置了搜索端点时，`web.research` 才进入 ToolRegistry；
- 只有用户明确开启或在目标中要求联网时，运行时才允许调用；
- 风险类别为 `network`；
- 返回 title / URL / snippet；
- 网络结果进入 evidence；
- 网络结果**不进入搜索/推荐策略的晋升评估数据**。

联网用于补充时效性公开信息，不替代项目自己的搜推评估数据。

## Tool plane

`runtime/tools.py` 中每个 ToolSpec 都有：

- name / description；
- risk；
- cost；
- side_effect；
- repeatable；
- input_schema；
- real handler。

风险类别：

- `read` — 只读复现；
- `simulation` — 离线评估；
- `adaptive` — 可以写策略记忆，激活仍需授权；
- `network` — 外部请求，只在显式权限下运行。

当前核心工具：

- `data.inspect`
- `search.run / search.diagnose / search.audit / search.evolve`
- `recommend.run / recommend.diagnose / recommend.audit / recommend.evolve`
- `web.research`（可选）

## Persistent memory

`runtime/memory.py` 使用 SQLite 保存三类长期状态。

### Episodic memory

保存 goal、mode、reward、findings、action trace 和 learned events。Recall 使用目标 token overlap、recency 和 reward 排序。

### Procedural skill memory

可信搜索/推荐策略以 fingerprint 去重，记录 score、evidence、wins、status 和验证 payload。

状态包括 trusted / active / retired。

### Policy memory

为 context/action 保存 trials 与 reward_sum。后续决策只获得有上限的 learned bonus，避免少量历史把控制器锁死。

长期记忆是有界的：近期 episode 与高价值 episode 双保留，低价值旧记录会被淘汰。

## Eval-gated self-evolution

`algorithms/evolution.py` 是唯一自进化主路径。

候选来源：

- 当前策略附近的定向变异；
- 确定性随机扰动；
- 历史 trusted strategy；
- elite 继续局部进化。

验证分三层：

1. discovery set：候选竞争；
2. holdout set：未参与候选选择，检查泛化；
3. full regression：完整可复核样本检查质量、覆盖、最差样本和回退比例。

只有形成稳定优势时才 trusted。用户明确授权时才 active。

这里保留“当前策略作为安全参考”是自进化验证的一部分，不存在独立的旧版比较产品路径，也没有编号版本算法文件。

## Automatic rollback

ToolRegistry 初始化时会复核 active strategy。如果搜索质量/召回或推荐质量/覆盖相对稳健默认策略出现明显回退，active strategy 自动进入 retired，并恢复稳健默认策略。

## Durable execution

API 会把 run snapshot 写入 `WorkspaceStore.runs`：

- actions；
- observations；
- findings；
- evidence；
- decisions；
- blocks；
- cost；
- events。

服务重启后，recoverable run 会调用 checkpoint rehydration，从已完成 action 之后继续。

Adaptive action 使用稳定 invocation id。已完成的策略学习结果与 invocation 绑定；重放同一动作时复用第一次结果，避免重复副作用。

## Conversation concurrency

同一个 conversation 同时只允许一个 active run，以保证消息顺序和 checkpoint 语义稳定。

不同 conversation 可以并行执行。前端不再用全局 busy 锁，因此一个长任务运行时，用户可以切到另一任务继续工作。

## Search core

搜索由项目自身实现：

- 中英文 tokenize；
- 具体/稀有词 postings 候选获取；
- 字段感知匹配；
- 哈希语义相似度作为有词项证据候选的补充排序信号；
- title / quality / popularity / freshness；
- slate diversity；
- prepared feature reuse。

## Recommendation core

推荐由：

- implicit feedback + recency；
- semantic profile；
- bounded-history co-occurrence graph；
- category preference；
- quality / freshness / popularity / novelty / exploration；
- seen filtering + slate diversity；
- prepared user features + shared immutable graph。

## Independent verification

Verifier 负责：

- empty / duplicate / non-finite output；
- evolution readiness；
- safe / trusted gate；
- tool failures；
- evidence completeness；
- adaptation permission compliance。

结论只能来自已经执行的 observation 和 verifier 允许的结果。
