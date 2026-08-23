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

界面只保留任务、执行状态、证据和数据上下文。桌面端使用右侧证据轨，窄屏和移动端改为抽屉或底部面板，主任务始终保持第一视觉层级。

---

# 系统能力

序枢把搜推系统常见的分析与验证流程放进同一个可恢复 Harness。

- 搜索：query 处理、候选召回、混合排序、去冗余、NDCG、Recall、MRR、生产回放
- 推荐：用户画像、共现图、类目偏好、冷启动、探索、去冗余、coverage、freshness、novelty、生产回放
- 策略进化：连续权重 gene 与 capability gene 共同搜索，response surface、posterior routing、population、quality-diversity archive
- 独立验证：离线 identity holdout、生产 temporal request holdout、paired bootstrap、回归 gate
- 分段策略：在全局策略 basin 周围按 request segment 搜索局部策略，稀疏 segment 回退全局策略
- Agent Harness：Mission Graph 由 declarative capability contract 编译，运行中根据证据状态、依赖和信息增益选择下一步
- 运行可靠性：checkpoint、resume、worker lease、heartbeat、fencing、workspace revision、typed strategy memory

内建 SearchEngine 与 RecommendationEngine 是 reference engine。已有 Elasticsearch、OpenSearch、Vespa、内部 rank API 或推荐服务可以通过 read-only serving adapter 进入同一套评估链路。

---

# 算法与评价定义

下面的公式按当前代码实现展开。符号只用于说明实现，不引入代码中不存在的统计假设。

## 1. 搜索排序

默认 SearchConfig 的连续权重为：

```text
lexical     0.47
semantic    0.25
title       0.10
quality     0.07
popularity  0.04
freshness   0.07
diversity   0.08
```

默认 capability 为：

```text
query       rare_focus
candidate   postings_union
rerank      category_mmr
```

### 1.1 词项 IDF

设目录文档数为 $N$，词项 $t$ 的 document frequency 为 $df_t$。实现使用：

$$
\operatorname{IDF}(t)
=
\ln\left(
1+\frac{N-df_t+0.5}{df_t+0.5}
\right)
$$

### 1.2 字段加权 TF

标题、正文、类目三个字段分别统计词频。对 query token $t$：

$$
f_{t,d}
=
2.1\,tf_{title}(t,d)
+1.0\,tf_{text}(t,d)
+0.75\,tf_{category}(t,d)
$$

对装备、用品、商品、产品、东西、好物这类泛化 token，额外使用 $0.45$ 的 query weight；其他 token 使用 $1$。

记该权重为 $q_t$。

### 1.3 BM25 lexical score

当前常数：

$$
k_1=1.45,\qquad b=0.72
$$

文档长度为 $|d|$，平均长度为 $\overline{|d|}$，则：

$$
S_{BM25}(q,d)
=
\sum_{t\in q}
q_t\operatorname{IDF}(t)
\frac{f_{t,d}(k_1+1)}
{f_{t,d}+k_1\left(1-b+b\frac{|d|}{\overline{|d|}}\right)}
$$

候选集合内再做最大值归一化：

$$
S_{lex}(q,d)
=
\frac{S_{BM25}(q,d)}
{\max_{d'}S_{BM25}(q,d')+\epsilon}
$$

代码中的 $\epsilon$ 为极小保护值，用于避免除零。

### 1.4 title match

设 query 去重 token 集为 $Q$，标题 token 集为 $T_d$：

$$
O_{title}(q,d)=\frac{|Q\cap T_d|}{\max(1,|Q|)}
$$

如果完整 query 是标题的小写字符串子串，则 $E_{title}=1$，否则为 $0$。

最终标题分：

$$
S_{title}(q,d)
=
\min\left(1,
0.70O_{title}(q,d)+0.55E_{title}(q,d)
\right)
$$

### 1.5 semantic score

query 与 item 文本都转换成稳定 hashed vector。语义分使用非负 cosine：

$$
S_{sem}(q,d)
=
\max\left(0,
\frac{v_q\cdot v_d}{\|v_q\|_2\|v_d\|_2}
\right)
$$

### 1.6 基础排序分

设 item 自身质量、归一化热度、新鲜度分别为 $Q_d,P_d,F_d$。默认基础分：

$$
\begin{aligned}
S_{base}(q,d)=
&0.47S_{lex}
+0.25S_{sem}
+0.10S_{title}\\
&+0.07Q_d
+0.04P_d
+0.07F_d
\end{aligned}
$$

这些权重属于 blend group。进化时该组会在边界内投影回原总质量，避免单个 gene 变化后无意扩大总 score scale。

### 1.7 去冗余 rerank

已选集合为 $A$，候选 item 为 $d$，rerank capability 给出相似度 $sim(d,a)$。冗余度：

$$
R(d,A)=
\max_{a\in A}sim(d,a)
$$

首个 item 的冗余度为 $0$。

最终逐步选择分：

$$
S_{rank}(d\mid A)
=
S_{base}(d)-\lambda_{div}R(d,A)
$$

默认：

$$
\lambda_{div}=0.08
$$

category rerank 使用 Jaccard：

$$
sim_{cat}(i,j)
=
\frac{|C_i\cap C_j|}{|C_i\cup C_j|}
$$

semantic rerank 使用 item vector cosine。hybrid rerank 为：

$$
sim_{hybrid}(i,j)
=
0.55sim_{cat}(i,j)+0.45sim_{sem}(i,j)
$$

---

## 2. 推荐排序

默认 RecommendConfig：

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

用户事件集合为 $H_u$，最近时间为：

$$
t_{max}=\max_{e\in H_u}t_e
$$

事件 age：

$$
a_e=\max(0,t_{max}-t_e)
$$

给定 horizon $\tau$，recency：

$$
r_e=\exp\left(-\frac{a_e}{\max(1,\tau)}\right)
$$

默认 recency_balanced 使用：

$$
\tau=30
$$

实际事件贡献权重：

$$
\widetilde w_e
=
w_e\left(0.55+0.45r_e\right)
$$

用户向量是历史 item vector 的加权和并进行 L2 归一化：

$$
\widetilde v_u
=
\sum_{e\in H_u}\widetilde w_e v_{i_e}
$$

$$
v_u
=
\frac{\widetilde v_u}
{\|\widetilde v_u\|_2}
$$

profile fit：

$$
S_{profile}(u,i)=\max(0,\cos(v_u,v_i))
$$

### 2.2 类目偏好

历史事件同时累积用户类目权重 $c_u(g)$。总类目质量：

$$
Z_u=\sum_g c_u(g)
$$

item $i$ 的类目集合为 $C_i$，则：

$$
S_{cat}(u,i)
=
\frac{\sum_{g\in C_i}c_u(g)}{\max(1,Z_u)}
$$

### 2.3 共现图

每个用户最多取最近 $120$ 个去重历史 item 构造共现计数 $co(s,i)$。

历史 seed 权重为 $w_s$，则候选图分：

$$
S_{graph}(u,i)
=
\min\left(
1,
\frac{\sum_s w_s\,co(s,i)}
{\max(1,\sum_s w_s)}
\right)
$$

### 2.4 novelty 与 exploration

归一化热度为 $P_i$：

$$
S_{novelty}(i)=1-P_i
$$

stable_fresh exploration 先根据 user 与 item 生成稳定 hash：

$$
h(u,i)\in[0,1)
$$

再乘新鲜度：

$$
S_{explore}(u,i)=h(u,i)F_i
$$

因此同一用户、同一 item 的探索信号稳定，不依赖运行时随机数。

### 2.5 冷启动 prior

默认 quality_freshness：

$$
S_{cold}^{qf}(i)
=
0.45Q_i+0.35F_i+0.20P_i
$$

另一个 discovery prior：

$$
S_{cold}^{discover}(i)
=
0.40Q_i+0.35F_i+0.25(1-P_i)
$$

fresh exploration prior：

$$
S_{cold}^{explore}(i)
=
0.35Q_i+0.40F_i+0.25S_{explore}(u,i)
$$

只有冷用户会激活 cold-start 分量，因此 cold_start gene 是 independent gene，不与 warm ranking blend 一起归一化。

### 2.6 推荐基础分

记 profile、graph、category、quality、freshness、popularity、novelty、exploration、cold prior 为对应信号，默认：

$$
\begin{aligned}
S_{base}(u,i)=
&0.34S_{profile}
+0.20S_{graph}
+0.10S_{cat}\\
&+0.12Q_i
+0.13F_i
+0.05P_i\\
&+0.06S_{novelty}
+0.04S_{explore}
+0.06S_{cold}
\end{aligned}
$$

对 warm user，$S_{cold}=0$。

最终 rerank 与搜索相同：

$$
S_{rank}(i\mid A)
=
S_{base}(i)-\lambda_{div}\max_{j\in A}sim(i,j)
$$

默认：

$$
\lambda_{div}=0.14
$$

界面中 fit signal 不是最终 ranking score，而是用于解释的聚合信号：

$$
S_{fit}
=
\min\left(
1,
0.55S_{profile}+0.30S_{cat}+0.15S_{graph}
\right)
$$

---

## 3. 离线指标与 guardrail

### 3.1 Recall@K

相关集合为 $Rel_q$，前 $K$ 个结果为 $Rank_q^K$：

$$
Recall@K
=
\frac{|Rank_q^K\cap Rel_q|}{|Rel_q|}
$$

### 3.2 Reciprocal Rank

第一个相关结果的位置为 $r_q$：

$$
RR@K=
\begin{cases}
\frac{1}{r_q}, & r_q\le K\\
0, & \text{otherwise}
\end{cases}
$$

MRR 是 query 上的平均 RR。

### 3.3 NDCG@K

当前 relevance label 是 binary relevance。

$$
DCG@K
=
\sum_{r=1}^{K}
\frac{rel_r}{\log_2(r+1)}
$$

理想排序的分数为 $IDCG@K$：

$$
NDCG@K
=
\frac{DCG@K}{\max(\epsilon,IDCG@K)}
$$

### 3.4 Recommendation coverage

审计用户集合产生过的去重 item 集为 $E$，eligible item 集为 $I_{eligible}$：

$$
Coverage
=
\frac{|E|}{|I_{eligible}|}
$$

### 3.5 Category diversity

一个 slate 中所有 category occurrence 构成序列 $G$，去重类目集合为 $U(G)$：

$$
Diversity
=
\frac{|U(G)|}{\max(1,|G|)}
$$

注意这里衡量的是类目 occurrence 的去重比例，不是 pairwise intra-list distance。

### 3.6 Freshness 与 novelty

$$
Freshness
=
\frac{1}{|L|}\sum_{i\in L}F_i
$$

$$
Novelty
=
\frac{1}{|L|}\sum_{i\in L}(1-P_i)
$$

### 3.7 Cold-start probe

系统生成不会与真实用户碰撞的 deterministic cold identity。每个冷启动 slate 的 guardrail score：

$$
Q_{cold}
=
0.35\overline Q
+0.25\overline F
+0.20\overline N
+0.20D
$$

### 3.8 推荐 proxy quality

没有生产 RewardSpec 和 exposure event 时，推荐离线综合分为：

$$
Q_{proxy}^{rec}
=
0.41C
+0.23D
+0.18F
+0.08N
+0.10Q_{cold}
$$

其中 $C,D,F,N$ 分别为 coverage、diversity、freshness、novelty。

这个值只用于离线 guardrail 与候选搜索，不被解释成 CTR、CVR、GMV、watch time 或 retention。

---

## 4. 生产业务回放

生产价值由 RewardSpec 与 ExposureEvent 定义。

### 4.1 事件 reward

事件类型为 $e$，业务配置权重为 $w_e$，事件数值为 $v_e$：

$$
r_e=w_e v_e
$$

RewardSpec 不属于 strategy genome，optimizer 不能修改业务目标。

### 4.2 capped inverse propensity

有 propensity $p_e$ 时：

$$
a_e
=
\min\left(c,\frac{1}{p_e}\right)
$$

$c$ 为 inverse_propensity_cap，默认：

$$
c=20
$$

没有 propensity 时：

$$
a_e=1
$$

### 4.3 rank discount

候选策略把历史 request 重新排序。logged item 在候选排名中的位置为 $k_e$：

$$
d(k_e)=\frac{1}{\log_2(k_e+1)}
$$

如果 logged item 没有进入候选排名，则其 numerator 贡献为 $0$。

### 4.4 request reward mass

对 request $r$ 的非零 reward event 集合 $E_r$：

$$
M_r
=
\sum_{e\in E_r}|r_e|a_e
$$

进入候选 ranking 的 logged reward mass：

$$
M_r^{ranked}
=
\sum_{e\in E_r:k_e\ exists}|r_e|a_e
$$

request reward coverage：

$$
C_r
=
\frac{M_r^{ranked}}{M_r}
$$

### 4.5 request policy value

$$
V_r
=
\frac{
\sum_{e\in E_r:k_e\ exists}r_e a_e d(k_e)
}{M_r}
$$

整个 replay 的业务 reward 是可评分 request 的宏平均：

$$
V_{policy}
=
\frac{1}{|R|}\sum_{r\in R}V_r
$$

整体 reward coverage 同样对 request 的 $C_r$ 做平均。

当前返回的 estimator 名称只有：

```text
logged_replay
propensity_weighted_logged_replay
```

这不是完整无偏 OPE。没有历史曝光和 outcome 的 item 仍然没有反事实标签。当前实现把 logged replay 用作 routing signal 与安全证据，未声称已经实现完整 IPS、SNIPS 或 Doubly Robust。

---

## 5. Temporal future holdout

生产 event 先按 request_id 分组。同一个 request identity 永远不会跨 discovery 与 holdout。

每个 request group 使用该 group 的最大 timestamp 排序：

$$
t_r=\max_{e\in E_r}t_e
$$

设 request 数量为 $n$，默认 holdout fraction 为 $0.25$。当 $n<4$ 时不生成 future holdout。

当 $n\ge4$：

$$
h
=
\max\left(
1,
\min\left(n-2,\operatorname{round}(0.25n)\right)
\right)
$$

按 $t_r$ 从旧到新排序后：

$$
Discovery=\{r_1,\ldots,r_{n-h}\}
$$

$$
Holdout=\{r_{n-h+1},\ldots,r_n\}
$$

这样至少保留两个 discovery request，同时 holdout 是时间上更新的 request identity。

业务 temporal holdout 与搜索 relevance holdout、推荐 warm-user holdout 是独立证据来源。

---

## 6. Paired bootstrap confidence

reference 与 candidate 只比较共同 request_id。

对共同 request $r_i$：

$$
\delta_i
=
V_i^{candidate}-V_i^{reference}
$$

观测平均提升：

$$
\widehat\Delta
=
\frac{1}{n}\sum_{i=1}^{n}\delta_i
$$

每次 bootstrap 从 $\{\delta_1,\ldots,\delta_n\}$ 有放回抽取 $n$ 个样本并取均值：

$$
\Delta_b^*
=
\frac{1}{n}\sum_{j=1}^{n}\delta_{I_j}
$$

默认请求 $600$ 次迭代，代码保证至少 $100$ 次 draw。

95% 区间由 bootstrap mean 的经验分位数给出：

$$
CI_{95}
=
\left[
Q_{0.025}(\Delta^*),
Q_{0.975}(\Delta^*)
\right]
$$

正提升概率：

$$
P_+
=
\frac{1}{B}\sum_{b=1}^{B}\mathbf 1(\Delta_b^*>0)
$$

随机种子由共同 request_id 与两组 request score 的稳定 hash 推导，所以相同输入会得到可复现结果。

只有一个 paired request 时函数会返回退化区间，但 public trust gate 会阻止 durable trust。业务信任至少需要：

$$
n_{paired}\ge2
$$

并且必须存在独立 domain guardrail holdout。

---

## 7. 策略进化

Strategy genome 同时包含连续 gene 与 capability gene。

```text
Search
  continuous  lexical semantic title quality popularity freshness diversity
  capability  query candidate rerank

Recommendation
  continuous  profile graph category quality freshness popularity novelty exploration cold_start diversity
  capability  profile candidate cold_start exploration rerank
```

### 7.1 schema-driven gene discovery

连续 gene 的 min、max、relative_step、group 来自 dataclass metadata。capability gene 的 choice 来自 CapabilityRegistry。

中心 evolver 不维护针对某个 query strategy 或 rerank strategy 的手写 mutation recipe。

### 7.2 blend group 投影

对需要保持总质量的 blend group $G$，原始总量为：

$$
T_G=\sum_{j\in G}x_j^{base}
$$

mutation 后在每个 gene 的边界 $[l_j,u_j]$ 内重新分配，最终满足：

$$
\sum_{j\in G}x_j^{projected}=T_G
$$

independent gene 不参与这个约束，因此 diversity 与 cold-start 等独立压力不会静默缩放主排序 blend。

### 7.3 单维响应步长

对连续 gene $x_j$：

$$
floor_j
=
\max\left(
0.008,
0.018(u_j-l_j)
\right)
$$

$$
step_j
=
\max\left(
floor_j,
|x_j|\rho_j
\right)
\cdot\max(0.25,s)
$$

其中 $\rho_j$ 为 relative_step，$s$ 为搜索阶段使用的 scale。

### 7.4 历史策略 Beta prior

每个 mutation arm 初始：

$$
\alpha_a=1,\qquad\beta_a=1
$$

已验证 trusted 或 active strategy 会把历史方向性 credit 加到对应 arm。单条 memory 的 wins 被限制在：

$$
1\le wins\le6
$$

连续 gene 的历史上调会增加 up arm 的 $\alpha$，并增加 down arm 的 $\beta$；下调相反。capability gene 对曾经获胜的 choice 做同类更新。

prior mean：

$$
\mu_a
=
\frac{\alpha_a}{\alpha_a+\beta_a}
$$

每次 response surface routing 采样：

$$
\theta_a\sim Beta(\alpha_a,\beta_a)
$$

### 7.5 response surface local signal

单步邻居 objective 相对 base 的变化：

$$
\Delta J_a=J_a-J_{base}
$$

局部信号：

$$
L_a
=
\operatorname{clip}\left(
0.5+\frac{\Delta J_a}{0.04},
0.05,
0.95
\right)
$$

routing score：

$$
R_a=0.74L_a+0.26\theta_a
$$

因此当前数据的局部响应占主导，历史 posterior 用于有界引导，而不是覆盖本次证据。

### 7.6 population 与 quality-diversity archive

response surface 排名前列的 config、正向维度组合和 trusted memory 作为 seed。当前默认：

```text
population size   10
max generations    2
max dimensions    24
max eval samples  36
```

archive 以 mutation signature 作为 niche key。对每个 signature 只保留 objective 最大的 config：

$$
Archive(s)
=
\arg\max_{x:\ signature(x)=s}J(x)
$$

这让进化不会只保留一个单一 basin 的重复近邻。

### 7.7 proxy objective

搜索 proxy objective：

$$
J_{search}^{proxy}
=
Q
+0.08Recall
-0.035W
+0.015\min(0,D_{worst})
$$

其中 $Q$ 为平均 NDCG，$W$ 为明显变差 query 比例，$D_{worst}$ 为最差 query NDCG delta。

搜索中明显变差的定义：

$$
\Delta NDCG<-0.02
$$

推荐 proxy objective：

$$
\begin{aligned}
J_{rec}^{proxy}
=
&Q_{proxy}
+0.05F
+0.03D
+0.04Q_{cold}\\
&-0.03W
+0.01\min(0,D_{worst})
\end{aligned}
$$

推荐 robustness 的 per-user utility：

$$
U_u=0.55D_u+0.45F_u
$$

明显变差 user 的定义：

$$
\Delta U_u<-0.03
$$

### 7.8 production objective

当 RewardSpec 与足够 production request 存在时，业务 reward 成为主要 routing objective。

搜索：

$$
J_{search}^{prod}
=
0.82V_{business}
+0.18J_{search}^{proxy}
$$

推荐：

$$
J_{rec}^{prod}
=
0.82V_{business}
+0.18J_{rec}^{proxy}
$$

这里的 $0.18$ 不是把 guardrail 当业务收益，而是让 relevance、coverage、freshness、cold-start 和 robustness 对候选排序保留有界安全影响。

---

## 8. Safety gate 与 trust gate

### 8.1 Search production gate

当前 search candidate 要进入 safe_to_try，必须同时满足：

$$
\Delta Q\ge-0.01
$$

$$
\Delta Recall\ge-0.03
$$

$$
W\le0.40
$$

$$
D_{worst}\ge-0.40
$$

future relevance holdout：

$$
\Delta Q_{holdout}\ge-0.015
$$

$$
\Delta Recall_{holdout}\ge-0.04
$$

future business holdout：

$$
\Delta V_{holdout}\ge-0.02
$$

如果 holdout robustness 可用：

$$
D_{worst}^{holdout}\ge-0.50
$$

并且 relevance query 样本数至少为 $3$。

production trusted 还需要：

$$
\Delta V_{full}>0
$$

$$
\Delta V_{discovery}>0.001
$$

$$
\Delta V_{holdout}\ge-0.003
$$

$$
P_+\ge0.65
$$

再经过 public trust evidence gate：

$$
n_{paired}\ge2
$$

且 domain guardrail holdout 必须独立存在。

### 8.2 Recommendation production gate

推荐先通过 proxy safety gate：

$$
n_{users}\ge3
$$

$$
\Delta Q\ge-0.003
$$

$$
\Delta Coverage\ge-0.02
$$

$$
\Delta Freshness\ge-0.012
$$

$$
\Delta Q_{cold}\ge-0.03
$$

$$
W\le0.40
$$

$$
D_{worst}\ge-0.30
$$

有独立 holdout 时还需要：

$$
\Delta Q_{holdout}\ge-0.008
$$

$$
\Delta Coverage_{holdout}\ge-0.06
$$

$$
\Delta Q_{cold,holdout}\ge-0.02
$$

$$
D_{worst}^{holdout}\ge-0.35
$$

production 路径再增加：

$$
\Delta V_{holdout}\ge-0.02
$$

trusted 的业务条件与搜索一致：

$$
\Delta V_{full}>0,
\quad
\Delta V_{discovery}>0.001,
\quad
\Delta V_{holdout}\ge-0.003,
\quad
P_+\ge0.65
$$

最终同样要求至少两个 paired future request 与独立 domain holdout。

---

## 9. Segment-conditioned strategy portfolio

全局 evolution 先找到一个 strategy basin。生产证据足够时，系统再按 request segment 在该 basin 周围构造 typed local neighborhood。

segment 候选池包含：

```text
base global config
selected global config
selected config around every continuous neighbor
selected config around every capability neighbor
```

segment 至少需要：

$$
n_{discovery}^{segment}\ge3
$$

$$
n_{holdout}^{segment}\ge2
$$

以 search segment 为例，trusted 还要求：

$$
\Delta V_{discovery}^{segment}>0.001
$$

$$
\Delta V_{holdout}^{segment}\ge-0.003
$$

$$
P_+^{segment}\ge0.65
$$

以及该 segment 的 relevance guardrail 可用并通过安全阈值。

推荐 segment 使用对应推荐 guardrail。证据不足的 segment 不强行学习局部 policy，继续使用全局策略作为 fallback。

---

## 10. Capability-driven Mission Graph

runtime capability 通过 declarative CapabilityContract 声明：

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

MissionCompiler 对当前 AgentPlan 选择 enabled capability，把相同 requirement_key 的 capability 归为可替代实现，并按：

$$
(-information\_gain,\ cost,\ order,\ name)
$$

的顺序选择 primary capability。

如果 requirement 的 dependency 不在当前 capability closure 中，则 requirement 被标记为 blocked。

运行时因此追踪的是 evidence DAG，而不是写死的 tool sequence：

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

当前 Mission Compiler 已经由 capability contract 驱动，不再需要在 compiler 中为搜索复现、推荐审计等任务写 tool-name branch。

---

# 生产数据契约

完整字段见 `docs/DATA_FORMAT.md`。

最小结构可以使用 YAML 表达：

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

ExposureEvent 的核心字段：

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

interactions 用于构造推荐 profile。events 用于生产曝光、outcome、replay、temporal split 和 policy lineage，两者职责分开。

Catalog.summary 会报告：

```text
production_events
production_requests
search_replay_requests
recommend_replay_requests
business_reward_ready
```

---

# Existing system integration

内建 engine 不是迁移目标。`lingjing_harness/adapters.py` 提供 read-only serving adapter，将外部结果规范化后接入同一套 replay 与 guardrail。

adapter 边界会：

- 统一 item id
- 去重
- 拒绝非有限 score
- 保持确定性排序
- 将外部 ranking 直接交给 production replay

当前 external adapter 只负责评估。自动写入外部 production policy 仍要求接入方提供显式 typed write 或 experiment contract。

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

打开本地工作区：

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

当 production reward 存在时：

- strategy memory 的 score 优先保存 business reward
- memory payload 保存 evaluation_basis 与 business_validation
- business regression 在 proxy validation 之前检查
- proxy-only refresh 不能隐藏 business regression
- RewardSpec 不会进入 optimizer gene space

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
────────────────────────────────────
        ↓
Mixed strategy genome
        ↓
Response surface + posterior routing
        ↓
Population + QD archive
        ↓
┌────────────────┬───────────────────┐
│ Domain         │ Production        │
│ guardrails     │ reward replay     │
└───────┬────────┴────────┬──────────┘
        ↓                 ↓
Independent holdout + temporal holdout
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

当前工程中已经明确维护的 invariant：

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

运行层还包括：

- Mission Graph checkpoint 与 resume
- invocation idempotency
- strategy schema canonicalization
- SQLite worker lease、heartbeat 与 fencing
- workspace revision
- production auth、rate limit 与 CSP
- attachment TTL 与 evidence retention
- network permission isolation

---

# 当前边界

当前实现没有把尚未完成的生产实验能力写成已具备功能。主要边界：

1. 完整 OPE estimator suite 尚未实现，当前没有完整 IPS、SNIPS、Doubly Robust
2. Online experiment adapter 尚未覆盖 A/B、interleaving、canary、traffic allocation
3. External typed write contract 仍由接入方定义，默认 adapter 是 read-only evaluation
4. Latency、P99、infra cost 还没有进入统一 Pareto objective
5. 当前 segment portfolio 是从全局 basin 周围做局部 typed neighborhood，并不是无限制的 per-segment global search

---

# Repository map

```text
frontend/
  index.html                       工作台结构
  app.js                           主交互与运行状态
  layout.css                      响应式布局
  refinement.css                  精简信息层级

lingjing_harness/
  production.py                   RewardSpec / ExposureEvent / temporal replay / bootstrap
  adapters.py                     外部 serving read-only adapter
  domain.py                       Catalog / interactions / production evidence

  algorithms/
    search.py                     搜索 query / candidate / rank / rerank
    recommend_core.py             推荐 profile / graph / cold-start / exploration / rerank
    capabilities.py               typed algorithm capability registry
    evaluation.py                 offline metrics 与 guardrail
    evolution_core.py             schema genes / response surface / posterior / QD
    production_evolution.py       business-routed evolution
    segment_evolution.py          request segment local strategy search
    segment_credit.py             segment strategy credit
    evolution.py                  public evolution + trust evidence gate

  runtime/
    capabilities.py               runtime capability contracts
    mission_compiler.py           capability-driven Mission Graph compiler
    deliberation.py               evidence reasoning / reflection / critic
    harness.py                    durable Agent Harness loop
    verifier.py                   result 与 authority verification
    memory.py                     episodic / procedural / policy memory
    tools.py                      stable ToolRegistry surface

  api.py                          API compatibility surface
  api_core.py                     API / auth / workspace / recovery
  store.py                        runs / leases / revisions / rate limit

tests/
  test_production_value_loop.py   reward / temporal / replay / business evolution
  test_segment_portfolio.py       segment strategy portfolio
  test_capability_mission.py      capability-driven mission compilation
  test_serving_adapters.py        external adapter boundary
```

---

# 质量门槛

```bash
make check
make test
make demo
python scripts/probe_harness_contract.py
```

CI 覆盖：

- Python compile 与完整 pytest
- Mission / Deliberation / Harness contract
- mixed genome 与 capability stages
- production reward、temporal request split、paired bootstrap
- segment portfolio 与 strategy credit
- malformed external adapter output
- strategy lifecycle、recovery、lease 与 fencing
- CLI 与 wheel clean install
- frontend syntax 与 desktop / mobile browser flow
