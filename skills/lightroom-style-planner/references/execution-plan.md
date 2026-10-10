# LR 执行计划字段规范

适用于本工作区 `lr_link.py apply --photo-id <ID> --plan <文件>`。运行时检查 `status` 中 `applyNumeric` 与 `applyStructured`；能力缺失时加载对应插件，不把文件存在当作运行中插件已更新。

## 1. 基线和执行字段

顶层 `photo_id` 为 inspect 返回的字符串，`baseline_token` 为最新 settings 回读的 32 位小写十六进制令牌。令牌绑定会话、目录、主选照片与完整显影设置；重载或成功写入后不能复用。至少提供一项真实调整。

| 字段 | 实际执行内容 |
|---|---|
| `settings` | 至多 128 个全局数值字段，键名和值域以 settings 返回的 numericRanges 为准；都是目标绝对值 |
| `curves` | 字段到完整点列的映射，支持下面列出的四条点曲线 |
| `masks` | 至多 8 项几何或原生 AI 蒙版创建/更新，或 existing 蒙版调整；保持其他调整组和已有精细选区 |
| `visual_analysis`、`edit_hypotheses`、`parameter_review`、`mask_plan`、`visual_review`、风格说明等 | 仅为分析与检查记录，不由执行器生成或分派操作；`visual_review` 在查看结果后填写 |

`numericRanges` 针对当前照片返回；插件 0.7 通过 LrDevelopController.getRange 查询扩展字段，features.numericRangeSources 记录来源。RAW 色温 Temperature/Tint 与 JPG/PNG 相对色温 IncrementalTemperature/IncrementalTint 不混用；只传入本图提供的字段。颗粒、锐化降噪、完整色彩分级、校准、暗角、参数曲线可用时均能参与规划。参数曲线三个分割点必须保持递增。写色温/色调会同时进入 Custom 白平衡。

## 2. 曲线

允许的键：`ToneCurvePV2012`、`ToneCurvePV2012Red`、`ToneCurvePV2012Green`、`ToneCurvePV2012Blue`。每条值为 2–16 个 `[x,y]` 点，坐标 0–255，x 严格递增、y 不递减。写入时设置曲线名称为 Custom；完整替换所指定曲线，不是叠加。未指定的曲线保持当前。

示例结构：`"curves": {"ToneCurvePV2012": [[0,0],[64,60],[128,128],[192,196],[255,255]]}`。点值只是格式示例，不是默认风格。

## 3. 几何蒙版

每项包含：

- `id`：新建时生成一次 UUID 的 32 位小写十六进制表示，后续更新沿用。LR CorrectionID 为同一 UUID 的大写带连字符格式。
- `name`：小写字母开头，仅小写字母、数字、连字符，最长 64 字符；LR 中名称为 `Codex <name>`。
- `type`：`linear` 或 `radial`。
- `geometry`：下表数组。
- `settings`：非空的局部参数映射，数值使用下节单位。

| 类型 | geometry 格式 | 含义 |
|---|---|---|
| linear | `[fullX,fullY,zeroX,zeroY]` | 完整作用端到零作用端的渐变；两点不能重合 |
| radial | `[left,top,right,bottom,feather,inside]` | 椭圆外框，羽化 0–100；inside 为 1 表示内部，0 表示外部；不支持旋转角 |

坐标以未裁切原图左上为原点，相对原图宽高，x 向右、y 向下；坐标范围 −2 至 3，允许明确设计的画外延伸。接口仅接受 Orientation=AB 且 CropAngle=0。裁切画面中的位置须先按实际 CropLeft/Top/Right/Bottom 换算，不把裁后坐标直接写入原图坐标。

更新时同 ID 必须对应同名、本工具创建的单组件调整组，并保持类型一致；指定几何替换原几何，指定局部值覆盖对应旧值，其他局部值保留。不能用新 ID 表达重试，也不能用该入口覆盖任意用户蒙版。

## 原生 AI 与已有精细蒙版

多人场景先执行[多人蒙版限制](mask-design.md#多人照片避免新建单人或部分人物选区)，不得输出新建指定单人/部分人物的计划；可复用覆盖已确认的已有选区。实验入口不属于正式创建能力。

新建 type=subject / sky / background 时 geometry=[]，id 留空让 Python 分配请求标识；必须在本图 features.aiMaskCreation 中。插件调用 createNewMask('aiSelection', type)，让 Lightroom 计算选区，保留原生 CorrectionID 和全部组件。Python 从实际响应登记 ID；下一轮必须用回读 ID，不沿用请求 ID 猜测新建。

已有人物、皮肤、头发、画笔、范围交集等可以 type=existing，使用 editable_masks 中准确的 id 和合成 name，geometry=[]。仅改变组的局部数值，保留用户原名称和所有选区组件。不得新增 existing、不更改或重画其选区。完整用户名称和组件仍可从 baseline 中读取。

精细人物/部位、对象框选、风景分类和范围采样仍需先在 Lightroom 交互创建；不把自动主体检测当成皮肤/头发分割。AI 操作超时或中途失败可能留下中间蒙版，执行器停止且不自动重放。SDK 返回稳定选区结构和当前选中 ID 不等于像素级无误，须检查实际预览。

## 4. 局部参数单位

| 输入键 | 输入范围 | LR 存储字段与换算 |
|---|---|---|
| ExposureEV | −4 至 +4 EV | LocalExposure2012 = 输入 / 4 |
| Contrast、Highlights、Shadows、Whites、Blacks | −100 至 +100 | 对应 LocalContrast2012 等字段 = 输入 / 100 |
| Temperature、Tint、Saturation、Texture、Dehaze | −100 至 +100 | 对应 LocalTemperature 等字段 = 输入 / 100 |
| Sharpness、LuminanceNoise、Moire、Defringe | −100 至 +100 | 对应 LocalSharpness 等字段 = 输入 / 100 |
| Clarity | −100 至 +100 | LocalClarity2012 = 输入 / 100 |

局部 Temperature 是相对滑块点数，不是 Kelvin。不要把全局曝光范围或全局色温单位套到局部字段。主体/天空/背景由原生 API 创建；其他类型可作为 existing 更新数值，不能在本入口自动绘制、采样或组合。

## 5. 执行与结果核对

服务端先完成字段、范围、几何及完整基线检查，再备份、创建 LR 快照和写入；客户端成功退出仍需检查响应 `valuesMatch` 与每项 `maskResults.verified`。写入失败或超时不默认原子回滚，先读实际状态。

一轮结束后导出并查看 LR 预览，再核对保存下来的曲线、局部数值与选区几何，以及裁切、配置文件等保护项。LR 可能在渲染后规范化结构，例如省略默认直线局部 MainCurve、把渐变参考点移至中点、补全空结构或 As Shot 数值；须逐项区分辅助字段规范化与实际调整改变，不因结构不完全相等盲目重写，也不忽略实质参数变化。

当前接口不返回逐像素蒙版覆盖图。回读坐标与查看合成预览可验证基本覆盖和过渡，不能宣称完成了像素级选区验证。发现明显光晕、内外方向错误或覆盖偏差时，按新基线集中纠偏后再检查。
