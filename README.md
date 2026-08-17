<div align="center">

<sub>RECSYS HARNESS · AUTONOMOUS SEARCH & RECOMMENDATION CONTROL LAYER</sub>

# Search & recommendation that can decide, learn, verify and recover.

**把“发现问题 → 决策 → 实验 → 验证 → 学习 → 回滚”收敛进一套可持续运行的搜推 Agent Harness。**

Goal in · Dynamic replan · Real tools · Persistent memory · Eval-gated evolution · Evidence out

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-1f6f5c?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Autonomous%20Runtime-1f6f5c?logo=fastapi&logoColor=white)
![Core](https://img.shields.io/badge/Core-No%20external%20LLM%20required-c8f06a?labelColor=16231b)
![Evolution](https://img.shields.io/badge/Evolution-Eval%20gated-c8f06a?labelColor=16231b)

[Product](#product) · [Autonomy](#autonomy) · [Self-evolution](#self-evolution) · [System map](#system-map) · [Quick start](#quick-start) · [API](#api-surface)

</div>

<p align="center">
  <img src="docs/readme-assets/product-run.png" alt="Recsys Harness executing a real search experience task" width="100%" />
</p>

<p align="center"><sub>真实产品运行截图 · 页面由当前前端实际渲染，任务由当前 Harness 实际执行。</sub></p>

---

## Product

**Recsys Harness** 是一个面向搜索与推荐系统的垂直 **Agent Harness**。

它不是聊天机器人套一个算法接口，也不是只会跑固定工作流的后台。用户给出业务目标后，系统会根据**当前数据、执行结果、风险预算、历史经验和用户约束**持续决定下一步；每执行一个动作都会重新观察并 Replan，直到证据足够、预算耗尽或安全门槛阻止继续。

<table>
<tr>
<td width="25%" valign="top"><strong>Autonomous</strong><br><br>不是一次性计划。每轮都重新评分下一步动作，并根据新证据改变路径。</td>
<td width="25%" valign="top"><strong>Self-evolving</strong><br><br>自动生成候选策略，通过探索集、留出集和全量回归后才晋升为长期经验。</td>
<td width="25%" valign="top"><strong>Memory-native</strong><br><br>保存 episode、策略 skill 与决策收益；后续相似任务会主动召回和复用。</td>
<td width="25%" valign="top"><strong>Recoverable</strong><br><br>执行过程持续 checkpoint；中断后从已完成动作之后继续，自适应副作用具备幂等保护。</td>
</tr>
</table>

### Product surface

客户界面仍然保持业务语言，不把内部算法复杂度甩给使用者。

<p align="center">
  <img src="docs/readme-assets/product-workbench.png" alt="Recsys Harness experience workbench" width="49%" />
  <img src="docs/readme-assets/product-evidence.png" alt="Recsys Harness evidence panel" width="49%" />
</p>

<p align="center"><sub>左：目标驱动的体验工作台 · 右：一次真实执行后的判断依据</sub></p>

用户看到的是 **搜索体验 / 推荐体验 / 方案复核 / 全局体检 / 执行记录 / 判断依据 / 当前数据**；自主决策、候选进化、稳健性门槛和长期记忆留在 Harness 内部。

---

## Autonomy

### Not “plan once, execute forever”

当前 Runtime 是一个动态决策循环：

1. **Observe**：读取工作区、用户目标、硬约束和相关历史经验；
2. **Decide**：根据当前 observation 对可用工具进行评分；
3. **Act**：执行风险允许且仍在预算内的真实工具；
4. **Verify locally**：结构检查、结果证据和异常进入当前状态；
5. **Replan**：重新计算下一步，而不是继续照着旧计划走；
6. **Stop**：证据足够、无新增有效动作、预算触顶或风险门槛触发时结束；
7. **Verify globally**：独立 ResultVerifier 检查最终结论是否真的有证据支持；
8. **Learn**：把本次 reward、动作收益、episode 和通过门槛的新 skill 写入长期记忆。

例如一个搜索任务如果复现结果出现空结果或弱匹配，Harness 会自动插入诊断工具；只有整体复核证据足够时，才允许进入策略进化。推荐冷启动也会触发不同的诊断路径。

### Decision budget

每个工具都有真实的：

- `risk`
- `cost`
- `side_effect`
- `input_schema`
- `repeatable`

Runtime 同时受最大工具数、最大成本和最大运行时间约束。Agent 可以自主选择动作，但不能无限调用工具。

---

## Self-evolution

这里的“自进化”**不是让 Agent 无限制修改自己的源代码**。

Recsys Harness 使用的是 **eval-gated self-evolution**：系统可以自主提出新策略，但新策略只有经过独立验证才能成为长期能力。

### Evolution protocol

<table>
<tr>
<td valign="top"><strong>1 · Discovery</strong><br><br>从当前策略、历史可信策略、定向变异和确定性探索中生成多组候选。</td>
<td valign="top"><strong>2 · Competition</strong><br><br>候选在探索样本上竞争，多代保留 elite，再继续局部变异。</td>
<td valign="top"><strong>3 · Holdout</strong><br><br>最终候选必须通过未参与选择的留出样本，避免“用同一批数据选又用同一批数据证明”。</td>
<td valign="top"><strong>4 · Full regression</strong><br><br>再回到完整可复核样本检查质量、覆盖、最差样本和回退比例。</td>
</tr>
<tr>
<td valign="top"><strong>5 · Promote</strong><br><br>只有同时满足安全与优势门槛的候选才写入 procedural memory。</td>
<td valign="top"><strong>6 · Activate</strong><br><br>只有用户目标明确授权“自动优化 / 允许调整”时，可信策略才可成为当前工作区 active strategy。</td>
<td valign="top"><strong>7 · Observe drift</strong><br><br>后续启动时会复核 active strategy 与稳健默认策略的差距。</td>
<td valign="top"><strong>8 · Rollback</strong><br><br>发现质量或覆盖显著回退时自动 retire 已学习策略并恢复稳健策略。</td>
</tr>
</table>

### What actually learns

当前会持续进化三类东西：

- **Episodic memory**：类似目标以前发生了什么、执行了哪些动作、最终 reward 如何；
- **Procedural skills**：通过验证的搜索 / 推荐策略配置、证据规模、胜出次数和状态；
- **Decision utility**：某类任务里某个工具过去的平均收益，用于未来 Replan 的动作评分。

长期记忆不是无限增长。系统同时保留近期 episode 与高价值 episode，并限制可信 skill 数量，低价值旧经验会被自动淘汰或 retired。

### Adaptation boundaries

下面两句话语义不同：

```text
给我一个推荐改进方案，先离线，不要上线。
```

系统可以自主探索并学习可信经验，但**不会改变当前工作区策略**。

```text
检查推荐体验，自动优化并持续学习。
```

系统才获得激活可信策略的权限；即使获得权限，候选仍然必须通过 discovery / holdout / full regression / robustness gate。

---

## Durable execution

自主系统不能把“进程不崩”当成可靠性。

当前执行过程持续写入 SQLite checkpoint，包括：

- 已完成 actions；
- observations；
- findings / evidence；
- decisions；
- spent cost；
- event trace。

服务重启后会从 checkpoint **精确恢复 RunState**，已完成的非重复工具不会重新执行。

自适应工具还带稳定的 invocation id。一次策略学习即使发生“副作用已经写入、进程随后崩溃”的极端窗口，恢复重放也会复用第一次的结果，不会把一次学习重复计算成多次胜利。

---

## System map

<p align="center">
  <img src="docs/readme-assets/system-map.svg" alt="Detailed autonomous architecture map of Recsys Harness" width="100%" />
</p>

| Plane | Responsibility |
|---|---|
| **Experience** | 自然语言业务目标、执行记录、证据、数据概况 |
| **Autonomous Control** | Goal parsing、dynamic decision、Replan、预算、完成条件 |
| **Tool Plane** | 风险、成本、schema、side-effect contract 与真实 handlers |
| **Evolution Lab** | 多候选探索、elite 变异、holdout、full regression、robustness gate |
| **Memory Plane** | Episodic / procedural / policy memory、召回、容量控制 |
| **Trust Plane** | 用户约束、独立 verifier、activation gate、drift detection、rollback |
| **Durability Plane** | Checkpoint、rehydration、adaptive idempotency、持久化 run |
| **Owned Ranking Core** | 项目自有搜索、推荐、评估与 counterfactual simulation |

---

## Built-in capabilities

### Search

- 中英文多粒度文本处理；
- 稀有具体词证据优先的候选获取；
- 字段感知匹配；
- 稳定哈希语义信号，仅用于有真实词项证据的候选重排；
- 标题、质量、热度、新鲜度等排序信号；
- 一屏结果多样性；
- prepared feature reuse，使多候选进化不重复计算相同特征；
- Recall / MRR / NDCG 离线复核。

### Recommendation

- 隐式反馈与时间衰减；
- 用户内容偏好画像；
- 有界历史的 item-item 共现图；
- 类目兴趣、质量、新鲜度、热度、新颖度；
- 稳定探索与已看过滤；
- 一屏多样性优化；
- prepared user features 与共享不可变图，支持快速多策略评估；
- Coverage / Diversity / Freshness / Novelty 离线复核。

### Current tool registry

| Tool | Risk | Purpose |
|---|---|---|
| `data.inspect` | `read` | 检查数据、记忆、已激活策略和回滚事件 |
| `search.run` | `read` | 真实复现搜索体验 |
| `search.diagnose` | `read` | 定位查询证据和候选覆盖问题 |
| `search.audit` | `simulation` | 在可复核查询上检查整体稳定性 |
| `search.evolve` | `adaptive` | 自主生成、验证并按权限学习/激活搜索策略 |
| `recommend.run` | `read` | 真实生成指定用户的一屏推荐 |
| `recommend.diagnose` | `read` | 检查冷启动、历史证据和候选池 |
| `recommend.audit` | `simulation` | 检查覆盖、新鲜度和分散度 |
| `recommend.evolve` | `adaptive` | 自主生成、验证并按权限学习/激活推荐策略 |

---

## Safety model

`adaptive` 不等于“可随意修改”。

Recsys Harness 默认把权限分成：

- **read** — 只读取和复现；
- **simulation** — 离线评估，不改变当前策略；
- **adaptive** — 可以写入内部策略记忆；只有目标明确授权时才允许激活。

安全链路包括：用户硬约束 → 工具权限检查 → evaluation readiness → holdout → full regression → robustness → trusted promotion → activation permission → future drift check → automatic rollback。

当前项目**没有直接发布到生产流量的工具**。内部策略激活和真实线上发布是两件不同的事。

---

## Quick start

```bash
git clone https://github.com/jiaweine/recsys-harness.git
cd recsys-harness
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make run
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
make run
```

Open `http://127.0.0.1:8765`.

CLI / smoke run:

```bash
make demo
lingjing-harness '检查用户 u-lin 的推荐体验，自动优化并持续学习'
```

Docker:

```bash
docker build -t recsys-harness .
docker run --rm -p 8765:8765 recsys-harness
```

---

## Bring your own data

页面和 API 支持导入 UTF-8 JSON，文件上限 **8 MB**。

```json
{
  "items": [
    {
      "id": "p01",
      "title": "示例内容",
      "text": "内容描述",
      "categories": ["户外"],
      "popularity": 120,
      "quality": 0.92,
      "freshness": 0.83
    }
  ],
  "interactions": [
    {"user_id": "u01", "item_id": "p01", "event": "click", "timestamp": 12}
  ],
  "query_labels": [
    {"query": "露营灯", "relevant": ["p01"]}
  ]
}
```

完整字段见 [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)。导入后的 Catalog 也会持久化，服务重启不会静默退回样例数据。

---

## API surface

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/status` | 数据、记忆与自主 Runtime 状态 |
| `GET` | `/api/capabilities` | Tool manifest 与 autonomy capability discovery |
| `GET` | `/api/conversations` | 会话列表 |
| `POST` | `/api/conversations` | 创建体验任务 |
| `GET` | `/api/conversations/{id}` | 获取单个任务 |
| `POST` | `/api/conversations/{id}/messages` | 提交目标并启动自主执行 |
| `GET` | `/api/runs/{run_id}` | 获取 checkpointed run 的进度、事件与结果 |
| `POST` | `/api/data/import` | 导入 JSON payload |
| `POST` | `/api/data/import-file` | 上传 JSON 数据文件 |

---

## Repository map

```text
recsys-harness/
├── lingjing_harness/
│   ├── algorithms/
│   │   ├── search.py          # owned search + prepared features
│   │   ├── recommend.py       # owned recommender + shared graph/features
│   │   ├── evaluation.py      # offline evaluation
│   │   ├── evolution.py       # multi-candidate eval-gated evolution
│   │   └── text.py
│   ├── runtime/
│   │   ├── harness.py         # autonomous loop + checkpoint rehydration
│   │   ├── policy.py          # dynamic decision / replan policy
│   │   ├── memory.py          # episodic + skill + policy memory
│   │   ├── tools.py           # risk/cost/schema tool contracts
│   │   ├── verifier.py        # independent final/evolution gate
│   │   └── contracts.py
│   ├── api.py                 # async API + durable run recovery
│   ├── store.py               # conversations + persistent runs
│   └── domain.py
├── frontend/                  # customer workbench
├── tests/                     # runtime / algorithm / API regression suite
├── docs/
├── Dockerfile
└── pyproject.toml
```

---

## Quality gates

```bash
make check
make test
make demo
```

CI additionally validates:

- Python compile;
- complete automated test suite;
- CLI execution;
- JavaScript syntax;
- wheel build + clean install;
- installed wheel can still serve the real Web product;
- real Chromium product run used to refresh README screenshots.

The repository also contains regression coverage for dynamic Replan, cold-start diagnosis, adaptation permission, self-evolution, automatic rollback, checkpoint resume and adaptive-tool idempotency.

---

## Design principles

1. **Business goal first** — 用户提供目标，不需要先学内部算法。
2. **Replan from evidence** — 新观察可以改变下一步动作。
3. **Learn only behind evals** — 没有独立验证，就没有策略晋升。
4. **Permission before activation** — 自主学习不等于未经授权改变当前策略。
5. **Rollback is part of learning** — 会晋升，也必须会退役和恢复。
6. **Memory with forgetting** — 经验要召回，也要控制容量和失效。
7. **Durability over optimism** — checkpoint、恢复与幂等是 Runtime 本身的责任。
8. **Owned ranking core** — 搜索推荐核心能力由项目自身掌握。
9. **External model optionality** — 外部模型可以增强交互，但不是核心排序或 Harness 运行的必要依赖。
10. **One clean product line** — 只维护一套主路径，不通过复制代码制造“新版”。

---

## Documentation

| Document | Contents |
|---|---|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 自主控制、记忆、自进化、可靠性和搜推核心 |
| [`DESIGN.md`](docs/DESIGN.md) | 客户工作台设计系统与交互原则 |
| [`DATA_FORMAT.md`](docs/DATA_FORMAT.md) | 数据字段与导入约束 |
| [`ACCEPTANCE.md`](docs/ACCEPTANCE.md) | 产品与工程验收标准 |

---

<div align="center">

### Search and recommendation are the capabilities.  
### Autonomous, verifiable evolution is the harness.

<sub>Decide. Act. Verify. Learn. Recover.</sub>

</div>
