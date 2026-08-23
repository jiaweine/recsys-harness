<div align="center">

# 序枢 · Recsys Harness

Search / Recommendation Agent Harness

面向现有搜索与推荐系统的诊断、离线评估、生产回放、策略进化和受控激活。

[![CI](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/jiaweine/recsys-harness/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local-first](https://img.shields.io/badge/runtime-local--first-111827)

[界面](#界面) · [算法](#算法与评价定义) · [生产回放](#4-生产业务回放) · [策略进化](#7-策略进化) · [快速启动](#快速启动) · [数据契约](#生产数据契约) · [边界](#当前边界)

</div>

---

## 界面

<p align="center">
  <img src="https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/overview.png" alt="序枢工作区" width="96%">
</p>

界面只保留任务、执行状态、证据和数据上下文。桌面端使用右侧证据轨，窄屏和移动端改为抽屉或底部面板，主任务保持第一视觉层级。

<!--
README asset pins retained for deterministic CI verification and historical visual references.
https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/overview.png
https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/workbench.png
https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/evidence.png
https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/mobile-workspace.png
https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/mobile-progress.png
https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/mobile-evidence.png
https://cdn.jsdelivr.net/gh/jiaweine/recsys-harness@4f2bee6f5e3256b32b63780f0cc83098ea7c85fc/docs/readme-assets/architecture.svg
-->

---

# 系统能力

- 搜索：query 处理、候选召回、混合排序、去冗余、NDCG、Recall、MRR、生产回放
- 推荐：用户画像、共现图、类目偏好、冷启动、探索、去冗余、coverage、freshness、novelty、生产回放
- 策略进化：连续权重 gene 与 capability gene 共同搜索，response surface、posterior routing、population、quality-diversity archive
- 独立验证：离线 identity holdout、生产 temporal request holdout、paired bootstrap、回归 gate
- 分段策略：在全局 strategy basin 周围按 request segment 搜索局部策略，稀疏 segment 回退全局策略
- Agent Harness：Mission Graph 由 declarative capability contract 编译，运行中根据证据、依赖和信息增益选择下一步
- 可靠运行：checkpoint、resume、worker lease、heartbeat、fencing、workspace revision、typed strategy memory

内建 SearchEngine 与 RecommendationEngine 是 reference engine。已有 Elasticsearch、OpenSearch、Vespa、内部 rank API 或推荐服务可以通过 read-only serving adapter 进入同一套评估链路。

---

# 算法与评价定义

下面按当前实现给出公式。符号用于解释代码，不附加代码中不存在的统计假设。

## 1. 搜索排序

默认连续权重：

```text
lexical     0.47
semantic    0.25
title       0.10
quality     0.07
popularity  0.04
freshness   0.07
diversity   0.08
```

默认 capability：

```text
query       rare_focus
candidate   postings_union
rerank      category_mmr
```

### 1.1 IDF 与字段 TF

目录文档数为 $N$，token $t$ 的 document frequency 为 $df_t$：

$$\operatorname{IDF}(t)=\ln\left(1+\frac{N-df_t+0.5}{df_t+0.5}\right)$$

标题、正文、类目字段的加权词频：

$$f_{t,d}=2.1\,tf_{title}(t,d)+tf_{text}(t,d)+0.75\,tf_{category}(t,d)$$

装备、用品、商品、产品、东西、好物等泛化 token 使用 $q_t=0.45$，其他 token 使用 $q_t=1$。

### 1.2 BM25 lexical score

当前常数：

$$k_1=1.45,\qquad b=0.72$$

文档长度为 $|d|$，平均长度为 $\overline{|d|}$：

$$S_{BM25}(q,d)=\sum_{t\in q}q_t\operatorname{IDF}(t)\frac{f_{t,d}(k_1+1)}{f_{t,d}+k_1\left(1-b+b\frac{|d|}{\overline{|d|}}\right)}$$

候选集合内最大值归一化：

$$S_{lex}(q,d)=\frac{S_{BM25}(q,d)}{\max_{d'}S_{BM25}(q,d')+\epsilon}$$

### 1.3 title match

query 去重 token 集为 $Q$，标题 token 集为 $T_d$：

$$O_{title}(q,d)=\frac{|Q\cap T_d|}{\max(1,|Q|)}$$

完整 query 是标题子串时 $E_{title}=1$，否则为 $0$：

$$S_{title}(q,d)=\min\left(1,0.70O_{title}(q,d)+0.55E_{title}(q,d)\right)$$

### 1.4 semantic score

query 和 item 文本都转换成稳定 hashed vector：

$$S_{sem}(q,d)=\max\left(0,\frac{v_q\cdot v_d}{\|v_q\|_2\|v_d\|_2}\right)$$

### 1.5 基础排序与去冗余

item 质量、归一化热度、新鲜度分别为 $Q_d,P_d,F_d$：

$$S_{base}(q,d)=0.47S_{lex}+0.25S_{sem}+0.10S_{title}+0.07Q_d+0.04P_d+0.07F_d$$

已选集合为 $A$，rerank capability 给出 $sim(d,a)$：

$$R(d,A)=\max_{a\in A}sim(d,a)$$

$$S_{rank}(d\mid A)=S_{base}(d)-\lambda_{div}R(d,A),\qquad\lambda_{div}=0.08$$

category rerank 使用 Jaccard：

$$sim_{cat}(i,j)=\frac{|C_i\cap C_j|}{|C_i\cup C_j|}$$

hybrid rerank：

$$sim_{hybrid}(i,j)=0.55sim_{cat}(i,j)+0.45sim_{sem}(i,j)$$

搜索解释面板中的 match signal：

$$S_{match}=0.65S_{lex}+0.35S_{sem}$$

---

## 2. 推荐排序

默认连续权重：

```text
profile      0.34
graph        0.20
category     0.10
quality      0.12
freshness    0.13
popularity   0.05
novelty      0.06
exploration  0.04
cold_start   0.06
diversity    0.14
```

默认 capability：

```text
profile      recency_balanced
candidate    full_pool
cold_start   quality_freshness
exploration  stable_fresh
rerank       category_mmr
```

### 2.1 时间衰减用户画像

历史事件集合为 $H_u$：

$$t_{max}=\max_{e\in H_u}t_e,\qquad a_e=\max(0,t_{max}-t_e)$$

给定 horizon $\tau$：

$$r_e=\exp\left(-\frac{a_e}{\max(1,\tau)}\right)$$

默认 recency_balanced 使用 $\tau=30$。事件实际贡献：

$$\widetilde w_e=w_e(0.55+0.45r_e)$$

用户向量：

$$\widetilde v_u=\sum_{e\in H_u}\widetilde w_e v_{i_e},\qquad v_u=\frac{\widetilde v_u}{\|\widetilde v_u\|_2}$$

profile fit：

$$S_{profile}(u,i)=\max(0,\cos(v_u,v_i))$$

### 2.2 类目偏好

历史类目权重为 $c_u(g)$：

$$Z_u=\sum_g c_u(g)$$

item 类目集合为 $C_i$：

$$S_{cat}(u,i)=\frac{\sum_{g\in C_i}c_u(g)}{\max(1,Z_u)}$$

### 2.3 共现图

每个用户最多取最近 $120$ 个去重历史 item 建立共现计数 $co(s,i)$。seed 权重为 $w_s$：

$$S_{graph}(u,i)=\min\left(1,\frac{\sum_s w_s\,co(s,i)}{\max(1,\sum_s w_s)}\right)$$

### 2.4 novelty、exploration 与 cold-start

归一化热度为 $P_i$：

$$S_{novelty}(i)=1-P_i$$

stable_fresh 使用稳定 hash $h(u,i)\in[0,1)$：

$$S_{explore}(u,i)=h(u,i)F_i$$

默认 cold prior：

$$S_{cold}^{qf}(i)=0.45Q_i+0.35F_i+0.20P_i$$

另两个内建 prior：

$$S_{cold}^{discover}(i)=0.40Q_i+0.35F_i+0.25(1-P_i)$$

$$S_{cold}^{explore}(i)=0.35Q_i+0.40F_i+0.25S_{explore}(u,i)$$

cold_start gene 是 independent gene，只对冷用户生效。

### 2.5 推荐基础分与 rerank

$$\begin{aligned}S_{base}(u,i)=&0.34S_{profile}+0.20S_{graph}+0.10S_{cat}+0.12Q_i+0.13F_i\\&+0.05P_i+0.06S_{novelty}+0.04S_{explore}+0.06S_{cold}\end{aligned}$$

warm user 使用 $S_{cold}=0$。

$$S_{rank}(i\mid A)=S_{base}(i)-\lambda_{div}\max_{j\in A}sim(i,j),\qquad\lambda_{div}=0.14$$

用于解释的 fit signal：

$$S_{fit}=\min\left(1,0.55S_{profile}+0.30S_{cat}+0.15S_{graph}\right)$$

---

## 3. 离线指标与 guardrail

### 3.1 Search relevance

相关集合为 $Rel_q$，前 $K$ 个结果为 $Rank_q^K$：

$$Recall@K=\frac{|Rank_q^K\cap Rel_q|}{|Rel_q|}$$

第一个相关结果的位置为 $r_q$：

$$RR@K=\begin{cases}\frac{1}{r_q},&r_q\le K\\0,&\text{otherwise}\end{cases}$$

MRR 是 query 上的平均 RR。

binary relevance 下：

$$DCG@K=\sum_{r=1}^{K}\frac{rel_r}{\log_2(r+1)}$$

$$NDCG@K=\frac{DCG@K}{\max(\epsilon,IDCG@K)}$$

### 3.2 Recommendation guardrail

审计用户曝光过的去重 item 集为 $E$，eligible item 集为 $I_{eligible}$：

$$Coverage=\frac{|E|}{|I_{eligible}|}$$

slate 中所有 category occurrence 为 $G$，去重类目集合为 $U(G)$：

$$Diversity=\frac{|U(G)|}{\max(1,|G|)}$$

$$Freshness=\frac{1}{|L|}\sum_{i\in L}F_i$$

$$Novelty=\frac{1}{|L|}\sum_{i\in L}(1-P_i)$$

冷启动 probe score：

$$Q_{cold}=0.35\overline Q+0.25\overline F+0.20\overline N+0.20D$$

没有 production reward 时的推荐 proxy quality：

$$Q_{proxy}^{rec}=0.41C+0.23D+0.18F+0.08N+0.10Q_{cold}$$

proxy quality 只用于离线 guardrail 和候选搜索，不被解释成 CTR、CVR、GMV、watch time 或 retention。

---

## 4. 生产业务回放

生产价值由 RewardSpec 和 ExposureEvent 定义。

### 4.1 event reward

事件类型为 $e$，业务权重为 $w_e$，事件数值为 $v_e$：

$$r_e=w_ev_e$$

RewardSpec 不属于 strategy genome，optimizer 不修改业务目标。

### 4.2 capped inverse propensity

有 propensity $p_e$ 时：

$$a_e=\min\left(c,\frac{1}{p_e}\right),\qquad c=20$$

没有 propensity 时 $a_e=1$。

### 4.3 rank discount 与 request value

候选策略对历史 request 重排。logged item 在候选结果中的 rank 为 $k_e$：

$$d(k_e)=\frac{1}{\log_2(k_e+1)}$$

request $r$ 的非零 reward event 集为 $E_r$，reward mass：

$$M_r=\sum_{e\in E_r}|r_e|a_e$$

进入候选 ranking 的 reward mass：

$$M_r^{ranked}=\sum_{e\in E_r,\ k_e\ available}|r_e|a_e$$

request coverage：

$$C_r=\frac{M_r^{ranked}}{M_r}$$

request policy value：

$$V_r=\frac{\sum_{e\in E_r,\ k_e\ available}r_ea_ed(k_e)}{M_r}$$

整个 replay 的业务值：

$$V_{policy}=\frac{1}{|R|}\sum_{r\in R}V_r$$

当前 estimator 名称：

```text
logged_replay
propensity_weighted_logged_replay
```

这不是完整无偏 OPE。没有历史曝光和 outcome 的 item 没有反事实标签。当前实现没有声称已具备完整 IPS、SNIPS 或 Doubly Robust。

---

## 5. Temporal future holdout

production event 先按 request_id 分组。同一个 request identity 不跨 discovery 和 holdout。

每组使用最大 timestamp：

$$t_r=\max_{e\in E_r}t_e$$

request 数量为 $n$，默认 holdout fraction 为 $0.25$。当 $n<4$ 时不生成 future holdout；当 $n\ge4$：

$$h=\max\left(1,\min\left(n-2,\operatorname{round}(0.25n)\right)\right)$$

按 $t_r$ 从旧到新排序：

$$Discovery=\{r_1,\ldots,r_{n-h}\}$$

$$Holdout=\{r_{n-h+1},\ldots,r_n\}$$

业务 temporal holdout 与 Search relevance holdout、Recommendation warm-user holdout 是独立证据。

---

## 6. Paired bootstrap confidence

reference 与 candidate 只比较共同 request_id：

$$\delta_i=V_i^{candidate}-V_i^{reference}$$

观测平均提升：

$$\widehat\Delta=\frac{1}{n}\sum_{i=1}^{n}\delta_i$$

bootstrap 每次有放回抽取 $n$ 个 request delta：

$$\Delta_b^*=\frac{1}{n}\sum_{j=1}^{n}\delta_{I_j}$$

默认请求 $600$ 次迭代，代码保证至少 $100$ 次 draw。

$$CI_{95}=\left[Q_{0.025}(\Delta^*),Q_{0.975}(\Delta^*)\right]$$

$$P_+=\frac{1}{B}\sum_{b=1}^{B}\mathbf1(\Delta_b^*>0)$$

种子由共同 request_id 与两组 request score 的稳定 hash 推导，相同输入可复现。

一个 paired request 会得到退化区间，但 public trust gate 会阻止 durable trust：

$$n_{paired}\ge2$$

同时还必须存在独立 domain guardrail holdout。

---

## 7. 策略进化

Strategy genome 同时包含 continuous gene 与 capability gene。

```text
Search continuous
  lexical semantic title quality popularity freshness diversity
Search capability
  query candidate rerank

Recommendation continuous
  profile graph category quality freshness popularity novelty exploration cold_start diversity
Recommendation capability
  profile candidate cold_start exploration rerank
```

### 7.1 schema-driven gene discovery

连续 gene 的 min、max、relative_step、group 来自 dataclass metadata；capability choice 来自 CapabilityRegistry。中心 evolver 不维护针对具体 query strategy 或 rerank strategy 的手写 mutation recipe。

### 7.2 blend group 投影

blend group $G$ 的 base 总量：

$$T_G=\sum_{j\in G}x_j^{base}$$

mutation 后在边界 $[l_j,u_j]$ 内重新分配并满足：

$$\sum_{j\in G}x_j^{projected}=T_G$$

independent gene 不参与该约束。

### 7.3 连续 gene 步长

$$floor_j=\max\left(0.008,0.018(u_j-l_j)\right)$$

$$step_j=\max\left(floor_j,|x_j|\rho_j\right)\max(0.25,s)$$

$\rho_j$ 为 relative_step，$s$ 为阶段 scale。

### 7.4 历史 Beta prior

每个 mutation arm 初始：

$$\alpha_a=1,\qquad\beta_a=1$$

trusted 或 active memory 给历史方向增加 credit，单条 memory 的 wins 被限制在 $[1,6]$。

$$\mu_a=\frac{\alpha_a}{\alpha_a+\beta_a}$$

$$\theta_a\sim Beta(\alpha_a,\beta_a)$$

### 7.5 response surface routing

单步邻居 objective delta：

$$\Delta J_a=J_a-J_{base}$$

局部信号：

$$L_a=\operatorname{clip}\left(0.5+\frac{\Delta J_a}{0.04},0.05,0.95\right)$$

routing score：

$$R_a=0.74L_a+0.26\theta_a$$

当前数据的局部响应占主导，历史 posterior 提供有界引导。

### 7.6 population 与 quality-diversity archive

默认搜索预算：

```text
population size   10
max generations    2
max dimensions    24
max eval samples  36
```

archive 以 mutation signature 为 niche key，只保留该 signature 下 objective 最大的 config：

$$Archive(s)=\arg\max_{x:\ signature(x)=s}J(x)$$

### 7.7 proxy objective

Search：

$$J_{search}^{proxy}=Q+0.08Recall-0.035W+0.015\min(0,D_{worst})$$

$Q$ 为平均 NDCG，$W$ 为 $\Delta NDCG<-0.02$ 的 query 比例。

Recommendation：

$$J_{rec}^{proxy}=Q_{proxy}+0.05F+0.03D+0.04Q_{cold}-0.03W+0.01\min(0,D_{worst})$$

推荐 robustness 的 per-user utility：

$$U_u=0.55D_u+0.45F_u$$

明显变差 user 满足：

$$\Delta U_u<-0.03$$

### 7.8 production objective

RewardSpec 与足够 production request 存在时：

$$J_{search}^{prod}=0.82V_{business}+0.18J_{search}^{proxy}$$

$$J_{rec}^{prod}=0.82V_{business}+0.18J_{rec}^{proxy}$$

business reward 是主要 routing signal，离线指标作为安全与体验 guardrail。

---

## 8. Safety gate 与 trust gate

### 8.1 Search production gate

safe_to_try 需要 relevance 样本至少 $3$，并同时满足：

$$\Delta Q\ge-0.01,\qquad\Delta Recall\ge-0.03$$

$$W\le0.40,\qquad D_{worst}\ge-0.40$$

future relevance holdout：

$$\Delta Q_{holdout}\ge-0.015,\qquad\Delta Recall_{holdout}\ge-0.04$$

future business holdout：

$$\Delta V_{holdout}\ge-0.02$$

有 holdout robustness 时：

$$D_{worst}^{holdout}\ge-0.50$$

trusted 还需要：

$$\Delta V_{full}>0$$

$$\Delta V_{discovery}>0.001$$

$$\Delta V_{holdout}\ge-0.003$$

$$P_+\ge0.65$$

最终还要满足 $n_{paired}\ge2$ 与独立 domain guardrail holdout。

### 8.2 Recommendation production gate

proxy safety：

$$n_{users}\ge3$$

$$\Delta Q\ge-0.003,\quad\Delta Coverage\ge-0.02,\quad\Delta Freshness\ge-0.012,\quad\Delta Q_{cold}\ge-0.03$$

$$W\le0.40,\qquad D_{worst}\ge-0.30$$

有独立 holdout 时：

$$\Delta Q_{holdout}\ge-0.008$$

$$\Delta Coverage_{holdout}\ge-0.06$$

$$\Delta Q_{cold,holdout}\ge-0.02$$

$$D_{worst}^{holdout}\ge-0.35$$

production 路径再要求：

$$\Delta V_{holdout}\ge-0.02$$

trusted 的业务条件与 Search 相同：

$$\Delta V_{full}>0,\quad\Delta V_{discovery}>0.001,\quad\Delta V_{holdout}\ge-0.003,\quad P_+\ge0.65$$

并经过 paired sample 与独立 holdout 的 public trust gate。

---

## 9. Segment-conditioned strategy portfolio

全局 evolution 先确定 strategy basin，production 证据足够时再按 request segment 在该 basin 周围构造 typed local neighborhood。

候选池包括 base global config、selected global config，以及 selected config 的每个 continuous/capability 邻居。

segment 最少需要：

$$n_{discovery}^{segment}\ge3$$

$$n_{holdout}^{segment}\ge2$$

以 Search segment 为例，trusted 还要求：

$$\Delta V_{discovery}^{segment}>0.001$$

$$\Delta V_{holdout}^{segment}\ge-0.003$$

$$P_+^{segment}\ge0.65$$

以及 segment relevance guardrail 可用并通过安全阈值。Recommendation segment 使用对应推荐 guardrail。证据不足的 segment 继续使用全局策略。

---

## 10. Capability-driven Mission Graph

runtime capability 通过 CapabilityContract 声明：

```text
name
domain
requirement_key
requires
priority
information_gain
cost
initial_status
hypotheses
```

MissionCompiler 对 AgentPlan 选择 enabled capability，把相同 requirement_key 的 capability 归为可替代实现。primary capability 按以下排序选择：

$$(-information\_gain,\ cost,\ order,\ name)$$

dependency 不在当前 capability closure 时，requirement 标记为 blocked。

```text
Goal + Authority
       ↓
Capability contracts
       ↓
MissionCompiler
       ↓
Evidence DAG
       ↓
Deliberation
       ↓
Capability invocation
       ↓
Observation / Reflection
       ↓
Requirement update
       ↓
Trajectory critic
       ↓
Verifier
```

当前 MissionCompiler 已经由 capability contract 驱动，不需要在 compiler 中为搜索复现、推荐审计等任务维护 tool-name branch。

---

# 生产数据契约

完整字段见 `docs/DATA_FORMAT.md`。

```yaml
items:
  - id: sku-1
    title: 商品A

reward_spec:
  weights:
    click: 1
    purchase: 5
    hide: -2
  inverse_propensity_cap: 20

events:
  - request_id: r-1
    timestamp: 100
    surface: recommend
    user_id: u-1
    item_id: sku-1
    event: click
    position: 1
    propensity: 0.5
    policy_id: prod-v1
```

ExposureEvent 核心字段：

```text
request_id
timestamp
surface
user_id or query
item_id
event
value
propensity
position
policy_id
model_version
experiment_id
metadata
```

interactions 用于推荐 profile；events 用于生产曝光、outcome、replay、temporal split 和 policy lineage。

---

# Existing system integration

`lingjing_harness/adapters.py` 提供 read-only serving adapter，将外部结果规范化后接入同一套 replay 与 guardrail。

adapter 会：

- 统一 item id
- 去重
- 拒绝非有限 score
- 保持确定性排序
- 将外部 ranking 交给 production replay

自动修改外部 production policy 仍要求接入方提供显式 typed write 或 experiment contract。

---

# 快速启动

要求 Python 3.11+。

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

打开：

```text
http://127.0.0.1:8765
```

开发验证：

```bash
python -m pip install -e .[dev]
pytest -q
python scripts/probe_harness_contract.py
```

---

# 策略生命周期

```text
candidate
   ↓
discovery evaluation
   ↓
domain guardrails
   ↓
future temporal holdout
   ↓
paired confidence
   ↓
trusted
   ↓ explicit authority
active
   ↓
periodic validation
   ├─ business regression → retire
   ├─ guardrail regression → retire
   └─ pass → keep active
```

当 production reward 存在时，strategy memory 优先保存 business reward，business regression 会在 proxy validation 之前检查，RewardSpec 不进入 optimizer gene space。

---

# 系统架构

```text
User goal + authority
        ↓
Runtime Capability Registry
        ↓
MissionCompiler → Evidence DAG
        ↓
Deliberation / Harness / Verifier
        ↓
Search + Recommendation tools
        ↓
Observations + durable state
        ↓
Mixed strategy genome
        ↓
Response surface + posterior routing
        ↓
Population + QD archive
        ↓
Domain guardrails + Production reward replay
        ↓
Independent holdout + Temporal holdout
        ↓
Paired confidence + regression gates
        ↓
Global trusted strategy
        ↓
Segment local portfolio
        ↓
Permissioned activation
        ↓
Revalidation / retirement
```

进一步文档：

- `docs/ARCHITECTURE.md`
- `docs/HARNESS_CONTRACT.md`
- `docs/VERTICAL_EVOLUTION.md`
- `docs/DATA_FORMAT.md`

---

# 可靠性约束

```text
business reward != proxy quality
RewardSpec cannot be evolved by optimizer
one request identity cannot cross temporal split
one future request cannot certify durable trust
business regression can retire active strategy
proxy validation cannot hide business regression
cold-start probe cannot collide with real user identity
blend mutation preserves declared group mass
stale worker cannot overwrite current run state
workspace revision change forces worker reload
```

运行层包括 Mission Graph checkpoint 与 resume、invocation idempotency、strategy schema canonicalization、SQLite worker lease、heartbeat、fencing、workspace revision、production auth、rate limit、CSP、attachment TTL、evidence retention 和 network permission isolation。

---

# 当前边界

1. 完整 OPE estimator suite 尚未实现，当前没有完整 IPS、SNIPS、Doubly Robust
2. Online experiment adapter 尚未覆盖 A/B、interleaving、canary、traffic allocation
3. External typed write contract 仍由接入方定义，默认 adapter 是 read-only evaluation
4. Latency、P99、infra cost 还没有进入统一 Pareto objective
5. 当前 segment portfolio 从全局 basin 周围做局部 typed neighborhood，不是无限制的 per-segment global search

---

# Repository map

```text
frontend/
  index.html                       工作台结构
  app.js                           主交互与运行状态
  layout.css                       响应式布局
  refinement.css                   精简信息层级
  copy-refinement.js               新任务精简文案

lingjing_harness/
  production.py                    RewardSpec / ExposureEvent / temporal replay / bootstrap
  adapters.py                      外部 serving read-only adapter
  domain.py                        Catalog / interactions / production evidence

  algorithms/
    search.py                      Search query / candidate / rank / rerank
    recommend_core.py              Recommendation profile / graph / cold-start / exploration / rerank
    capabilities.py                typed algorithm capability registry
    evaluation.py                  offline metrics 与 guardrail
    evolution_core.py              schema genes / response surface / posterior / QD
    production_evolution.py        business-routed evolution
    segment_evolution.py           request segment local strategy search
    evolution.py                   public evolution + trust evidence gate

  runtime/
    capabilities.py                runtime capability contracts
    mission_compiler.py            capability-driven Mission Graph compiler
    deliberation.py                evidence reasoning / reflection / critic
    harness.py                     durable Agent Harness loop
    verifier.py                    result 与 authority verification
    memory.py                      episodic / procedural / policy memory
    tools.py                       stable ToolRegistry surface
```

---

# 质量门槛

```bash
make check
make test
make demo
python scripts/probe_harness_contract.py
```

CI 覆盖 Python compile、完整 pytest、Harness contract、mixed genome、production reward、temporal split、paired bootstrap、segment portfolio、serving adapter、strategy lifecycle、recovery、frontend hygiene 和 browser flow。
