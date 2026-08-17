<div align="center">

# Recsys Harness

### 自主运行搜索与推荐系统的 Deliberative Agent Harness

**把搜推工程从“脚本、指标和人工经验”变成一个会拆任务、追证据、维护假设、动态决策、反思轨迹、独立验证、恢复和学习的运行时。**

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

**Mission Graph · Evidence Requirements · Hypothesis Tracking · Reflection · Trajectory Critic · Real Tools · Durable Recovery**

[真实产品](#真实产品) · [为什么是 Harness](#为什么是-harness) · [Agent Harness 方法](#agent-harness-方法) · [快速启动](#快速启动) · [能力](#能力) · [可靠性](#可靠性) · [系统架构](#系统架构) · [质量门槛](#质量门槛)

</div>

---

## 真实产品

<p align="center"><sub>DESKTOP · FULL WORKSPACE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@f7f415f59581c69e7a0bec4f24d33c637af55691/docs/readme-assets/overview.png" alt="Recsys Harness 真实运行界面" width="96%">
</p>
<p align="center"><sub>一个任务面：目标、对话、运行状态、证据、附件与可恢复执行保持在同一上下文。</sub></p>

> **Real product, real browser.** 截图来自仓库实际启动的应用。CI 用真实浏览器执行产品任务，并在视觉 QA 通过后刷新 README 资产。

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <strong>任务工作台</strong><br>
      <sub>输入、附件、权限与执行入口保持在同一操作面。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@f7f415f59581c69e7a0bec4f24d33c637af55691/docs/readme-assets/workbench.png" alt="Recsys Harness 工作台" width="100%">
    </td>
    <td width="50%" valign="top" align="center">
      <strong>证据与判断</strong><br>
      <sub>真实动作、轨迹、依据与结论集中在检查视图。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@f7f415f59581c69e7a0bec4f24d33c637af55691/docs/readme-assets/evidence.png" alt="Recsys Harness 证据面板" width="100%">
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
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@f7f415f59581c69e7a0bec4f24d33c637af55691/docs/readme-assets/mobile-workspace.png" alt="Recsys Harness 移动端主任务" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@f7f415f59581c69e7a0bec4f24d33c637af55691/docs/readme-assets/mobile-progress.png" alt="Recsys Harness 移动端执行轨迹" width="92%"></td>
    <td width="33%" valign="top" align="center"><img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@f7f415f59581c69e7a0bec4f24d33c637af55691/docs/readme-assets/mobile-evidence.png" alt="Recsys Harness 移动端判断依据" width="92%"></td>
  </tr>
</table>

---

## 为什么是 Harness

搜索与推荐只是能力。真正困难的是：**面对一个模糊业务目标，系统如何决定还缺什么证据、下一步值得做什么、什么时候必须诊断、什么时候可以停止、什么时候有资格学习，以及中断后如何继续。**

Recsys Harness 把这些问题放进一个持久、可审计、受权限约束的运行时，而不是交给一次 prompt 或固定 DAG。

| 普通 tool-calling agent | Recsys Harness |
|---|---|
| 先生成计划，再按计划调用工具 | 先编译 Mission Graph；每个 observation 后重新 deliberation |
| 只记录“调用了什么” | 同时记录 targeted requirement、utility、hypothesis、alternatives 与 rationale |
| 工具结果直接进入下一段文本 | 结果先更新 evidence requirement / hypothesis / contradiction，再决定下一步 |
| 模型觉得“够了”就结束 | Trajectory Critic 判断关键证据是否 terminal，ResultVerifier 再独立验收 |
| 历史对话就是 memory | episodic / procedural / policy memory 分开管理，并且有界 |
| 重启后重新跑 | mission、reflection、critic、actions、observations 一起 checkpoint / resume |
| 联网或附件可能影响指令 | observation 与 authority 严格分离；只有用户能扩大权限 |

> **核心原则：** 自主性不是“让 Agent 想做什么就做什么”，而是让它在明确的证据、权限、风险、预算、验证与恢复边界里，持续选择当前最有价值的动作。

---

## Agent Harness 方法

### 0 · Runtime composition

`AgentHarness` 是运行时主体；`OwnedPolicy` 只负责编译用户意图与权限，并把运行时决策交给 `DeliberationEngine`。

<table>
  <tr>
    <td width="33%" valign="top"><strong>MissionGraph</strong><br><sub>任务级 Evidence Requirements、依赖关系、Hypotheses 与 exit criteria。</sub></td>
    <td width="33%" valign="top"><strong>DeliberationEngine</strong><br><sub>根据任务缺口、多信号 utility、历史收益与风险选择下一动作。</sub></td>
    <td width="33%" valign="top"><strong>TrajectoryCritic</strong><br><sub>检查关键需求是否闭合、矛盾是否处理、轨迹是否可以结束。</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top"><strong>ToolRegistry</strong><br><sub>真实搜索 / 推荐 / audit / evolution / network 能力与风险 contract。</sub></td>
    <td width="33%" valign="top"><strong>ResultVerifier</strong><br><sub>独立核对 evidence、tool failure、权限、mission terminality 与学习门槛。</sub></td>
    <td width="33%" valign="top"><strong>AgentMemory + Checkpoint</strong><br><sub>有界长期学习，并持久化完整 deliberation state。</sub></td>
  </tr>
</table>

对应实现：[`harness.py`](lingjing_harness/runtime/harness.py) · [`policy.py`](lingjing_harness/runtime/policy.py) · [`deliberation.py`](lingjing_harness/runtime/deliberation.py) · [`tools.py`](lingjing_harness/runtime/tools.py) · [`verifier.py`](lingjing_harness/runtime/verifier.py) · [`memory.py`](lingjing_harness/runtime/memory.py)

规范性行为契约：[`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md)

### 1 · Goal / Authority → Mission Graph

用户输入先被解析为目标、领域、实体与权限：

```text
mode · goal · query / user
explore · allow_adaptation · allow_network
constraints
```

随后运行时在**第一次工具调用之前**编译 Mission Graph。它不是固定执行列表，而是当前任务需要证明什么的图：

```text
MissionGraph
├─ EvidenceRequirement: workspace_facts
├─ EvidenceRequirement: search_reproduction
│  └─ search_diagnosis       (dormant until evidence asks for it)
├─ EvidenceRequirement: search_global_quality
├─ EvidenceRequirement: search_candidate_validation
└─ Hypotheses
   ├─ search_local_mismatch
   └─ search_systemic_gap
```

每个 requirement 都带：

```text
key · domain · tool · priority · prerequisites · status · satisfied_by · reason
```

这使“任务分解”与“动作选择”彻底分离：**Mission Graph 定义还需要知道什么；DeliberationEngine 决定现在用哪个动作最值得。**

同时，权限和 observation 分离：附件、图片感知、网页内容可以改变事实和假设，但不能批准联网或策略激活。

### 2 · Evidence Requirements：控制器追的是缺口，不是工具顺序

一次搜索任务可能先需要 `workspace_facts → search_reproduction`。如果复现结果正常，`search_diagnosis` 可以一直 dormant；如果首结果弱匹配或为空，它会在 reflection 中被动态激活。

推荐任务同理：只有真实首屏暴露空结果或冷启动证据时，`recommend_diagnosis` 才进入开放需求。

所以同一句“帮我看看”不会被硬编码成固定流程：

```text
observation A → requirement satisfied → close
observation B → hypothesis strengthened → activate diagnosis
observation C → local/global evidence conflict → record contradiction → investigate
```

Recommendation-only 的优化任务不会因为“explore=true”顺手跑搜索 audit；scope 由 Mission Graph 保持任务级隔离。

### 3 · Hypothesis tracking：解释也有状态

Harness 不只保存 observations，还维护可以被支持、削弱或淘汰的 hypothesis：

```text
key · domain · confidence · status
supporting_evidence · contradicting_evidence
```

例如：

- `search_local_mismatch`：当前 query 可能存在匹配或候选覆盖问题；
- `search_systemic_gap`：问题可能来自整体搜索质量而不是单点；
- `recommend_cold_start`：用户可能缺少行为或可展示池证据；
- `recommend_systemic_gap`：问题可能是全局推荐质量缺口。

工具 observation 会改变 hypothesis confidence；活跃 hypothesis 又会反过来影响下一步 action utility。

### 4 · Multi-signal deliberation：不是一棵 if/else 路由树

候选动作只来自**当前 open requirement + 已满足 prerequisites + 当前权限下可调用的 ToolSpec**。

当前控制器综合这些信号：

| Signal | 作用 |
|---|---|
| `priority` | requirement 对任务闭合的重要性 |
| `information_gain` | 预计能补多少新信息 |
| `evidence_gap` | 当前关键证据缺口大小 |
| `hypothesis_pressure` | 高置信未决解释对该动作的推动 |
| `contradiction_pressure` | 本地 / 全局证据冲突是否需要优先处理 |
| `cost_pressure` | capability 成本相对预算的压力 |
| `risk_pressure` | read / simulation / network / adaptive 风险 |
| `domain_novelty` | 避免在同一条路径机械重复 |
| `stagnation_pressure` | 多轮没有新状态变化时降低重复路径价值 |
| `learned_policy_bonus` | 相似任务历史真实 reward 的有界先验 |

当前实现的核心打分结构是：

```text
utility(action)
  = 0.30 × priority
  + 0.22 × information_gain
  + 0.18 × evidence_gap
  + 0.12 × hypothesis_pressure
  + 0.08 × contradiction_pressure
  + domain_novelty
  + learned_policy_bonus
  - 0.07 × cost_pressure
  - 0.05 × risk_pressure
  - failure_penalty
  - stagnation_penalty
```

每次 decision 都会把 target requirement、utility decomposition、active hypotheses、rationale 与 top alternatives 写入 trace，因此“为什么选这个工具而不是另一个”可以被复核。

### 5 · Execute → Reflect → Replan

真正的控制循环不是 Plan → Execute，而是：

```text
compile goal + authority
        ↓
build Mission Graph
        ↓
recall bounded memory
        ↓
┌──────────────────────────────────────────────┐
│ deliberate over currently valid candidates  │
│        ↓                                     │
│ execute one real, risk-guarded capability    │
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

**工具返回不是流程终点，而是新的世界状态。** 每个 completed / failed action 后都会产生 reflection，然后才允许下一次 decision。

### 6 · Contradiction & stagnation：避免“收集很多证据但没在思考”

复杂任务经常不是证据不足，而是证据互相打架。

例如：单个 query 复现看起来正常，但全局 search audit 显著偏低。Harness 会把它记录为 material contradiction，并激活诊断 requirement；Trajectory Critic 不会把未经调查的矛盾视为干净结束。

同时，如果连续动作没有改变 requirement、hypothesis 或 contradiction，`stagnation` 会增加，对重复路径施加 penalty，逼迫 deliberation 转向更有信息价值的路径或停止。

### 7 · Trajectory Critic：什么时候“够了”不是模型一句话

`TrajectoryCritic` 独立于 action selection，持续计算：

```text
evidence_coverage
terminal_coverage
unresolved critical/high requirements
blocked requirements
contradictions / unresolved_contradictions
stagnation
confidence
```

关键 requirement 只有三种有意义状态：

- `satisfied`：所需 evidence 已经由真实 capability 支撑；
- `blocked`：能力缺失、评估样本不足或执行失败，无法在当前边界继续；
- `open`：仍然应该继续调查。

只要还有可执行的 critical / high requirement，或者 material contradiction 没有处理，critic 就不会给 clean close。

### 8 · Independent verifier：轨迹闭合之后还要再验一次

最终 `ResultVerifier` 不负责选动作，它只负责拒绝未经证明的结果：

```text
executed_tools
no_failed_tools
evidence_backed
adaptation_respected
mission_terminal
contradictions_resolved
```

所以“控制器觉得做完了”和“系统允许把它作为完整结果返回”是两个不同判断。

策略学习还有第二套更严格门槛：

```text
Discovery competition
→ Independent holdout
→ Full regression
→ Robustness gate
→ Trusted strategy memory
→ optional activation
```

没有独立 holdout，只能探索；网络 evidence 不能进入策略晋升样本；即使形成 trusted strategy，是否激活仍受用户权限控制。

### 9 · Tool contract：真实能力，但不越权

每个 capability 声明显式 contract：

```text
risk · cost · side_effect · repeatable · input_schema
```

| Risk | Harness 允许什么 |
|---|---|
| `read` | 读取、复现、诊断，不修改策略 |
| `simulation` | 离线 audit / evaluation |
| `adaptive` | 探索并写可信策略记忆；激活仍需要授权 |
| `network` | 只有当前 run 明确允许联网时才可调用 |

工具 contract 是控制平面的硬边界，不由 observation、附件、网页或历史 memory 改写。

### 10 · Typed memory：学习对象不是一坨聊天记录

| Memory | 保存什么 | 如何影响未来 |
|---|---|---|
| **Episodic** | 类似任务的目标、动作、findings、reward | 新任务启动时召回相关执行经验 |
| **Procedural** | 通过验证的搜索 / 推荐策略 | trusted / active / retired 生命周期 |
| **Policy** | 某上下文下某 action 的历史 reward | 作为有界 `learned_policy_bonus` 参与 deliberation |

历史经验只是一种有限先验，不能覆盖当前 evidence、risk contract 或用户权限。

### 11 · Recoverable deliberation：恢复的不只是工具列表

checkpoint 持久化：

```text
actions · observations · findings · evidence · decisions
mission graph · hypotheses · reflections · contradictions
critic · stagnation · spent cost · events
```

恢复时 Harness rehydrate 整个 Mission Graph 和 reasoning state，从已完成动作之后继续；Adaptive action 使用稳定 invocation id 做幂等保护，避免进程在“写入策略经验”和“保存 checkpoint”之间中断时重复学习。

### 12 · Contract + probes：Harness 行为必须可测试

[`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md) 定义 H1–H12 的 MUST / MUST NOT 规则，包括 authority、mission scope、re-deliberation、decision provenance、contradiction、critic closure、learning gate 与 durable state。

CI 会执行：

```bash
python scripts/probe_harness_contract.py
pytest -q
```

probe 直接验证任务 scope、权限隔离、decision provenance、弱证据触发动态 diagnosis、critic closure 与 reflection trace，不依赖 README 自己声称“这是一个高级 Agent”。

---

## 快速启动

**要求：Python 3.11+。核心搜推、控制、验证与 memory 默认本地运行；图片感知和联网研究是可选能力。**

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
    <td width="33%" valign="top"><strong>01 · Real Search</strong><br><sub>查询复现、诊断、离线 audit 与候选策略探索。</sub></td>
    <td width="33%" valign="top"><strong>02 · Real Recommendation</strong><br><sub>用户首屏、冷启动诊断、覆盖 / 新鲜度 / 分散度复核。</sub></td>
    <td width="33%" valign="top"><strong>03 · Eval-gated Evolution</strong><br><sub>Discovery、holdout、regression、robustness、trusted memory。</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top"><strong>04 · Multimodal Context</strong><br><sub>文本、JSON / CSV / Markdown / TXT 与图片进入受限 observation。</sub></td>
    <td width="33%" valign="top"><strong>05 · Controlled Network</strong><br><sub>联网显式授权；外部来源保留 provenance，只作为 evidence。</sub></td>
    <td width="33%" valign="top"><strong>06 · Long-term Learning</strong><br><sub>Episodic / procedural / policy memory，各自有界。</sub></td>
  </tr>
</table>

<details>
<summary><strong>搜索 / 推荐实际覆盖</strong></summary>

| 搜索 | 推荐 |
|---|---|
| 查询复现与诊断 | 隐式反馈与时间衰减 |
| 字段感知匹配与排序 | 用户内容偏好 |
| 质量 / 热度 / 新鲜度信号 | 有界 item-item 共现 |
| 结果多样性 | 质量 / 新鲜度 / 热度 / 新颖度 |
| Recall / MRR / NDCG 离线复核 | Coverage / Diversity / Freshness / Novelty 复核 |

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
    <td width="25%" valign="top"><strong>Guardrails</strong><br><sub>risk / cost / side effect / schema 显式化。</sub></td>
    <td width="25%" valign="top"><strong>Durability</strong><br><sub>动作与 deliberation state 一起 checkpoint。</sub></td>
    <td width="25%" valign="top"><strong>Fencing</strong><br><sub>lease + heartbeat 阻止 stale worker 覆盖新状态。</sub></td>
    <td width="25%" valign="top"><strong>Coherence</strong><br><sub>workspace revision 保证跨 worker 数据一致。</sub></td>
  </tr>
</table>

<details open>
<summary><strong>Durable execution</strong></summary>

同一个 conversation 同时只允许一个 active run；不同 conversation 可以并行。Run state 使用 checkpoint；Adaptive invocation 幂等；`cancel_requested` 可跨进程安全收敛。

SQLite 同时承担 conversation reservation、worker owner、lease、heartbeat 与 terminal-state fencing。迟到 worker 不能覆盖新 owner 已提交的状态。

</details>

<details>
<summary><strong>Workspace coherence</strong></summary>

Catalog 有共享 revision 和 update lock。数据导入期间不接受新的 run；revision 提交后，其他 worker 会重新加载一致的 Catalog / Harness。

</details>

---

## 系统架构

<p align="center"><sub>AGENT HARNESS RUNTIME · CONTROL · EVIDENCE · TRUST · STATE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@f7f415f59581c69e7a0bec4f24d33c637af55691/docs/readme-assets/architecture.svg" alt="Recsys Harness system architecture" width="97%">
</p>

当前运行时的控制关系可以概括为：

```text
Goal + Authority
      ↓
MissionGraph ────────────────┐
      ↓                      │
DeliberationEngine           │
      ↓                      │
ToolRegistry → Observation   │
      ↓                      │
Reflection ──→ Hypotheses    │
      │        Contradiction │
      │        Requirements  │
      ↓                      │
TrajectoryCritic ── replan ──┘
      ↓ close
ResultVerifier
      ↓
Memory + Durable State
```

<table>
  <tr>
    <td width="33%" valign="top"><strong>Control plane</strong><br><sub>Mission Graph、Deliberation、Reflection 与 Trajectory Critic 持有运行时决策权。</sub></td>
    <td width="33%" valign="top"><strong>Evidence plane</strong><br><sub>真实搜推、诊断、audit、附件感知与网络研究只提供 observation / evidence。</sub></td>
    <td width="33%" valign="top"><strong>Trust & state plane</strong><br><sub>Verifier、holdout、typed memory、checkpoint、lease 与 workspace revision 约束长期行为。</sub></td>
  </tr>
</table>

**Architecture invariants**

`attachments never grant permission` · `network evidence never promotes strategy` · `holdout precedes trust` · `critic gates clean closure` · `stale workers cannot overwrite current state`

> 设计说明：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · 运行时契约：[`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md)

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

完整格式见 [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)。

---

## 质量门槛

```bash
make check
make test
make demo
python scripts/probe_harness_contract.py
```

CI 覆盖：Python 编译与完整回归、Harness contract probe、CLI smoke、wheel 干净安装、真实浏览器桌面 / 移动流程、附件与联网边界、瞬时 polling 重连、关键移动触控目标、多 worker lease / fencing / workspace revision，以及生产访问与限流契约。

---

## Repository map

```text
frontend/                          产品 UI
lingjing_harness/
  algorithms/                      搜索、推荐、评估、候选策略探索
  runtime/
    harness.py                     Agent Harness orchestration / checkpoint loop
    policy.py                      用户目标、scope 与 authority 编译
    deliberation.py                Mission Graph / hypotheses / reflection / critic
    contracts.py                   runtime state / requirement / decision contracts
    tools.py                       真实能力与 risk / cost / side-effect contract
    verifier.py                    独立结果、权限与 trajectory 验证
    memory.py                      episodic / procedural / policy memory
    perception.py                  多模态 observation
    network.py                     可控网络 evidence
  api.py                           API、认证、附件、恢复、工作区运行时
  store.py                         durable run、lease、revision、共享限流
tests/test_deliberation.py         mission / reflection / critic 回归测试
docs/HARNESS_CONTRACT.md           H1–H12 规范性 Harness 行为契约
scripts/probe_harness_contract.py  可执行 contract probe
scripts/capture_readme_assets.py   真实浏览器 QA 与 README 截图
```

> 仓库只维护**一条主线实现**。搜索与推荐是能力；Agent Harness 是控制、验证、学习与恢复这些能力的产品主体。

---

<div align="center">

### Search and recommendation are the capabilities. The Agent Harness is the product.

**Compile · Deliberate · Execute · Reflect · Critique · Verify · Learn · Recover**

<sub>Mission graphs · Evidence gaps · Typed authority · Real tools · Independent gates · Durable state</sub>

</div>
