<div align="center">

# Recsys Harness

### 自主运行搜索与推荐系统的 Agent Harness

**把搜推工程从“脚本、指标和人工经验”变成一个会观察、决策、执行、验证、恢复和学习的闭环。**

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

**真实工具 · 动态决策 · 证据门控学习 · 多模态上下文 · 可控联网 · 可恢复执行**

[真实产品](#真实产品) · [它解决什么](#它解决什么) · [一次任务如何运行](#一次任务如何运行) · [快速启动](#快速启动) · [核心能力](#核心能力) · [可靠性](#可靠性) · [系统架构](#系统架构) · [部署](#部署)

</div>

---

## 真实产品

<p align="center"><sub>DESKTOP · FULL WORKSPACE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@0128d0654fd39b32be146108d070b495e5d81983/docs/readme-assets/overview.png" alt="Recsys Harness 真实运行界面" width="96%">
</p>
<p align="center">
  <sub>一个任务面：对话、运行状态、证据、附件和可恢复执行保持在同一上下文。</sub>
</p>

> **Real product, real browser.** 截图来自仓库实际启动的应用。CI 会用真实浏览器执行产品任务，并在视觉 QA 通过后刷新 README 资产。

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <strong>任务工作台</strong><br>
      <sub>输入、附件、权限和执行入口保持在同一操作面。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@0128d0654fd39b32be146108d070b495e5d81983/docs/readme-assets/workbench.png" alt="Recsys Harness 工作台" width="100%">
    </td>
    <td width="50%" valign="top" align="center">
      <strong>证据与判断</strong><br>
      <sub>轨迹、依据和结论集中在独立检查视图。</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@0128d0654fd39b32be146108d070b495e5d81983/docs/readme-assets/evidence.png" alt="Recsys Harness 证据面板" width="100%">
    </td>
  </tr>
</table>

### Mobile · task first

移动端不复刻桌面三栏，也不把证据面板粗暴铺满整屏。它遵循三个原则：

<table>
  <tr>
    <td width="33%" valign="top"><strong>01 · One primary flow</strong><br><sub>主任务始终是第一层；导航与状态为内容让路。</sub></td>
    <td width="33%" valign="top"><strong>02 · Evidence as a sheet</strong><br><sub>轨迹与依据从底部进入，保留上方任务上下文。</sub></td>
    <td width="33%" valign="top"><strong>03 · Touch first</strong><br><sub>关键交互保持可触达尺寸，并考虑 safe-area。</sub></td>
  </tr>
</table>

<p align="center">
  <sub>MOBILE · THREE REAL STATES</sub><br>
  <strong>同一个真实任务的主工作区、执行轨迹与判断依据。</strong>
</p>

<table>
  <tr>
    <td width="38%" valign="top" align="center">
      <strong>01 · 主任务</strong><br>
      <sub>对话、结论与执行入口</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@0128d0654fd39b32be146108d070b495e5d81983/docs/readme-assets/mobile-workspace.png" alt="Recsys Harness 移动端主任务" width="94%">
    </td>
    <td width="31%" valign="top" align="center">
      <strong>02 · 执行轨迹</strong><br>
      <sub>bottom sheet · 任务状态与真实动作</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@0128d0654fd39b32be146108d070b495e5d81983/docs/readme-assets/mobile-progress.png" alt="Recsys Harness 移动端执行轨迹" width="92%">
    </td>
    <td width="31%" valign="top" align="center">
      <strong>03 · 判断依据</strong><br>
      <sub>bottom sheet · 可复核证据与来源</sub><br><br>
      <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@0128d0654fd39b32be146108d070b495e5d81983/docs/readme-assets/mobile-evidence.png" alt="Recsys Harness 移动端判断依据" width="92%">
    </td>
  </tr>
</table>

产品界面只呈现业务语言：搜索体验、推荐体验、自主优化、全局体检、执行轨迹、判断依据、图片感知和联网研究。内部算法与可选后端不会暴露在客户界面。

---

## 它解决什么

搜索和推荐系统的问题通常不是“算一个分数”，而是一个持续产生新证据、持续做取舍的工程闭环。

> **一句话理解：** Recsys Harness 把问题复现、证据补全、候选探索、独立验证、学习决策和全过程留痕统一成一个**有状态、有证据、有边界**的 agent run。

当业务方说：

> “最近搜索『露营灯』的结果不太准，帮我看看。”

真正的工作往往横跨数据、候选、排序、冷启动、全局质量、回归验证和策略治理。区别在于：

| 常规处理 | Recsys Harness |
|---|---|
| 人工决定下一步查什么 | 每轮根据新 observation 重新选择动作 |
| 脚本、指标和结论散落在不同地方 | 工具轨迹、证据、结论和成本统一保存 |
| 调完参数看一个平均指标 | 候选必须经过独立验证与全量回归 |
| 中断后重新开始 | checkpoint + durable recovery |
| 经验依赖个人记忆 | 有界的 episodic / procedural / policy memory |

**它不是“聊天框 + 一次算法调用”，也不是把固定流水线换一个 Agent 名字。**  
搜索和推荐是能力，Harness 才是产品主体。

---

## 一次任务如何运行

用户只需要描述目标和边界：

```text
“露营灯”的搜索结果不准，先检查，不要改当前策略。
```

<table>
  <tr>
    <td width="25%" valign="top"><strong>01 · Observe</strong><br><sub>复现真实结果，读取工作区、附件和历史状态。</sub></td>
    <td width="25%" valign="top"><strong>02 · Decide</strong><br><sub>根据证据缺口、信息增益、风险和预算选择下一步。</sub></td>
    <td width="25%" valign="top"><strong>03 · Execute & Verify</strong><br><sub>运行真实工具，并用独立样本与全量回归验证。</sub></td>
    <td width="25%" valign="top"><strong>04 · Learn / Stop</strong><br><sub>记录可信经验、受控激活，或在证据不足时停止。</sub></td>
  </tr>
</table>

真正关键的是：**路径不是预先写死的。**

每一次工具返回新 observation 后，控制器都会重新判断下一步。复现正常，就不会为了“流程完整”机械诊断；证据出现冲突、冷启动或覆盖不足，后续动作会重新规划。

> **Control loop:** Observe → Decide → Execute → Checkpoint → Replan → Verify → Learn / Stop

---

## 快速启动

**要求：Python 3.11+。核心能力默认本地运行，可选外部模型与联网能力。**

**1 · Clone & create environment**

```bash
git clone https://github.com/jiaweine/recsys-harness.git
cd recsys-harness
python -m venv .venv
```

**2 · Install & run**

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

**3 · Open**

```text
http://127.0.0.1:8765
```

CLI：

```bash
lingjing-harness "做一次全局体检"
```

开发与测试：

```bash
python -m pip install -e ".[dev]"
pytest -q
```

> `make run` · `make test` · `make check` · `make demo` 提供常用开发入口。

---

## 核心能力

<table>
  <tr>
    <td width="33%" valign="top"><strong>01 · Autonomous Control</strong><br><sub>每次 observation 后重新决策，不依赖一次性 Plan。</sub></td>
    <td width="33%" valign="top"><strong>02 · Real Search & RecSys</strong><br><sub>搜索、推荐、评估都是真实工具，不是占位函数。</sub></td>
    <td width="33%" valign="top"><strong>03 · Evidence-gated Learning</strong><br><sub>候选策略必须经过独立验证与回归门槛。</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top"><strong>04 · Long-term Memory</strong><br><sub>任务、策略与动作收益分别进入有界记忆。</sub></td>
    <td width="33%" valign="top"><strong>05 · Multimodal Context</strong><br><sub>文本、数据文件与图片统一进入受限 observation。</sub></td>
    <td width="33%" valign="top"><strong>06 · Controlled Network</strong><br><sub>联网显式授权，外部 evidence 不直接晋升本地策略。</sub></td>
  </tr>
</table>

<details open>
<summary><strong>01 · 自主决策，而不是一次性 Plan</strong></summary>

项目自己的决策控制器会在**每一次工具返回新 observation 后重新选择下一步**。动作效用综合：

- 当前证据缺口与预期信息增益；
- 异常与冲突信号；
- 工具成本和剩余预算；
- 相似历史任务中的真实收益；
- 用户授予的联网与策略调整权限。

因此 Agent 可以继续、插入诊断、切换检查路径、停止，或者进入受控的策略探索。

</details>

<details>
<summary><strong>02 · 搜索与推荐是真实工具</strong></summary>

| 搜索 | 推荐 |
|---|---|
| 查询复现与诊断 | 隐式反馈与时间衰减 |
| 字段感知匹配与排序 | 用户内容偏好 |
| 质量 / 热度 / 新鲜度信号 | 有界 item-item 共现 |
| 结果多样性 | 质量 / 新鲜度 / 热度 / 新颖度 |
| Recall / MRR / NDCG 离线复核 | Coverage / Diversity / Freshness / Novelty 复核 |

</details>

<details>
<summary><strong>03 · 证据门控学习</strong></summary>

这里的“学习”不是 Agent 任意修改自己的源代码，而是让候选策略逐级通过验证门槛：

| Gate | 必须回答的问题 |
|---|---|
| **Discovery competition** | 多个候选里是否真的存在更优方向 |
| **Independent holdout** | 优势能否在独立样本上成立 |
| **Full regression** | 局部收益是否伤害整体质量 |
| **Robustness gate** | 改进是否足够稳定，而不是偶然波动 |
| **Trusted strategy memory** | 是否有资格进入可信策略记忆 |

没有独立 holdout，只允许探索，**不能变成 trusted / active 策略**。即使候选通过验证，是否激活仍受用户权限控制；未来验证出现明显漂移时，可以 retired 并回到稳健配置。

</details>

<details>
<summary><strong>04 · 真正的长期记忆</strong></summary>

| Memory | 保存什么 |
|---|---|
| **Episodic** | 过去类似任务发生了什么 |
| **Procedural** | 通过证据门槛的搜索 / 推荐策略 |
| **Policy** | 某类任务中不同动作过去带来的收益 |

记忆不是无限堆积。系统保留近期经验和高价值经验，并淘汰低价值旧记录。

</details>

<details>
<summary><strong>05 · 多模态输入</strong></summary>

同一个任务可以带自然语言、截图、PNG / JPEG / WebP / GIF、JSON / CSV / Markdown / TXT；最多 8 个附件，单文件上限 12MB。

文本和数据文件直接本地解析。图片可以交给可选的本地视觉感知接口转成受限 observation。

> **Permission boundary:** 感知层不是 Agent 大脑。图片中的文字不能扩大联网权限、不能批准策略激活，也不能绕过工具 guardrail。

</details>

<details>
<summary><strong>06 · 可控联网</strong></summary>

联网是显式能力，不是后台默认行为。只有当用户在任务里授权联网，并且服务端配置了搜索端点，网络研究工具才会进入本次可用工具集合。

网络结果会保留来源和摘要，作为当前判断的 evidence；**公开网页证据不会直接成为搜索 / 推荐策略晋升数据**。

```bash
export LINGJING_WEB_SEARCH_URL=<your-search-endpoint>
export LINGJING_WEB_SEARCH_KEY=<optional-key>
```

可选图片感知：

```bash
export LINGJING_VISION_BASE_URL=<your-vision-endpoint>
export LINGJING_VISION_MODEL=<your-model-id>
```

核心搜索、推荐、决策、验证和记忆不依赖这些可选服务。

</details>

---

## 可靠性

自主执行的价值，来自“能做事”；生产可用的前提，是“知道哪里不能越界”。

<table>
  <tr>
    <td width="25%" valign="top"><strong>Guardrails</strong><br><sub>工具声明风险、成本、副作用与可重复性。</sub></td>
    <td width="25%" valign="top"><strong>Durability</strong><br><sub>动作、证据、决策与成本持续 checkpoint。</sub></td>
    <td width="25%" valign="top"><strong>Fencing</strong><br><sub>lease + heartbeat 阻止 stale worker 覆盖新状态。</sub></td>
    <td width="25%" valign="top"><strong>Coherence</strong><br><sub>workspace revision 保证跨 worker 数据一致。</sub></td>
  </tr>
</table>

<details open>
<summary><strong>Tool guardrails</strong></summary>

```text
risk · cost · side_effect · repeatable · input_schema
```

| 风险类型 | 含义 |
|---|---|
| `read` | 读取或真实复现，不修改策略 |
| `simulation` | 离线评估 |
| `adaptive` | 可以写策略记忆；激活仍需要授权 |
| `network` | 外部请求；本次任务必须允许联网 |

</details>

<details>
<summary><strong>Durable execution</strong></summary>

每个 run 持续保存：

```text
actions · observations · findings · evidence · decisions · cost · events
```

服务重启后可以从 checkpoint 恢复。Adaptive action 使用稳定 invocation id，避免“已经写入策略记忆、但 checkpoint 还没保存”时把一次学习重复执行多次。

</details>

<details>
<summary><strong>Multi-worker fencing</strong></summary>

SQLite 同时承担共享运行协调：conversation 级原子 run reservation、worker owner + lease + heartbeat、stale worker fencing，以及 completed / failed / cancelled 单调终态。

同一个 conversation 同时只允许一个 active run；不同 conversation 可以并行。`cancel_requested` 在重启后安全收敛，迟到 worker 不能覆盖新 owner 的 checkpoint。

</details>

<details>
<summary><strong>Workspace coherence</strong></summary>

工作区 Catalog 有共享 revision 和 update lock。数据导入期间不接受新的 run；revision 提交后，其他 worker 会重新加载同一 Catalog / Harness，避免跨 worker 使用不同版本数据。

</details>

---

## 系统架构

<p align="center"><sub>CONTROL · EVIDENCE · TRUST · STATE</sub></p>
<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@0128d0654fd39b32be146108d070b495e5d81983/docs/readme-assets/architecture.svg" alt="Recsys Harness system architecture" width="97%">
</p>

架构图刻意把系统分成三个面，而不是把组件堆成一张依赖关系图：

<table>
  <tr>
    <td width="33%" valign="top">
      <strong>Control plane</strong><br>
      <sub>目标、observation、动作效用、预算、风险和 Replan 在项目自有控制核中闭环。</sub>
    </td>
    <td width="33%" valign="top">
      <strong>Evidence plane</strong><br>
      <sub>真实搜推工具、诊断结果、附件感知与可选网络研究只提供可追溯 evidence。</sub>
    </td>
    <td width="33%" valign="top">
      <strong>Trust & state plane</strong><br>
      <sub>holdout、回归、memory、checkpoint、lease、workspace revision 与 access 共同约束长期行为。</sub>
    </td>
  </tr>
</table>

**Architecture invariants**

`attachments never grant permission` · `network evidence never promotes strategy` · `holdout precedes trust` · `stale workers cannot overwrite current state`

> 完整设计说明：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

---

## 部署

<table>
  <tr>
    <td width="50%" valign="top"><strong>Local development</strong><br><sub>默认本地模式不要求登录，适合单机开发、调试和体验。</sub></td>
    <td width="50%" valign="top"><strong>Production exposure</strong><br><sub>生产模式必须显式配置访问密钥，并启用服务端安全边界。</sub></td>
  </tr>
</table>

<details open>
<summary><strong>生产模式配置</strong></summary>

```bash
export LINGJING_ENV=production
export LINGJING_ACCESS_TOKEN='<a-long-random-secret>'
python -m uvicorn lingjing_harness.api:app --host 0.0.0.0 --port 8765
```

生产模式下：

- 少于 16 个字符的访问密钥会拒绝启动；
- Web UI 使用签名 HttpOnly + SameSite 会话；
- 密钥不写入 URL 或 localStorage；
- 登录、上传、任务提交和数据导入共享 SQLite 限流；
- 默认不信任转发 IP 头；
- 返回 CSP、frame deny、nosniff 等安全响应头。

如果部署在可信反向代理后并确实需要读取转发 IP：

```bash
export LINGJING_TRUST_PROXY_IP=1
```

</details>

> **部署边界：** 当前多 worker 设计假设 worker 共享同一 SQLite 与数据目录。真正的多机 / 多区域部署应把协调存储和对象存储迁移到共享基础设施，而不是复制本地 SQLite 文件。

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
```

<details>
<summary><strong>CI 实际覆盖什么？</strong></summary>

- Python 编译与完整回归测试；
- CLI smoke；
- wheel 构建、干净安装与安装后 Web / API 能力；
- 前端 JavaScript 语法；
- UI 不泄漏内部算法或第三方产品名；
- 真实浏览器桌面 / 移动流程与附件交互；
- 移动证据面板必须保持 bottom-sheet 结构与任务上下文；
- 瞬时 polling 故障后的自动重连；
- 关键移动触控目标；
- 浏览器 page / console error；
- 多 worker lease / fencing / workspace revision；
- 生产访问与共享限流契约。

</details>

---

## Repository map

```text
frontend/                         产品 UI
lingjing_harness/
  algorithms/                     搜索、推荐、评估、策略探索
  runtime/
    harness.py                    自主执行循环
    policy.py                     项目自有决策控制器
    tools.py                      能力注册与风险 guardrail
    perception.py                 多模态 observation
    network.py                    可控网络 evidence
    memory.py                     长期 Agent memory
    verifier.py                   独立结果验证
  api.py                          API、认证、附件、恢复、工作区运行时
  store.py                        durable run、lease、revision、共享限流
tests/                            回归与 resilience 测试
docs/                             架构、设计、数据与验收说明
scripts/capture_readme_assets.py  真实浏览器 QA 与 README 截图
```

> 仓库只维护**一条主线实现**，不保留编号产品副本或平行历史源码树。

---

<div align="center">

### Search and recommendation are the capabilities. The harness is the product.

**Observe · Decide · Execute · Verify · Learn · Recover**

<sub>真实工具 · 动态决策 · 可验证学习 · 多模态上下文 · 可控联网 · 可恢复执行</sub>

</div>
