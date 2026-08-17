# Design System — Signal Editorial Lab

## 产品气质

三个词：**精准、编辑感、正在运行**。

这不是聊天机器人皮肤，也不是指标卡片墙。用户来到这里是为了把一个搜推体验问题交给系统，然后看到：系统正在理解什么、做了什么、依据是什么、下一步是否值得继续。

视觉必须让“自主执行”变得可感知，但不能用霓虹、粒子、玻璃拟态去假装智能。

## Taste DNA：为什么这样设计

### 1. 深色顶栏不是为了“科技感”

它承担工作台的固定坐标：品牌、当前数据、图片感知与联网状态始终在同一条基线上。深色只出现在这个高稳定区域和少量功能性标记中，主体仍是暖纸色，避免整页进入常见 AI dark dashboard 套路。

### 2. 酸性黄绿只代表“当前 / 通过 / 可行动”

`#D9FF59` 不做装饰背景。它只用于当前模式、已完成步骤、关键选中状态和少量文字强调，因此用户一眼就能理解哪里“正在生效”。

### 3. 信号橙只代表“注意 / 外部 / 活动中”

`#F36B4B` 用于执行脉冲、外部证据提示、重要细节线。它不和主高亮色争夺主按钮身份。

### 4. 分隔线代替卡片墙

这个产品的信息天然具有连续关系：目标 → 执行 → 证据 → 继续动作。大量独立圆角卡片会切断关系，因此主要依靠 1px / 2px 规则线、背景层级和留白组织内容。真正需要边界的只有 composer、附件和小型状态单元。

### 5. 大标题承担“问题空间”，UI 字体承担“操作空间”

欢迎页的大字号衬线表达编辑台/研究台气质；操作、轨迹、数据全部回到高可读无衬线。两类字体角色明确，不把 display 字体滥用到按钮与表格。

## 色彩系统

- Night: `#14231E` — 顶栏、主动作、功能性深色块
- Night raised: `#20352D` — 深色层级
- Paper: `#F5F1E8` — 主工作区
- Paper secondary: `#EAE5DA` — 导航与次级背景
- Paper structural: `#DED8CA` — 进度底轨、弱分区
- Ink: `#172019` — 正文与关键规则线
- Muted: `#687169` — 次要说明
- Signal: `#D9FF59` — 当前/通过/行动
- Signal soft: `#EAFF9A` — hover 与轻量强调
- Hot: `#F36B4B` — 活动/外部/提醒
- Good: `#4E8D63` — 稳定在线状态

禁止用纯黑、纯白做大面积产品色。

## 字体

为了本地优先和离线可靠性，**UI 不请求任何远程字体**。

- Display: `Iowan Old Style / Songti SC / STSong / Noto Serif CJK SC / Source Han Serif SC / Georgia`
- UI: `Avenir Next / SF Pro Display / Segoe UI / PingFang SC / Hiragino Sans GB / Microsoft YaHei`
- Mono labels: `SFMono-Regular / Cascadia Code / Roboto Mono / Consolas`

正文基线 13–14px；关键操作不低于 11px；不再使用 8px 正文。

## 空间与密度

空间读取：**高信息密度控制台，8px 节奏，严格对齐，主任务区域留出编辑式呼吸**。

- 控件高度：40–48px
- 触控目标：关键交互至少 40px，移动端目标 44px 优先
- 组内间距 < 组件间距 < 区域间距
- 标题后的空间小于标题前的空间
- 主工作区最大阅读宽度约 900–980px
- 不使用 `padding: 16px` 复制到所有容器的机械节奏

## 页面结构

### Desktop

1. Top status rail — 数据与能力状态
2. Left mode rail — 搜索 / 推荐 / 自主优化 / 全局体检 + 历史
3. Main editorial stage — 目标、对话、执行、composer
4. Evidence rail — 执行轨迹 / 判断依据 / 工作区

### Tablet

左侧模式区变为横向任务条；证据区变成右侧抽屉，不丢失功能。

### Mobile

任务模式保留在顶栏下方；主工作区单列；证据面板通过“查看证据”打开。移动端不能因为空间不足直接删除证据能力。

## 多模态输入

Composer 是整个产品最重要的操作组件。

支持：

- 文本目标；
- 拖入文件；
- 粘贴截图；
- 图片缩略图；
- JSON / CSV / Markdown / TXT 上下文；
- 单次联网开关。

附件先出现在 composer 的 context rail，发送后继续显示在用户消息中，让用户知道这次执行究竟带了什么上下文。

## 动效

动效只用于四类信息：

1. **进入**：消息、附件、Inspector panel 轻量 opacity + transform；
2. **运行**：12px signal block + 外框脉冲；
3. **进度**：只动画 transform/width 的进度表达；
4. **响应**：hover/active 以背景、1–7px 光学位移反馈。

默认 150–280ms，使用非弹性 easing。禁止 bounce / elastic。

`prefers-reduced-motion` 必须保留状态变化，但关闭连续动画与长过渡。

## 客户文案边界

客户 UI 允许：

- 搜索体验
- 推荐体验
- 自主优化
- 全局体检
- 执行轨迹
- 判断依据
- 工作区
- 图片感知
- 联网研究
- 公开来源
- 稳定验证

客户 UI 不出现内部模型名、第三方产品名，也不出现：

- BM25
- embedding / 向量
- 召回 / 重排
- MMR
- NDCG / MRR
- 图模型
- policy model
- 具体视觉模型名称
- 具体联网后端名称

## Anti-slop checklist

每次 UI 变更至少检查：

- 无远程字体依赖；
- 无紫蓝 AI 渐变；
- 无玻璃拟态；
- 无卡片套卡片；
- 无圆角方块图标墙；
- 无所有区域相同 padding/gap；
- 无 8–9px 正文；
- 关键移动端目标可触达；
- 证据能力在移动端仍存在；
- 键盘 focus 清晰；
- 中文 IME 不误触发送；
- drag/drop 与 paste 不阻断普通文本输入；
- reduced motion 有明确降级；
- 页面不包含其他产品或模型品牌名。
