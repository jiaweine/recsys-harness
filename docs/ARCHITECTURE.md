# Architecture

## Product definition

Recsys Harness 是搜索与推荐领域的自主 Agent Harness。它把业务目标、动态决策、真实工具、项目自有搜推算法、独立验证、长期记忆、自进化和可恢复执行放进一个运行时。

核心原则不是“让 Agent 任意修改自己”，而是 **autonomy under explicit constraints + evolution behind independent evaluation**。

## Runtime control loop

`runtime/harness.py` 不执行一次性固定 plan。每个 cycle 都会：

1. 读取当前 RunState；
2. `OwnedPolicy.decide()` 对仍有价值的动作进行评分；
3. 叠加长期 policy utility；
4. 检查 tool risk / cost / budget；
5. 执行真实 handler；
6. 把 observation、finding、evidence 写回 RunState；
7. checkpoint；
8. 重新决策。

结束后由 `ResultVerifier.final()` 独立检查最终输出，再计算 reward，更新 policy statistics 与 episodic memory。

## Autonomous decision policy

`runtime/policy.py` 负责：

- 目标域识别：search / recommend / both / audit；
- 查询与用户提取；
- 明确解析“允许自动调整”和“不要改变当前策略”等硬约束；
- 基于 observation 插入诊断动作；
- 在证据足够后决定是否进入 evolve；
- 根据历史 reward 对动作评分进行有限调整。

Policy 不依赖外部 LLM，因此执行路径确定、可测试、可回放。外部模型未来可以作为可选语义增强层，但不能绕过 ToolRegistry 和 Verifier。

## Tool plane

`runtime/tools.py` 中每个 ToolSpec 都有：

- name / description；
- risk；
- cost；
- side_effect；
- repeatable；
- input_schema；
- real handler。

风险分为 read / simulation / adaptive。adaptive 工具可以写入内部策略记忆，但激活当前工作区策略仍需要目标明确授权。

当前工具：

- data.inspect
- search.run / search.diagnose / search.audit / search.evolve
- recommend.run / recommend.diagnose / recommend.audit / recommend.evolve

## Persistent memory

`runtime/memory.py` 使用 SQLite 保存三类长期状态。

### Episodic memory

保存 goal、mode、reward、findings、action trace 和 learned events。Recall 使用目标 token overlap、recency 和 reward 排序。

记忆有容量控制：保留近期 episode，也保留跨时间的高价值 episode，避免数据库无限增长或低价值历史淹没有效经验。

### Procedural skill memory

可信搜索/推荐策略以 fingerprint 去重，记录 score、evidence、wins、status 和验证 payload。

状态包括 trusted / active / retired。

### Policy memory

为 context/action 保存 trials 与 reward_sum。后续决策只获得有上限的 learned bonus，避免少量历史把控制器锁死。

## Eval-gated self-evolution

`algorithms/evolution.py` 是自进化核心。

候选来源：

- 当前策略附近的定向变异；
- 确定性随机扰动；
- 历史 trusted strategy；
- elite 继续局部进化。

验证分三层：

1. discovery set：用于候选竞争；
2. holdout set：未参与候选选择，用于检查泛化；
3. full regression：完整受控样本上检查质量、覆盖、最差样本和回退比例。

只有 safe 且形成稳定优势时才 trusted。用户明确授权时才 active。

## Automatic rollback

ToolRegistry 初始化时会复核 active strategy。如果搜索质量/召回或推荐质量/覆盖相对 owned default 出现明显回退，active strategy 自动进入 retired，并恢复稳健默认策略。

回滚事件会进入 `data.inspect`，因此本次 Harness 可以把它作为 finding 呈现，而不是静默发生。

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

服务重启后，recoverable run 会调用 Harness 的 checkpoint rehydration，从已完成 action 之后继续。

Adaptive action 使用稳定 invocation id。已完成的策略学习结果会与 invocation 绑定；重放同一动作时复用第一次结果，避免重复计数或重复产生副作用。

## Search core

搜索由项目自身实现：

- 中英文 tokenize；
- 具体/稀有词 postings 候选获取；
- 字段感知匹配；
- 哈希语义相似度作为有词项证据候选的补充排序信号；
- title / quality / popularity / freshness；
- slate diversity。

`prepare(query)` 生成与策略配置无关的原始特征，`rank_prepared()` 只应用配置并排序。Evolution 因此可以让大量 candidate 共用同一份准备结果。

## Recommendation core

推荐由：

- implicit feedback + recency；
- semantic profile；
- bounded-history co-occurrence graph；
- category preference；
- quality / freshness / popularity / novelty / exploration；
- seen filtering + slate diversity。

同样通过 `prepare(user)` + `rank_prepared()` 复用昂贵特征和图结构，使多候选 evolution 不重复建图。

## Independent verification

Verifier 负责：

- empty/duplicate/non-finite output；
- evolution readiness；
- safe/trusted gate；
- tool failures；
- evidence completeness；
- adaptation permission compliance。

结论只能来自已经执行的 observation 和 verifier 允许的结果。
