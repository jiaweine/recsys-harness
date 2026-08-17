<div align="center">

# Recsys Harness

### 自主运行搜索与推荐系统的 Agent Harness

把一个搜推问题交给它。它会观察工作区、选择工具、执行真实检索或推荐、验证结果、保留证据，并在满足门槛时学习可复用策略。

**项目自有决策核 · 多模态输入 · 可控联网 · 证据门控学习 · 可恢复执行**

[CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml) · Python 3.11+ · Local-first · External model optional

[快速启动](#快速启动) · [它解决什么](#它解决什么) · [一次任务如何运行](#一次任务如何运行) · [核心能力](#核心能力) · [系统架构](#系统架构) · [部署](#部署)

</div>

---

## 真实产品

![Recsys Harness 真实运行界面](/docs/readme-assets/overview.png)

> 上图不是设计稿。仓库会启动当前应用，用真实浏览器执行产品任务，并自动刷新 README 截图。

### 工作台

![Recsys Harness 工作台](/docs/readme-assets/workbench.png)

### 执行轨迹与判断依据

![Recsys Harness 证据面板](/docs/readme-assets/evidence.png)

### 移动端

![Recsys Harness 移动端](/docs/readme-assets/mobile.png)

产品界面只呈现业务语言：搜索体验、推荐体验、自主优化、全局体检、执行轨迹、判断依据、图片感知和联网研究。内部算法与可选后端不会暴露在客户界面。

---

## 它解决什么

搜索和推荐系统的问题通常不是“算一个分数”这么简单。

当业务方说：

> “最近搜索『露营灯』的结果不太准，帮我看看。”

真正的工作往往包含：

1. 复现真实结果；
2. 判断是数据、候选、排序还是冷启动问题；
3. 看局部问题是否已经影响整体质量；
4. 尝试多个改进方向；
5. 用独立样本和全量回归验证；
6. 决定只记录经验，还是允许激活新的策略；
7. 保存过程，方便恢复、审计和下一次复用。

**Recsys Harness 把这整段工作变成一个有状态、有证据、有边界的 agent run。**

| 传统处理 | Recsys Harness |
|---|---|
| 人工决定下一步查什么 | 每轮根据新证据重新选择动作 |
| 脚本、指标和结论分散 | 工具轨迹、证据、结论和成本统一保存 |
| 调参数后看一个平均指标 | 候选必须经过独立验证与全量回归 |
| 中断后从头再来 | checkpoint + durable recovery |
| 经验依赖个人记忆 | 有界的任务记忆与策略记忆 |

它不是“聊天框 + 一次算法调用”，也不是把固定流水线换一个 Agent 名字。

---

## 一次任务如何运行

用户只需要描述目标：

```text
“露营灯”的搜索结果不准，先检查，不要改当前策略。
```

Harness 可能实际走成：

```text
观察工作区
→ 复现真实搜索
→ 判断证据缺口
→ 必要时插入诊断
→ 做整体质量复核
→ 生成候选改进
→ 独立留出验证
→ 全量回归
→ 只记录可信经验，不激活
→ 输出结论与证据
```

关键不是这条路径本身，而是：**路径会变。**

如果复现结果正常，诊断不会为了“流程完整”机械执行；如果新的 observation 暴露出冷启动、覆盖不足、异常词或证据冲突，后续动作会重新规划。

核心循环：

```text
Observe → Decide → Execute → Checkpoint → Replan → Verify → Learn / Stop
```

---

## 快速启动

需要 Python 3.11+。

```bash
git clone https://github.com/jiaweine/recsys-harness.git
cd recsys-harness
python -m venv .venv
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -e .
python -m uvicorn lingjing_harness.api:app --host 127.0.0.1 --port 8765
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m uvicorn lingjing_harness.api:app --host 127.0.0.1 --port 8765
```

然后打开：

```text
http://127.0.0.1:8765
```

CLI 也可以直接运行：

```bash
lingjing-harness "做一次全局体检"
```

开发与测试：

```bash
python -m pip install -e ".[dev]"
pytest -q
```

仓库也提供 `make run`、`make test`、`make check`、`make demo` 作为便捷命令。

---

## 核心能力

### 1. 自主决策，而不是一次性 Plan

项目自己的决策控制器会在**每一次工具返回新 observation 后重新选择下一步**。

动作效用会综合：

- 当前证据缺口；
- 预期信息增益；
- 异常与冲突信号；
- 工具成本和剩余预算；
- 相似历史任务中的真实收益；
- 用户授予的联网与策略调整权限。

因此 Agent 可以继续、插入诊断、换一条检查路径、停止，或者进入受控的策略探索。

### 2. 搜索与推荐是真实工具，不是占位函数

当前 Harness 内置并直接运行：

**搜索**

- 查询复现与诊断；
- 字段感知匹配与排序；
- 质量 / 热度 / 新鲜度信号；
- 结果多样性；
- Recall / MRR / NDCG 离线复核。

**推荐**

- 隐式反馈与时间衰减；
- 用户内容偏好；
- 有界 item-item 共现；
- 质量 / 新鲜度 / 热度 / 新颖度；
- 已看过滤与结果多样性；
- Coverage / Diversity / Freshness / Novelty 复核。

搜索和推荐是能力，**Harness 才是产品主体**。

### 3. 证据门控学习

这里的学习不是 Agent 任意改自己的源代码。

它会在当前策略附近、历史可信经验和确定性扰动中生成多个候选，然后经过：

```text
候选探索
→ discovery competition
→ 独立 holdout
→ full regression
→ robustness gate
→ trusted strategy memory
```

没有独立 holdout，只允许探索，**不能变成 trusted / active 策略**。

即使候选通过验证，是否激活仍受用户权限控制。没有明确授权时，系统可以学习可信经验，但不会改变当前工作区策略。

后续如果 active strategy 出现明显漂移，系统会 retired 该策略并回到稳健配置。

### 4. 真正的长期记忆

长期状态分为三类：

- **Episodic memory**：过去类似任务发生了什么；
- **Procedural memory**：通过证据门槛的搜索 / 推荐策略；
- **Policy memory**：某类任务中不同动作过去带来的收益。

记忆不是无限堆积。系统保留近期经验和高价值经验，并淘汰低价值旧记录。

### 5. 多模态输入

同一个任务可以带：

- 自然语言；
- 拖入或粘贴的截图；
- PNG / JPEG / WebP / GIF；
- JSON / CSV / Markdown / TXT；
- 最多 8 个附件；
- 单文件 12MB 上限。

文本和数据文件直接本地解析。图片可以交给可选的本地视觉感知接口转成受限 observation。

**感知层不是 Agent 大脑。** 图片中的文字不能扩大联网权限、不能批准策略激活，也不能绕过工具 guardrail。

附件存储带总容量限制、未引用附件 TTL 回收和证据引用保护；感知阶段也响应停止信号和时间预算。

### 6. 可控联网

联网是显式能力，不是后台默认行为。

只有当用户在任务里授权联网，并且服务端配置了搜索端点，网络研究工具才会进入本次可用工具集合。

网络结果会保留来源和摘要，作为当前判断的 evidence；**公开网页证据不会直接成为搜索 / 推荐策略晋升数据**。策略改进仍需回到本地工作区经过独立验证。

通用配置：

```bash
export LINGJING_WEB_SEARCH_URL=<your-search-endpoint>
export LINGJING_WEB_SEARCH_KEY=<optional-key>
```

可选图片感知配置：

```bash
export LINGJING_VISION_BASE_URL=<your-vision-endpoint>
export LINGJING_VISION_MODEL=<your-model-id>
```

核心搜索、推荐、决策、验证和记忆不依赖这些可选服务。

---

## 可靠性

Agent 能自己行动，不代表可以没有边界。

### Tool guardrails

每个工具声明：

```text
risk · cost · side_effect · repeatable · input_schema
```

风险类型包括：

| 类型 | 含义 |
|---|---|
| `read` | 读取或真实复现，不修改策略 |
| `simulation` | 离线评估 |
| `adaptive` | 可以写策略记忆；激活仍需要授权 |
| `network` | 外部请求；本次任务必须允许联网 |

### Durable execution

每个 run 持续保存：

```text
actions · observations · findings · evidence · decisions · cost · events
```

服务重启后可以从 checkpoint 恢复。Adaptive action 使用稳定 invocation id，避免“已经写入策略记忆、但 checkpoint 还没保存”时把一次学习重复执行多次。

### Multi-worker fencing

SQLite 也承担共享运行协调：

- conversation 级原子 run reservation；
- worker owner + lease + heartbeat；
- lease 过期后只有一个新 worker接管；
- stale worker 的迟到 checkpoint 不能覆盖新 owner；
- **completed / failed / cancelled 是单调终态，落库后不能被迟到 worker 复活或覆盖**；
- stop 请求写入共享 run 状态，可以由不同 worker 接收；
- `cancel_requested` 重启后安全收敛，而不是留下孤儿任务。

同一个 conversation 同时只允许一个 active run；不同 conversation 可以并行。

### Workspace coherence

工作区 Catalog 有共享 revision 和 update lock。

数据导入期间不接受新的 run；revision 提交后，其他 worker 会重新加载相同 Catalog / Harness，避免一个 worker 用新数据、另一个 worker 继续接旧数据任务。

---

## 系统架构

![Recsys Harness system architecture](/docs/readme-assets/architecture.svg)

| 层 | 职责 |
|---|---|
| Experience | 文本、附件、联网授权、执行轨迹、证据 |
| Perception | 文档解析、可选图片感知、权限隔离 |
| Autonomous Control | 目标理解、动作效用、动态 Replan、预算与停止条件 |
| Tool Plane | 搜索、推荐、评估、学习、联网工具与风险边界 |
| Evolution | 多候选探索、holdout、全量回归、robustness gate |
| Memory | episodic / procedural / policy memory |
| Trust | 用户约束、独立验证、激活门槛、漂移回滚 |
| Durability | checkpoint、lease、heartbeat、fencing、幂等恢复 |
| Workspace | revision、更新锁、跨 worker 数据一致性 |
| Access | 可选登录、共享限流、生产安全边界 |

更完整的设计说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 部署

### 本地开发

默认本地模式不要求登录，适合单机开发和体验。

### 对外部署

生产模式必须显式配置访问密钥：

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

如果部署在可信反向代理后并确实需要读取转发 IP，再显式设置：

```bash
export LINGJING_TRUST_PROXY_IP=1
```

当前多 worker 设计假设 worker 共享同一 SQLite 与数据目录。真正的多机 / 多区域部署应把协调存储和对象存储迁移到共享基础设施，而不是把本地 SQLite 文件复制到多台机器。

---

## 数据

内置 sample catalog 可以直接体验。导入自己的数据时支持：

- items；
- interactions；
- query labels；
- eligibility / quality / popularity / freshness 等业务字段。

格式见 [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)。

---

## 质量门槛

```bash
make check
make test
make demo
```

仓库 CI 还会验证：

- Python 编译；
- 完整回归测试；
- CLI smoke；
- wheel 构建与干净安装；
- 安装后 Web / API 能力；
- 前端 JavaScript 语法；
- UI 不泄漏内部算法或第三方产品名；
- 没有历史编号产品路径或旧决策路径；
- 真实浏览器桌面 / 移动流程；
- 真实附件交互；
- 瞬时 polling 故障后的自动重连；
- 关键移动触控目标；
- 浏览器 page / console error；
- 多 worker lease / fencing / workspace revision；
- 生产访问与共享限流契约。

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

仓库只维护**一条主线实现**，不保留编号产品副本或平行历史源码树。

---

<div align="center">

### Search and recommendation are the capabilities. The harness is the product.

**真实工具 · 动态决策 · 可验证学习 · 多模态上下文 · 可控联网 · 可恢复执行**

</div>