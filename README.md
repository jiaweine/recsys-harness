<div align="center">

<sub>RECSYS HARNESS</sub>

# Search & recommendation, operated like an agent system.

**把“调算法、跑脚本、看指标、做复盘”收敛成一个可以直接交付目标的搜推执行工作台。**

Goal in · Plan with owned policy · Execute real tools · Verify with evidence

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-1f6f5c?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Agent%20Runtime-1f6f5c?logo=fastapi&logoColor=white)
![Core](https://img.shields.io/badge/Core-No%20external%20LLM%20required-c8f06a?labelColor=16231b)

[Product](#product) · [System map](#system-map) · [Quick start](#quick-start) · [Data](#bring-your-own-data) · [API](#api-surface) · [Docs](#documentation)

</div>

<p align="center">
  <img src="docs/readme-assets/product-run.png" alt="Recsys Harness running a real search experience review with execution trace" width="100%" />
</p>

<p align="center"><sub>真实产品截图 · 画面来自当前前端 DOM/CSS，内容来自一次实际执行的本地“露营灯”搜索复核。</sub></p>

---

## Product

**Recsys Harness** 是一个面向搜索与推荐系统的垂直 **Agent Harness**。

它不是“聊天框 + 指标看板”，也不是只返回 ranked list 的算法 Demo。用户描述业务目标后，Harness 会把目标转换成执行计划，调用项目自有的搜索、推荐、评估与实验能力，检查执行结果，再把**结论、证据和下一步动作**留在同一会话中。

<table>
<tr>
<td width="33%" valign="top">
<strong>01 · Goal-first</strong><br><br>
用户说“哪里体验不好、想验证什么”，而不是先决定脚本、接口和参数。
</td>
<td width="33%" valign="top">
<strong>02 · Owned core</strong><br><br>
搜索、推荐、评估、候选比较和任务路由都由项目自身实现；核心路径不依赖外部 LLM。
</td>
<td width="33%" valign="top">
<strong>03 · Evidence-first</strong><br><br>
系统只有在真实工具执行和验证器检查之后才形成结论，未验证的推测不会伪装成结果。
</td>
</tr>
</table>

### The product surface

产品界面采用“体验工作台 / 运营控制室”的结构，而不是把内部算法词汇直接暴露给业务用户。

<p align="center">
  <img src="docs/readme-assets/product-workbench.png" alt="Recsys Harness product workbench" width="49%" />
  <img src="docs/readme-assets/product-evidence.png" alt="Recsys Harness evidence panel after a completed search review" width="49%" />
</p>

<p align="center"><sub>左：目标驱动的工作台入口 · 右：真实执行完成后的判断依据视图</sub></p>

客户界面使用的是 **搜索体验 / 推荐体验 / 方案复核 / 全局体检 / 执行记录 / 判断依据 / 当前数据**。算法实现细节保留在工程层，不把系统复杂度转嫁给用户。

---

## What a real run looks like

给它一句目标：

> 最近搜索“露营灯”的结果不太准，帮我复现问题并给一个改进方案，但先不要上线。

Harness 实际会完成这些动作：

1. **读取当前工作区**，确认内容、用户反馈和可复核样本；
2. **拆解任务**，决定先复现单点，再检查整体稳定性；
3. **真实运行搜索**，保留当前排序结果；
4. **执行离线复核**，检查已标注查询的整体表现；
5. **运行候选方案**，在相同数据上进行 Shadow Compare；
6. **经过 ResultVerifier**，拒绝空结果、重复结果、异常输出或明显回退；
7. **形成结论与证据**，并给出可以继续执行的下一步。

当前内置样例的一次真实运行中，“露营灯”任务会留下完整的事件轨迹、结果证据和候选方案判断，而不是只输出一段解释文本。

---

## System map

<p align="center">
  <img src="docs/readme-assets/system-map.svg" alt="Detailed architecture map of Recsys Harness" width="100%" />
</p>

这张图对应的是当前代码中的真实边界，不是概念架构：

| Plane | Current responsibility |
|---|---|
| **Experience Plane** | 自然语言目标、任务场景、执行记录、判断依据、数据概况 |
| **Agent Runtime / Control Plane** | `OwnedPolicy`、计划生成、工具调用、事件流、验证、完成条件 |
| **Trust Plane** | 工具风险分级、模拟优先、`safe_to_try` 门槛、无自动上线工具 |
| **Data Plane** | Catalog、用户行为、查询标注、SQLite 会话与运行结果 |
| **Tool Plane** | `data.inspect`、search / recommend 的 run、audit、compare 工具契约 |
| **Owned Search + Recommendation Core** | 搜索排序、个性化推荐、离线评估、候选实验 |

### Runtime lifecycle

运行时保持一条明确的主路径：

| Observe | Plan | Execute | Verify | Complete |
|---|---|---|---|---|
| 读取工作区和任务约束 | OwnedPolicy 识别任务并生成步骤 | Tool Registry 调用真实 handler | ResultVerifier 检查结果与候选风险 | 汇总 evidence、answer、suggestions |

这也是项目把 **Harness** 放在产品中心的原因：算法负责产生能力，Harness 负责把能力组织成可靠执行。

---

## Built-in capabilities

### Search engine

当前搜索路径由项目自身实现，组合了：

- 中英文多粒度文本处理；
- 字段感知的词项匹配；
- 稳定哈希语义表征；
- 标题、质量、热度、新鲜度等排序信号；
- 一屏结果多样性控制；
- 已标注查询的离线质量复核。

### Recommendation engine

当前推荐路径组合了：

- 隐式反馈与时间衰减；
- 用户内容偏好画像；
- item-item 共现关系；
- 类目兴趣；
- 质量、新鲜度、热度和新颖度；
- 稳定探索；
- 一屏结果多样性优化；
- 已看内容过滤。

### Harness runtime

真正把算法能力变成产品的是外层执行系统：

- 持续会话；
- Goal planning；
- Typed tool contracts；
- Runtime event trace；
- 工具风险分级；
- Shadow Compare；
- Result verification；
- Evidence-backed answer；
- 可继续追问的客户工作台。

<details>
<summary><strong>Current tool registry</strong></summary>
<br>

| Tool | Risk | Purpose |
|---|---|---|
| `data.inspect` | `read` | 检查当前工作区是否足够支撑判断 |
| `search.run` | `read` | 运行指定搜索并保留排序证据 |
| `search.audit` | `simulation` | 在已标注查询上做离线复核 |
| `search.compare` | `simulation` | 比较当前搜索方案和候选方案 |
| `recommend.run` | `read` | 为指定用户生成推荐序列 |
| `recommend.audit` | `simulation` | 检查推荐覆盖、多样性、新鲜度等表现 |
| `recommend.compare` | `simulation` | 离线比较当前推荐方案和候选方案 |

</details>

---

## Safe by design

当前内置工具只有 **`read`** 和 **`simulation`** 两类风险级别。

候选策略不会直接覆盖当前搜索 / 推荐逻辑。比较过程在同一份数据上隔离运行，验证器会结合当前结果和候选结果决定是否标记为 `safe_to_try`；如果候选没有稳定优势或出现明显问题，就不会建议进一步放量。

**当前项目默认没有自动上线工具。** 这是刻意的产品边界，而不是缺失的按钮：先让 Agent 学会可靠诊断、比较和验证，再逐步扩展真实变更权限。

---

## Quick start

### 1. Clone & install

```bash
git clone https://github.com/jiaweine/recsys-harness.git
cd recsys-harness

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Start the product

```bash
make run
```

Or run the backend directly:

```bash
python -m uvicorn lingjing_harness.api:app --host 0.0.0.0 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

FastAPI docs:

```text
http://127.0.0.1:8765/docs
```

### 3. Run a complete Harness task from CLI

```bash
make demo
```

Or:

```bash
python -m lingjing_harness.cli '最近搜索“露营灯”的结果不准，帮我优化，但先不要上线'
```

### Docker

```bash
docker build -t recsys-harness .
docker run --rm -p 8765:8765 recsys-harness
```

---

## Bring your own data

页面和 API 都支持导入 UTF-8 JSON。当前文件上传上限为 **8 MB**。

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
    {
      "user_id": "u01",
      "item_id": "p01",
      "event": "click",
      "timestamp": 12
    }
  ],
  "query_labels": [
    {
      "query": "露营灯",
      "relevant": ["p01"]
    }
  ]
}
```

完整字段说明见 [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)。

---

## API surface

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/status` | 当前运行状态与数据概况 |
| `GET` | `/api/conversations` | 会话列表 |
| `POST` | `/api/conversations` | 创建体验任务 |
| `GET` | `/api/conversations/{id}` | 获取单个任务 |
| `POST` | `/api/conversations/{id}/messages` | 提交目标并启动 Harness |
| `GET` | `/api/runs/{run_id}` | 获取执行进度、事件与结果 |
| `POST` | `/api/data/import` | 导入 JSON payload |
| `POST` | `/api/data/import-file` | 上传 JSON 数据文件 |

任务执行采用异步 run + polling，前端持续展示阶段变化、执行记录和结果证据。

---

## Repository map

```text
recsys-harness/
├── lingjing_harness/
│   ├── algorithms/
│   │   ├── search.py          # Search engine
│   │   ├── recommend.py       # Recommendation engine
│   │   ├── evaluation.py      # Offline evaluation
│   │   ├── experiment.py      # Shadow comparison
│   │   └── text.py            # Text representation
│   ├── runtime/
│   │   ├── policy.py          # Goal → execution plan
│   │   ├── tools.py           # Tool registry
│   │   ├── verifier.py        # Result verification
│   │   └── harness.py         # Agent execution loop
│   ├── api.py                 # FastAPI backend
│   ├── cli.py                 # CLI entry point
│   ├── domain.py              # Data contracts
│   ├── sample_data.py         # Runnable sample workspace
│   └── store.py               # SQLite session persistence
├── frontend/
│   ├── index.html             # Customer workspace
│   ├── app.css
│   └── app.js
├── docs/
│   ├── readme-assets/         # Real product screenshots + system map
│   ├── ARCHITECTURE.md
│   ├── DESIGN.md
│   ├── DATA_FORMAT.md
│   └── ACCEPTANCE.md
├── tests/
├── scripts/
├── Dockerfile
├── Makefile
└── pyproject.toml
```

---

## Quality

```bash
make check
make test
```

当前自动化检查覆盖：

- Python 全量编译；
- 前端 JavaScript 语法；
- 搜索关键查询结果；
- 推荐已看过滤与结果分散度；
- 搜索 / 推荐离线质量检查；
- Harness 路由与工具执行；
- 候选方案比较与安全门槛；
- 结果证据输出；
- SQLite 会话持久化；
- API 基础契约与错误数据拦截。

GitHub Actions CI 位于 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)。

---

## Design principles

1. **Business goals first** — 用户表达业务问题，不要求先懂算法。
2. **Execution over narration** — 没有真实执行过的动作，不写成“已验证”。
3. **Evidence over confidence** — 结论必须能回到本次执行结果。
4. **Simulation before mutation** — 候选方案先隔离比较，不直接改变当前策略。
5. **Owned ranking core** — 搜索与推荐核心策略掌握在项目自身。
6. **LLM optionality** — 语言模型可以增强交互，但不是核心排序的单点依赖。
7. **One clean path** — 保持一套清晰、可运行、可扩展的主路径，不堆积重复实现。

---

## Documentation

| Document | Contents |
|---|---|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Harness、工具、搜推内核、验证器与 Shadow Compare |
| [`DESIGN.md`](docs/DESIGN.md) | 客户工作台的设计系统与交互原则 |
| [`DATA_FORMAT.md`](docs/DATA_FORMAT.md) | 数据字段、导入格式与约束 |
| [`ACCEPTANCE.md`](docs/ACCEPTANCE.md) | 工程验收与检查标准 |

---

## Direction

Recsys Harness 的长期目标不是成为另一个“算法后台”，而是成为搜索与推荐系统上方的一层 **agentic operating layer**。

未来可以继续接入更真实的数据源、实验平台、流量控制、人工审批和线上观测；上层 Runtime、工具契约、验证边界和证据机制仍然保持稳定。

<div align="center">

### Search and recommendation are the capabilities.  
### The harness is the product.

<sub>Goal in. Evidence out.</sub>

</div>
