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

界面围绕任务、运行状态、证据和数据上下文组织。桌面端使用辅助详情栏，窄屏和移动端使用抽屉或底部面板，主任务保持第一视觉层级。

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
- 策略进化：连续权重 gene 与 capability gene 共同搜索，包含 response surface、posterior routing、population 和 quality-diversity archive
- 独立验证：离线 identity holdout、生产 temporal request holdout、paired bootstrap 和回归 gate
- 分段策略：在全局 strategy basin 周围按 request segment 搜索局部策略，稀疏 segment 回退全局策略
- Agent Harness：Mission Graph 由 declarative capability contract 编译，运行中根据证据、依赖和信息增益选择下一步
- 可靠运行：checkpoint、resume、worker lease、heartbeat、fencing、workspace revision 和 typed strategy memory

内建 `SearchEngine` 与 `RecommendationEngine` 是 reference engine。已有 Elasticsearch、OpenSearch、Vespa、内部 rank API 或推荐服务可以通过 read-only serving adapter 接入同一套评估链路。

---

# 算法与评价定义

下面按当前代码实现给出公式。符号用于解释实现，不附加代码中不存在的统计假设。

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

$$
\operatorname{IDF}(t)
=
\ln\left(1+\frac{N-df_t+0.5}{df_t+0.5}\right)
$$

标题、正文、类目字段的加权词频：

$$
f_{t,d}
=
2.1\,tf_{title}(t,d)
+tf_{text}(t,d)
+0.75\,tf_{category}(t,d)
$$

装备、用品、商品、产品、东西、好物等泛化 token 使用 $q_t=0.45$，其他 token 使用 $q_t=1$。

### 1.2 BM25 lexical score

当前常数：

$$
k_1=1.45,\qquad b=0.72
$$

文档长度为 $|d|$，平均长度为 $\overline{|d|}$：

$$
S_{BM25}(q,d)
=
\sum_{t\in q}
q_t\operatorname{IDF}(t)
\frac{f_{t,d}(k_1+1)}
{f_{t,d}+k_1\left(1-b+b\frac{|d|}{\overline{|d|}}\right)}
$$

候选集合内最大值归一化：

$$
S_{lex}(q,d)
=
\frac{S_{BM25}(q,d)}
{\max\left(10^{-9},\max_{d'}S_{BM25}(q,d')\right)}
$$

### 1.3 title match

query 去重 token 集为 $Q$，标题 token 集为 $T_d$：

$$
O_{title}(q,d)
=
\frac{|Q\cap T_d|}{\max(1,|Q|)}
$$

完整 query 是标题子串时 $E_{title}=1$，否则为 $0$：

$$
S_{title}(q,d)
=
\min\left(1,0.70O_{title}(q,d)+0.55E_{title}(q,d)\right)
$$

### 1.4 semantic score

query 和 item 文本都转换成稳定 hashed vector：

$$
S_{sem}(q,d)
=
\max\left(0,
\frac{v_q\cdot v_d}
{\|v_q\|_2\|v_d\|_2}
\right)
$$

### 1.5 基础排序

item 质量、归一化热度、新鲜度分别为 $Q_d,P_d,F_d$：

$$
S_{base}(q,d)
=
0.47S_{lex}
+0.25S_{sem}
+0.10S_{title}
+0.07Q_d
+0.04P_d
+0.07F_d
$$

搜索解释面板中的 match signal：

$$
S_{match}
=
0.65S_{lex}+0.35S_{sem}
$$

### 1.6 去冗余 rerank

已选集合为 $A$，rerank capability 给出 $sim(d,a)$：

$$
R(d,A)=\max_{a\in A}sim(d,a)
$$

首个 item 的冗余度为 $0$。最终逐步选择分：

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

semantic rerank 使用 item vector cosine。hybrid rerank：

$$
sim_{hybrid}(i,j)
=
0.55sim_{cat}(i,j)+0.45sim_{sem}(i,j)
$$

### 1.7 query 与候选策略

`rare_focus` 优先保留区分度较高的 token。设目录大小为 $N$，token 保留条件为：

$$
df_t\le \max(64,\lfloor0.35N\rfloor)
$$

`catalog_expand` 在当前目录中从锚点文档继续选择高 IDF token，最多加入两个扩展 token。

`semantic_rescue` 在 lexical candidate 之外补充 semantic candidate，阈值为：

$$
S_{sem}\ge0.16
$$

补充预算取当前候选规模与固定上下限共同决定，最多优先加入语义相似度最高的候选。

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

$$
t_{max}=\max_{e\in H_u}t_e
$$

事件 age：

$$
a_e=\max(0,t_{max}-t_e)
$$

给定 horizon $\tau$：

$$
r_e
=
\exp\left(-\frac{a_e}{\max(1,\tau)}\right)
$$

默认 `recency_balanced` 使用：

$$
\tau=30
$$

事件实际贡献：

$$
\widetilde w_e
=
w_e(0.55+0.45r_e)
$$

用户向量：

$$
\widetilde v_u
=
\sum_{e\in H_u}\widetilde w_ev_{i_e}
$$

$$
v_u
=
\frac{\widetilde v_u}{\|\widetilde v_u\|_2}
$$

profile fit：

$$
S_{profile}(u,i)
=
\max(0,\cos(v_u,v_i))
$$

### 2.2 类目偏好

历史类目权重为 $c_u(g)$：

$$
Z_u=\sum_gc_u(g)
$$

item 类目集合为 $C_i$：

$$
S_{cat}(u,i)
=
\frac{\sum_{g\in C_i}c_u(g)}{\max(1,Z_u)}
$$

### 2.3 共现图

每个用户最多取最近 $120$ 个去重历史 item 建立共现计数 $co(s,i)$。seed 权重为 $w_s$：

$$
S_{graph}(u,i)
=
\min\left(
1,
\frac{\sum_sw_s\,co(s,i)}
{\max(1,\sum_sw_s)}
\right)
$$

### 2.4 novelty 与 exploration

归一化热度为 $P_i$：

$$
S_{novelty}(i)=1-P_i
$$

`stable_fresh` 使用 user 与 item 的稳定 hash $h(u,i)\in[0,1)$：

$$
S_{explore}(u,i)
=
h(u,i)F_i
$$

同一 user 与 item 的探索信号是确定性的，不依赖运行时随机数。

### 2.5 cold-start prior

默认 `quality_freshness`：

$$
S_{cold}^{qf}(i)
=
0.45Q_i+0.35F_i+0.20P_i
$$

`discovery_prior`：

$$
S_{cold}^{discover}(i)
=
0.40Q_i+0.35F_i+0.25(1-P_i)
$$

`fresh_explore`：

$$
S_{cold}^{explore}(i)
=
0.35Q_i+0.40F_i+0.25S_{explore}(u,i)
$$

cold-start 分量只对没有历史事件的用户生效，因此 `cold_start` gene 是 independent gene。

### 2.6 推荐基础分

$$
\begin{aligned}
S_{base}(u,i)=
&0.34S_{profile}
+0.20S_{graph}
+0.10S_{cat}
+0.12Q_i\\
&+0.13F_i
+0.05P_i
+0.06S_{novelty}
+0.04S_{explore}
+0.06S_{cold}
\end{aligned}
$$

warm user 使用 $S_{cold}=0$。

### 2.7 推荐 rerank

$$
S_{rank}(i\mid A)
=
S_{base}(i)
-\lambda_{div}\max_{j\in A}sim(i,j)
$$

默认：

$$
\lambda_{div}=0.14
$$

用于解释的 fit signal：

$$
S_{fit}
=
\min\left(
1,
0.55S_{profile}+0.30S_{cat}+0.15S_{graph}
\right)
$$

### 2.8 候选集合

默认 `full_pool` 使用全部 eligible 且未看过的 item。

`evidence_union` 组合三类候选证据：

1. 共现图命中的 item
2. 用户偏好类目命中的 item
3. 用户向量语义相似度较高的 item

当候选不足时，再按 item quality、freshness 和 popularity 的固定组合补足。

---

## 3. 离线指标与 guardrail

### 3.1 Search relevance

相关集合为 $Rel_q$，前 $K$ 个结果为 $Rank_q^K$：

$$
Recall@K
=
\frac{|Rank_q^K\cap Rel_q|}{|Rel_q|}
$$

第一个相关结果的位置为 $r_q$：

$$
RR@K
=
\begin{cases}
\frac{1}{r_q},&r_q\le K\\
0,&\text{otherwise}
\end{cases}
$$

MRR 是 query 上的平均 RR。

binary relevance 下：

$$
DCG@K
=
\sum_{r=1}^{K}\frac{rel_r}{\log_2(r+1)}
$$

$$
NDCG@K
=
\frac{DCG@K}{\max(\epsilon,IDCG@K)}
$$

当前 search proxy quality 使用 query 上的平均 NDCG。

### 3.2 Recommendation coverage

审计用户曝光过的去重 item 集为 $E$，eligible item 集为 $I_{eligible}$：

$$
Coverage
=
\frac{|E|}{|I_{eligible}|}
$$

### 3.3 Recommendation diversity

slate 中所有 category occurrence 为 $G$，去重类目集合为 $U(G)$：

$$
Diversity
=
\frac{|U(G)|}{\max(1,|G|)}
$$

### 3.4 Freshness 与 novelty

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

### 3.5 Cold-start probe

冷用户 slate 的 item quality、freshness、novelty 和 category diversity 分别取平均或集合统计：

$$
Q_{cold}
=
0.35\overline Q
+0.25\overline F
+0.20\overline N
+0.20D
$$

### 3.6 Recommendation proxy quality

没有 production reward 时：

$$
Q_{proxy}^{rec}
=
0.41C
+0.23D
+0.18F
+0.08N
+0.10Q_{cold}
$$

proxy quality 只用于离线 guardrail 和候选搜索，不被解释成 CTR、CVR、GMV、watch time 或 retention。

---

## 4. 生产业务回放

生产价值由 `RewardSpec` 和 `ExposureEvent` 定义。

### 4.1 event reward

事件类型为 $e$，业务权重为 $w_e$，事件数值为 $v_e$：

$$
r_e=w_ev_e
$$

`RewardSpec` 不属于 strategy genome，optimizer 不修改业务目标。

### 4.2 capped inverse propensity

有 propensity $p_e$ 时：

$$
a_e
=
\min\left(c,\frac{1}{p_e}\right)
$$

默认：

$$
c=20
$$

没有 propensity 时 $a_e=1$。

### 4.3 rank discount

候选策略把 event 对应 item 排到位置 $k$ 时：

$$
d(k)=\frac{1}{\log_2(k+1)}
$$

未进入候选 ranking 的 event 不产生 numerator 贡献。

### 4.4 request reward mass

每条有效 reward event 的绝对质量：

$$
m_e=|r_e|a_e
$$

request 总 reward mass：

$$
M_r=\sum_{e\in r}m_e
$$

候选 ranking 实际覆盖到的 reward mass：

$$
M_r^{ranked}
=
\sum_{e\in r:item_e\in ranking}m_e
$$

### 4.5 request policy score

$$
V_r
=
\frac{
\sum_{e\in r:item_e\in ranking}r_ea_ed(rank_e)
}{M_r}
$$

如果 $M_r=0$，request 不进入最终均值。

### 4.6 reward coverage

$$
Coverage_r^{reward}
=
\frac{M_r^{ranked}}{M_r}
$$

整体业务 reward：

$$
V
=
\frac{1}{|R|}\sum_{r\in R}V_r
$$

整体 reward coverage 是 request coverage 的均值。

当前 estimator 名称明确为：

```text
logged_replay
propensity_weighted_logged_replay
```

当前实现不是完整无偏 OPE。没有历史曝光的 item 没有真实 outcome，后续可以增加 IPS、SNIPS 和 Doubly Robust estimator。

---

## 5. Temporal request holdout

生产 events 先按 `request_id` 分组，一个 request identity 不允许跨 discovery 和 holdout。

每个 request 的时间定义为该 request 内最大 timestamp：

$$
t_r=\max_{e\in r}t_e
$$

按 $t_r$ 升序排列 request。默认 holdout fraction：

$$
f=0.25
$$

request 数量为 $n$ 时，代码使用：

$$
h
=
\max\left(
1,
\min\left(n-2,\operatorname{round}(nf)\right)
\right)
$$

最后 $h$ 个 request 组成 future holdout，其余 request 组成 discovery。

当 request 数量少于 `minimum_requests=4` 时，不构造 temporal holdout。

这套 business temporal holdout 与 Search relevance label holdout、Recommendation warm-user holdout 是独立证据来源。

---

## 6. Paired bootstrap

只比较 reference 与 candidate 都存在 score 的共同 request。

共同 request 集为 $R_c$：

$$
\delta_r
=
V_r^{candidate}-V_r^{reference}
$$

观测均值：

$$
\widehat\Delta
=
\frac{1}{|R_c|}\sum_{r\in R_c}\delta_r
$$

每次 bootstrap 从 $R_c$ 有放回抽取 $|R_c|$ 个 request，得到：

$$
\widehat\Delta^{(b)}
=
\frac{1}{|R_c|}
\sum_{j=1}^{|R_c|}
\delta_{r_j^{(b)}}
$$

默认迭代数为 $600$，实际最少执行 $100$ 次。

95% interval 取 bootstrap draw 的经验 2.5% 和 97.5% 分位点。

正向概率：

$$
P_{+}
=
\frac{1}{B}
\sum_{b=1}^{B}
\mathbf 1\left[\widehat\Delta^{(b)}>0\right]
$$

bootstrap seed 由共同 request 的 reference 和 candidate score 稳定哈希得到，因此同一证据快照的结果可复现。

---

## 7. 策略进化

### 7.1 Mixed Strategy Genome

策略空间包含两类 gene。

| 类型 | 例子 | 约束 |
| --- | --- | --- |
| continuous | lexical、semantic、freshness、diversity | 有上下界和 relative step |
| capability | query strategy、candidate strategy、rerank strategy | 只能选择 CapabilityRegistry 已注册实现 |

Search ranking blend 和 Recommendation warm ranking blend 属于受质量约束的 blend group。diversity、cold-start 等按 independent gene 处理。

### 7.2 blend group 投影

某个 blend group 的初始总质量为：

$$
T_g=\sum_{j\in g}x_j^{base}
$$

mutation 后每个 gene 先裁剪：

$$
x_j'
=
\min(u_j,\max(l_j,x_j))
$$

然后按剩余 capacity 重新分配差额，使：

$$
\sum_{j\in g}x_j'=T_g
$$

这样单个 ranking gene 变化不会无意改变整个 blend 的总尺度。

### 7.3 连续 gene 步长

设 gene 当前绝对值为 $|x|$，范围为 $u-l$，relative step 为 $\rho$：

$$
s_{floor}
=
\max(0.008,0.018(u-l))
$$

$$
s(x)
=
\max(s_{floor},|x|\rho)\max(0.25,scale)
$$

局部邻域包含该 gene 的上调和下调候选。

### 7.4 Response surface

每个 typed neighbor 都在 discovery evidence 上真实执行并得到 objective。

base objective 为 $J_0$，邻域 candidate objective 为 $J_c$：

$$
\Delta J=J_c-J_0
$$

局部信号：

$$
L
=
\operatorname{clip}
\left(
0.5+\frac{\Delta J}{0.04},
0.05,
0.95
\right)
$$

### 7.5 历史策略 Beta prior

每个 arm 初始：

$$
\alpha=1,\qquad\beta=1
$$

trusted 或 active strategy memory 会给对应 arm 增加成功质量。memory wins 被限制在：

$$
1\le wins\le6
$$

连续 gene 如果可信历史策略相对 base 明显上调，则上调 arm 增加 alpha，下调 arm 增加 beta。capability gene 对已验证 choice 增加 alpha，并对同组其他 choice 增加部分 beta。

posterior sample：

$$
\theta\sim Beta(\alpha,\beta)
$$

prior mean：

$$
\mu_{prior}=\frac{\alpha}{\alpha+\beta}
$$

### 7.6 routing score

局部 objective signal 与 posterior sample 混合：

$$
S_{route}
=
0.74L+0.26\theta
$$

response surface 先按 $S_{route}$ 排序，再用于构造初始 population。

### 7.7 population 与 quality-diversity archive

当前上限：

```text
MAX_GENERATIONS          2
POPULATION_SIZE          10
MAX_EVOLUTION_DIMENSIONS 24
MAX_EVOLUTION_SAMPLES    36
```

初始 population 由以下候选组成：

1. response surface 高分候选
2. 正向局部 gene 的组合候选
3. trusted strategy memory
4. schema-constrained mutation

如果局部最优增益低于：

$$
0.001
$$

mutation scale 会扩大，用于跳出当前 basin。

quality-diversity archive 使用相对 base 的 mutation signature 分桶。每个 signature 只保留 objective 最好的 candidate，从而避免 population 被同一种局部修改占满。

---

## 8. Objective 与 robustness

### 8.1 Search robustness

对相同 query 比较 candidate 与 reference 的 NDCG：

$$
\Delta_q=NDCG_q^{candidate}-NDCG_q^{reference}
$$

明显变差比例：

$$
worse\_share
=
\frac{\sum_q\mathbf 1[\Delta_q<-0.02]}{|Q|}
$$

最坏变化：

$$
worst\_delta=\min_q\Delta_q
$$

Search proxy objective：

$$
J_{search}
=
Q
+0.08R
-0.035W
+0.015\min(0,D_{worst})
$$

其中 $Q$ 为 NDCG proxy quality，$R$ 为 Recall，$W$ 为 worse share。

### 8.2 Recommendation robustness

单用户 utility：

$$
U_u
=
0.55D_u+0.45F_u
$$

用户变化：

$$
\Delta_u=U_u^{candidate}-U_u^{reference}
$$

明显变差阈值为：

$$
\Delta_u<-0.03
$$

Recommendation proxy objective：

$$
J_{rec}
=
Q
+0.05F
+0.03D
+0.04Q_{cold}
-0.03W
+0.01\min(0,D_{worst})
$$

### 8.3 Production objective

有 production reward 时，业务 reward 是主要 routing signal，domain metric 保留为 guardrail 和 tie-break signal。

Search：

$$
J_{search}^{business}
=
0.82V+0.18J_{search}
$$

Recommendation：

$$
J_{rec}^{business}
=
0.82V+0.18J_{rec}
$$

$V$ 为 logged replay business reward。

---

## 9. Safety 与 trust gate

### 9.1 Search proxy gate

Search candidate 至少需要 $3$ 个 relevance evidence sample。

核心安全条件：

$$
\Delta NDCG\ge-0.002
$$

$$
\Delta Recall\ge-0.01
$$

$$
worse\_share\le0.34
$$

$$
worst\_delta\ge-0.35
$$

有独立 relevance holdout 时还要求：

$$
\Delta NDCG_{holdout}\ge-0.005
$$

$$
\Delta Recall_{holdout}\ge-0.02
$$

trusted 还要求独立 holdout 存在，discovery objective delta 至少为 $0.001$，并且 quality 或 recall 有正向变化。

### 9.2 Recommendation proxy gate

至少需要 $3$ 个 warm user evidence sample。

核心条件：

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
worse\_share\le0.40
$$

$$
worst\_delta\ge-0.30
$$

独立 holdout 还检查 quality、coverage 和 cold-start regression。

### 9.3 Production trust gate

有 business replay 时，proxy gate 仍负责体验安全，business evidence 决定 durable trust。

公共 trust 条件包含：

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
P_{+}\ge0.65
$$

另外要求 paired future request：

$$
N_{paired}\ge2
$$

并且 domain guardrail holdout 必须独立存在。

因此一个 future request 不会成为 durable activation certificate。

---

## 10. Segment-conditioned portfolio

全局策略确定一个已验证 basin 后，生产路径还会按 request segment 在该 basin 周围构建局部候选。

segment candidate pool 包含：

1. 当前 global config
2. global evolution 选出的 seed config
3. seed 周围各 typed dimension 的局部 neighbor

Search segment 最低业务证据：

$$
N_{discovery}\ge3
$$

$$
N_{holdout}\ge2
$$

同时需要 segment 对应 relevance label 足够支持 guardrail。

局部策略 trust 继续要求 discovery reward 改善、future holdout 不回退、paired confidence 达标，以及 domain guardrail 安全。

证据不足的 segment 不强行生成独立策略，直接使用全局 trusted strategy。

---

# Agent Harness

## Capability-driven MissionCompiler

运行时 capability contract 声明：

```text
name
requirement_key
label
domain
priority
requires
information_gain
cost
order
initial_status
hypotheses
```

`MissionCompiler` 不维护搜索复现、推荐审计等 tool-name 分支，而是按 capability contract 编译 Evidence DAG。

编译规则：

1. 根据 `AgentPlan` 过滤当前可参与的 capability
2. 按 `requirement_key` 分组
3. 同一 requirement 的 capability 按 information gain、cost、order 和 name 排序
4. 选择主 capability，同时保留可替换实现列表
5. 解析 requirement dependency closure
6. 缺失依赖的 requirement 标记为 blocked
7. 把 capability 声明的 hypothesis 合并进 Mission Graph

运行时由 Deliberation、Harness 和 Verifier 共同维护 requirement 状态、观察、冲突和关闭条件。

核心退出条件包括：

- critical 和 high evidence requirement 已进入 terminal 状态
- material contradiction 已调查
- tool 与 permission budget 未越界
- learning 在 trust 前完成独立验证

---

# 策略生命周期

策略状态使用明确条件表达，不使用单一路径描述。

| 状态 | 进入条件 | 主要证据 |
| --- | --- | --- |
| candidate | optimizer 产生可评估配置 | discovery report |
| safe to try | domain regression gate 通过 | relevance 或 recommendation guardrail |
| trusted | 独立 holdout 与业务证据满足 trust gate | temporal holdout、paired confidence |
| active | trusted 且用户显式授权 | authority record |
| retired | 业务或 guardrail revalidation 失败 | regression evidence |

当 production reward 存在时，strategy memory 优先保存 business reward。business regression 会在 proxy validation 之前检查，`RewardSpec` 不进入 optimizer gene space。

---

# Existing system integration

序枢内建 Search 和 Recommendation 是 reference implementation，不要求企业迁移现有 serving stack。

```python
from lingjing_harness import AdapterSearchEngine, CallableSearchAdapter, RewardSpec
from lingjing_harness.production import evaluate_logged_policy

adapter = CallableSearchAdapter(
    lambda query, limit: my_search_service(query=query, limit=limit)
)
engine = AdapterSearchEngine(adapter)

report = evaluate_logged_policy(
    events,
    surface="search",
    reward_spec=RewardSpec(weights={"click": 1, "purchase": 5}),
    search_engine=engine,
)
```

external adapter 会执行：

- 结果统一为 `id`
- 去重
- 拒绝非有限 score
- 保持确定排名
- 接入同一套 logged replay 与 validation

当前 external adapter 是 read-only evaluation contract。自动修改外部 production policy 仍要求接入方提供显式 typed write 或 experiment contract。

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

浏览器打开：

```text
http://127.0.0.1:8765
```

CLI：

```bash
xushu-harness 做一次全局体检
```

开发验证：

```bash
python -m pip install -e ".[dev]"
pytest -q
python scripts/probe_harness_contract.py
```

`序枢 / Xushu` 是产品品牌；`lingjing_harness` Python namespace 和已有 `LINGJING_*` 环境变量保留为兼容接口。

---

# 生产数据契约

完整格式见 [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)。

最小 production value 数据：

```json
{
  "items": [
    {"id": "sku-1", "title": "商品 A"}
  ],
  "reward_spec": {
    "weights": {
      "click": 1,
      "purchase": 5,
      "hide": -2
    }
  },
  "events": [
    {
      "request_id": "r-1",
      "timestamp": 100,
      "surface": "recommend",
      "user_id": "u-1",
      "item_id": "sku-1",
      "event": "click",
      "position": 1,
      "propensity": 0.5,
      "policy_id": "prod-v1"
    }
  ]
}
```

`ExposureEvent` 核心字段：

```text
request_id
timestamp
surface
item_id
event
value
propensity
position
user_id
query
policy_id
model_version
experiment_id
metadata
```

`interactions` 用于用户画像和图结构，`events` 用于生产 request replay。两者语义不同。

`Catalog.summary()` 会报告：

```text
production_events
production_requests
search_replay_requests
recommend_replay_requests
business_reward_ready
```

---

# 系统架构

系统按职责分成五个平面。

| 平面 | 职责 |
| --- | --- |
| Runtime | Goal、authority、Mission Graph、Deliberation、Harness、Verifier |
| Domain | Search、Recommendation、query/user context、capability execution |
| Evidence | observations、attachments、network evidence、production events |
| Evolution | mixed genome、response surface、posterior routing、population、QD archive |
| Trust | domain guardrail、temporal holdout、paired confidence、activation、revalidation |

持久化层保存 conversation、run、checkpoint、worker lease、workspace revision 和 typed strategy memory。

进一步文档：

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/HARNESS_CONTRACT.md`](docs/HARNESS_CONTRACT.md)
- [`docs/VERTICAL_EVOLUTION.md`](docs/VERTICAL_EVOLUTION.md)
- [`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md)

---

# 可靠性边界

已有工程约束包括：

- Mission Graph 与 checkpoint resume
- adaptive invocation idempotency
- strategy schema canonicalization
- cold-start independent probe
- query、user、request identity isolation
- SQLite worker lease、heartbeat 和 fencing
- workspace revision
- production auth、rate limit 和 CSP
- attachment TTL 与 evidence retention
- network permission isolation
- stale worker 不得覆盖当前状态

生产价值相关 invariants：

```text
business reward != proxy quality
request identity cannot cross temporal split
one future request cannot certify durable trust
business regression can retire active strategy
proxy validation cannot hide business regression
RewardSpec cannot be evolved by optimizer
```

---

# 当前边界

当前仍未实现或仍需外部系统配合的能力：

1. 完整 OPE estimator suite，包括 IPS、SNIPS 和 Doubly Robust
2. Online experiment adapter，包括 A/B、interleaving、canary 和 traffic allocation
3. External typed write contract，用于安全发布外部 serving policy
4. Latency 与 infra cost Pareto objective，用于联合优化质量、业务收益、P99 和成本
5. 更丰富的 production causal evidence，降低 logged replay 的观测偏差

这些能力不会在 README 中提前描述成已完成。

---

# Repository map

```text
frontend/                          序枢产品 UI
lingjing_harness/
  production.py                    RewardSpec / ExposureEvent / temporal replay / bootstrap
  adapters.py                      existing-system read-only serving adapters
  domain.py                        Catalog + training data + production evidence
  algorithms/
    search.py                      Search mixed genome + execution stages
    recommend_core.py              Rec mixed genome + cold-start / exploration / rerank
    capabilities.py                typed capability registry + config validation
    evaluation.py                  domain guardrails + business reward reporting
    evolution_core.py              response surface / posterior / QD primitives
    production_evolution.py        business-routed evolution
    segment_evolution.py           request-segment local strategy portfolio
    segment_credit.py              segment strategy attachment and credit
    evolution.py                   stable public evolution surface
  runtime/
    capabilities.py                declarative runtime capability contracts
    mission_compiler.py            capability-driven Evidence DAG compiler
    harness.py                     durable Agent Harness loop
    deliberation.py                evidence reasoning / reflection / critic
    verifier.py                    result / authority verification
    memory.py                      episodic / procedural / policy memory
    tools.py                       stable ToolRegistry surface
  api.py                           API / auth / workspace / recovery
  store.py                         runs / leases / revisions / shared rate limit
```

---

# 质量门槛

```bash
make check
make test
make demo
python scripts/probe_harness_contract.py
```

CI 验证范围包括：

- Python compile 和 full pytest
- MissionCompiler、Deliberation 与 Harness contract
- mixed genome 与 capability stages
- production reward、temporal request split 与 bootstrap
- segment portfolio 与 strategy credit
- malformed external adapter output
- strategy lifecycle、recovery 与 fencing
- CLI 和 wheel clean install
- frontend syntax 与 product hygiene
- desktop 和 mobile product browser flow
