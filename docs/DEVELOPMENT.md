# 开发与内部引擎说明

文中的命令均在项目根目录执行。

以下是内部引擎与高级开发说明。日常聊天修图使用上面的公开入口。

实现路径：扫描素材库 → 在 LR 中导入/选择目标照片 → 读取基线及预览 → 制定独立视觉目标 → 自动固定 goal.json → 分析 agent 统一设计全局与蒙版 → Python 校验 → 桥接写入 → 回读与预览 → 独立质检 → 通过后导出全尺寸 JPEG，或纠偏/停止。

`harness.py` 是公开入口，`main.py` 是内部引擎，不是伪代码。依赖 Python 3.10+ 标准库、本机 Codex CLI、项目内的 `lr_link.py` 与正在运行的 Lightroom 插件。目标制定、参数规划和质检通过分阶段、独立的 `codex exec` 调用实现（两个角色，规划角色分两个阶段）；Python 通过 stdin 传入方法与 JSON，通过 `--image` 传图，通过结构化输出文件接收结果。没有 master agent，也没有负责传输的第三个模型。

这里使用 [Codex 官方非交互入口](https://developers.openai.com/codex/noninteractive)，并非调用此聊天内部的 sub-agent 工具，也不依赖一个假定存在的 Python Codex SDK。CLI 复用本机登录，但仍有模型服务的网络、推理耗时与相应额度消耗。

## 使用前

1. 安装与连接统一按 [首次使用指南](../README.md) 操作，使用 `harness.py setup` 安装到用户 Modules 目录。不要再同时加载源码目录内的插件副本。
2. 将原片放在主程序旁的 `raw/`，参考图放在 `references/`。原片未入库时程序按原路径导入，已入库则复用，不移动/复制原文件，也不重置已有显影。导入可能应用 Lightroom 本身配置的 RAW 默认值。默认处理主照片；指定虚拟副本时仍需先在 LR 选择该副本并用 `--photo-id` 绑定。
3. 确保 Codex CLI 已登录。程序先从 PATH 查找 `codex`，找不到时检查系统或用户 Applications 目录中的 ChatGPT/Codex 应用内置 CLI；也可用 `--codex` 指定可执行文件。启动时先检查 CLI 路径，再读取 Lightroom。程序不读取、复制或保存登录令牌。未指定模型时采用 CLI 默认模型；不会继承当前聊天的模型选择。
4. 模型子进程会保留终端显式设置的代理；未设置时沿用 macOS 系统 HTTP/HTTPS 代理。只传给子进程，不修改系统代理设置，也不把代理凭据写入日志。运行期间不要手动调整目标照片。

所有项目依赖都按 `main.py` 所在目录定位：

- 桥接客户端：`lr_link.py`
- Lightroom 插件：`LightroomLink.lrplugin/`
- Python 与 Lua 共用的通信目录：`work/lr-link/`（启动连接时创建）
- 分析 Skill：`skills/lightroom-style-planner/SKILL.md`
- 质检 Skill：`skills/lightroom-result-reviewer/SKILL.md`

`--bridge`、`--planner-skill`、`--reviewer-skill`、`--raw-dir`、`--reference-dir`、`--export-dir` 和 `--run-dir` 的相对路径均以项目目录为基准。`--target` 和 `--reference` 优先在对应素材库内查找，其次在项目目录内查找；也接受用户明确指定的绝对路径。Python 解释器随当前运行环境选择，Codex 按上述顺序自动定位，不绑定某个用户的安装目录。

运行时发给 Lightroom/Codex 的路径，以及用于照片身份核对、恢复校验的运行记录，仍保存解析后的完整路径。历史 `runs/` 不能在搬家后直接续跑，请发起新任务；程序不会改写旧记录及其校验摘要。迁移目录不等于获得 macOS 文件访问权限，如系统拒绝访问，请为启动程序的终端及 Lightroom 授予对应文件夹权限。

## 素材库与导出目录

目录都以 **main.py 所在位置** 为基准，不随启动命令所在的工作目录变化：

```text
lightroom-harness/
├── main.py
├── lr_link.py
├── LightroomLink.lrplugin/
├── work/lr-link/  插件通信文件
├── raw/           目标照片 RAW / JPG / JPEG / PNG，支持子文件夹
├── references/    JPEG / PNG / WebP 风格参考，支持子文件夹
├── exports/       质检通过后的全尺寸 JPEG
├── runs/          各次运行的方案、预览和记录
├── skills/        分析与质检两个独立 Skill
└── tests/
```

- 内部引擎直接调用时，只有一张支持的目标照片才自动选择（公开入口始终要求明确路径）。多张时，交互终端列出编号供选择；非交互调用必须用 `--target` 或 `--all` 明确范围，不随意挑第一张。
- 默认把参考库中的全部图片作为这一轮的参考组。参考库最好只放同一目标风格；不同风格混放时，用 `--reference 文件名` 指定一张或多张。
- `--target DSC_1234.NEF` 优先在 `raw/` 查找；绝对路径也可用。参考图同理。
- RAW 常见扩展名包括 NEF、CR2/CR3、ARW、DNG、RAF、RW2、ORF 等，扩展名不区分大小写。最终是否能解码取决于当前 LR 版本。
- JPG/JPEG、PNG 也可作为 target，扩展名不区分大小写，放在同一个 `raw/` 目录即可；目录名沿用旧版以兼容现有素材。也可用 `--input-dir 图片目录`（等同 `--raw-dir`）指定其他照片库。
- 列出素材而不连接 LR：`python3 harness.py library`。输出的 `targets` 列出全部支持的目标照片；`raw` 字段为兼容旧版保留，只列 RAW。

例如处理 JPEG（PNG 同样用法）：

```bash
python3 harness.py run --target "原图.jpg" --reference "参考图.png" --style "参考图的色彩与影调"
```

所有输入格式仍通过 LR 当前设置、可写范围和实际预览进行规划与质检；输出统一为当前裁切后全尺寸 JPEG。`export.json` 使用 `source_image` 记录输入路径，旧字段 `source_raw` 保留为兼容别名。

最简运行：

```bash
python3 harness.py run --target "原图.NEF" --no-references --style "自然、克制，保留细节"
```

程序根据参考图和 `--style` 制定视觉目标并自动继续规划参数；没有参考图时按文字需求和原片制定目标。

## 无确认运行

指定原图、参考图与文字要求即可连续执行目标生成、参数规划、调色、质检和导出，不再询问用户确认。

```bash
python3 -B harness.py run --target '原图.NEF' --reference '参考图.jpg' --style '参考图的色彩与影调'
```

没有参考图时使用 `--no-references`。`--target`、参考方式和 `--style` 都需要明确提供。

如只想生成目标，可使用 `harness.py plan`，保存状态为 `goal_ready`，不执行显影。之后通过 `python3 -B harness.py continue --run-dir 'runs/任务目录'` 执行，不需要确认摘要。旧 `--approve` 参数仅作兼容：提供时仍校验，不再作为用户确认步骤。历史 `awaiting_confirmation` 且未执行的目标也可继续；已完成、失败或写入不确定的任务不能重放。

| 参数 | 作用 |
|---|---|
| `--target` | 原图路径，支持 RAW/JPG/PNG |
| `--reference` / `--no-references` | 明确指定参考图（可重复）或只按文字要求 |
| `--style` | 本轮风格要求 |
| `--run-dir` | 新任务必须使用不存在的目录 |
| `--planner-model` / `--reviewer-model` | 可选；未指定时采用 CLI 默认模型 |
| `--max-corrections 0/1/2` | 默认 2，初次处理后最多再纠偏两轮 |
| `--agent-timeout` | 单次模型调用超时，默认 900 秒 |
| `--photo-id` | 进一步绑定照片或已选中的虚拟副本 |

质检通过后，正式成片保存到 `exports/`，为全尺寸 sRGB JPEG（质量 100）。失败或未通过质检只保留预览与诊断，不冒充正式成片。

## 私人数据集实验（开发用途）

脚本 `batch_style_test.py` 是作者的回归实验工具，依赖未分发的私人 `data_set/`，不属于新用户安装或验证步骤。新用户应使用 README 的单张照片入口。该脚本只运行以下 7 组，保留原始实验编号：

| 编号 | 素材 | 风格 |
|---|---|---|
| E03 | 北外滩第三张（DSC_0217.NEF） | 下午城市 |
| E06 | 迪拜 | 黑金 |
| E10 | 法兰克福 | 黑金 |
| E16 | 玻璃饮品 | 逆光通透 |
| E20 | 酒瓶与手表 | 逆光通透 |
| E21 | 古典明暗人像 | 古典明暗 |
| E25 | 室内人像 | 古典明暗 |

```bash
# 只列清单，不连接 Lightroom（默认行为）
python3 -B batch_style_test.py --dry-run

# 执行选定的 7 组，不询问目标确认
python3 -B batch_style_test.py --run

# 只试一组：北外滩第三张 × 下午城市
python3 -B batch_style_test.py --run --only E03

# 只刷新结果对照页面
python3 -B batch_style_test.py --report
```

运行前在 Lightroom Classic 启动插件连接，确认本机 Codex CLI 已登录。脚本通过公开 `harness.py doctor` 检查环境，然后逐组调用 `plan` 和 `continue`；两步之间检查基线，无用户确认交互。运行期间不要手动调整实验副本。

默认批次目录是 `experiments/style-selected-7/`，与之前 28 组批次分开，避免旧失败状态或代码摘要影响本次运行。`--only` 可进一步筛选上述 7 组，不能加入其他实验。重新执行同一命令会校验已有成片并跳过完成项，继续尚未开始或仅完成目标生成的实验；绝不重新发送已执行或不确定的写入。达到修正次数上限（默认初次调色后再修正两轮）属于正常结束，保留未达标状态及预览，退出码为 0，自动继续下一组；再次运行会跳过该组，不重放。真正的执行失败仍停止。查明原因后，可用 `--skip-failed` 跳过已结束的失败项继续其他实验；写入不确定状态不能跳过。重做实验或换模型、纠偏次数时用新的 `--batch-dir experiments/新的批次名`。

每组复制一份独立 RAW（不复制旁车参数文件、不使用硬链接），由 Lightroom 按导入默认值创建基线。保留 RAW 本身嵌入的设置；同一素材若存在多组实验，其初始显影设置必须一致，否则在写入前停止。原数据集与原照片的 Lightroom 编辑不参与实验。副本会保留在磁盘和 Lightroom 图库中，不自动清理。

批次目录包含 `batch.json`（输入摘要和固定配置）、`results.csv`、`comparison.html`（按风格或素材筛选）、`references/`（参考图快照），以及所选各组（保留 E03、E06 等编号） 的独立输入、控制台日志和 `run/` 记录。实际模型 prompt 保存在每组 `run/goal-planner/prompt.txt` 和 `run/round-XX/planner/prompt.txt`；它们与具体素材、基线和轮次绑定，不是通用风格参数。网页链接正式成片到 `exports/`，不要只搬走批次目录而丢掉导出文件。

每张参考图固定为对应风格目录内唯一的图片，包括现有「下午城市/prompts/下午城市.jpg」。目录内图片数量变化会要求重新明确参考，不静默选择。显式指定的模型参数及方法摘要在批次创建时固定；未指定模型时仍使用 CLI 默认模型。代码或方法改变后须新建批次。全批 7 次初始修改，默认每组最多两次纠偏，耗时和模型额度以实际运行记录为准。

## 两个 agent 的输入与权限

工作流程的维护入口分别是 [分析与参数规划 Skill](../skills/lightroom-style-planner/SKILL.md) 和 [成片质检 Skill](../skills/lightroom-result-reviewer/SKILL.md)。分析 Skill 自带已研究的色彩/影调、参数、蒙版及执行字段参考；质检 Skill 自包含验收流程，不加载分析侧的整套参数推导资料。旧的综合修图 Skill 保留，主程序现在使用这里的两个角色 Skill。

这里的“调用 Skill”是 Python 显式读取该角色的 `SKILL.md` 及其链接的本地 Markdown 资料，将完整内容传入对应 Codex 调用，并在提示中标明 Skill 名称。无需把它们安装到全局目录，也不依赖 agent 临时寻找文件。工作流程已从 `main.py` 的 PLANNER/REVIEWER 提示字符串移出；Python 仍维护 schema、校验与执行权限，Skill 不替代这些程序约束。

每次新运行先将两个 Skill 分别保存到 `runs/.../skills/planner/`、`runs/.../skills/reviewer/`，由 `skill-index.json` 绑定角色与文件摘要。每次模型调用仅加载自己的快照，并保存 `skill-usage.json` 与完整 `prompt.txt`。编辑源 Skill 只影响下一次新运行，已生成目标的任务继续使用原快照；快照被修改会停止。旧版运行记录没有角色 Skill 快照时，需要重新生成方案，不在继续执行途中偷偷替换规则。

分析 agent 的 define_goal 阶段接收用户需求、初始预览、参考及当前可用控件/蒙版能力摘要，加载目标制定规范；不传入参数基线或参数配方。输出独立的视觉标准。plan 阶段才接收固定目标、当前预览、完整显影基线、可写范围、方法规范、可编辑蒙版及上一轮反馈，输出控制项检查和全局/曲线/蒙版计划。

质检 agent 是独立调用，接收任务初始预览、本轮修改前预览、实际结果、参考图和冻结的目标。不接收 planner 的参数方案及论证，也不接收过去的评审或未解决目标编号；每轮独立检查全部目标。逐条分别输出达标状态 `met/unmet/unknown` 与相比修改前的变化 `improved/unchanged/worse/unknown`。纠偏意见注明区域、严重程度和可见差距，同一问题涉及多个目标时合并描述，标准仍逐项评价。有未达目标、不确定性或保护项损伤时，Python 拒绝 `pass`；有改善仍可能未达标。

planner 同样每轮独立调用，纠偏时接收当前状态、最新评审和简短操作历史，不再传入上一轮完整方案及理由。Python 在独立评审后生成 `round-XX/review-comparison.json`，记录各目标的连续未达标次数和从达标变为未达标的情况；unknown 不计作连续未达标。某目标连续两轮 unmet 时，下轮 planner 必须重新判断控制选择、蒙版范围和调整力度；仍可沿用有效方法，但需要说明依据。历史仅包含经过回读校验的操作及评审结果，不能代替图片或降低固定目标。更新后使用新批次目录；旧任务的代码与 Skill 快照保持原样，不自动重放。

程序使用只读 sandbox、独立临时工作目录，不加载用户 CLI 配置，并关闭本机已核对的 shell、code-mode、apps、浏览器、电脑操作、子代理、图像生成、hooks 与 memories 特性。图片和方法由主程序直接传入，不需要 agent 自行操作软件。运行机的组织/系统托管配置仍可能影响 CLI；这不是独立容器或对所有未来 CLI 版本的能力隔离证明。LR 写入的正常入口只有 Python 调用的桥接客户端。

`--ignore-user-config` 会使用户配置中的自定义模型提供商等不生效；本版本面向已经验证过的本机 Codex 登录环境。如果换 CLI 版本，应先核对功能开关和 `exec` 参数，不要用关闭 sandbox 解决配置兼容问题。

## Python 强制执行的规则

- 绑定原图路径、照片 ID、目录和完整基线；写入前重读并比较。新令牌只在确认设置未变化后采用，插件仍在写入处做最后一次基线校验。
- 校验 JSON 结构、完整控制项覆盖、全局字段及值域、曲线、局部单位、蒙版身份和几何。未暴露的字段不能仅凭模型声称支持而发送。
- 新蒙版 ID 由 Python 分配；后续沿用稳定身份。几何覆盖仅更新桥接创建且类型匹配的单组件蒙版；原生 AI 使用真实 ID。已有用户精细蒙版支持只改局部数值，保持原名与覆盖。
- 独立 goal.json 自动通过摘要校验保持固定；规划和评审调用前后均核对。纠偏不能修改已固定的风格、验收目标和硬约束。无编辑必要时允许空操作，但仍质检。
- 写入响应、实际参数回读、预览和视觉评价分别保存。数值正确不等于审美通过。
- 写入之前先保存执行意图。超时/回读异常不自动重试、不假定回滚、不重复新建蒙版。
- 同一桥接上的本程序实例互斥；仍通过基线校验防止覆盖 LR 中的手工修改或其他工具的修改。

插件 0.7 扩展了常用显影参数与原生 AI 蒙版；0.8 增加安装路径绑定、加载版本诊断，并改为手动启动连接。更新后必须在 Lightroom 增效工具管理器重载已安装的插件，再启动连接。

| 功能 | 当前执行方式 |
|---|---|
| 颗粒、锐化与传统降噪、色彩分级、相机校准、暗角、参数曲线、白平衡数值 | 查询当前照片的 SDK 合法范围，提供对应数值字段；未提供的字段不发送 |
| 主体、天空、背景蒙版 | 调用 Lightroom 原生 AI 创建，等待稳定结构，登记真实 ID，再写局部参数并回读 |
| 已有皮肤、头发、指定人物、画笔与组合蒙版 | 按真实 ID 只调局部数值，保留覆盖和原名称 |
| 自动新建精细人物部位、对象框选、范围采样 | 当前需先在 Lightroom 交互完成，之后作为 existing 蒙版参与流程 |
| 裁切/透视、配置文件切换、生成式移除、AI降噪、镜头虚化 | 尚未自动化，本次不宣称支持 |

numericRanges 从仅有小范围静态白名单扩展为当前照片的 SDK 值域查询；features 记录数值来源及蒙版能力。目标制定阶段也会收到能力摘要，参数阶段接收完整设置。默认仍保护构图和场景内容。RAW Kelvin 色温与 JPG/PNG 相对色温使用各自字段，写数值时进入 Custom 白平衡。

AI 超时可能留下中间蒙版，程序保存状态并停止，不自动重试或叠加创建。程序核对结构与局部数值，视觉覆盖仍需实际预览评审；没有逐像素蒙版覆盖图。用户已有蒙版可被规划为 existing；输出只改变明确指定的局部数值。

## 记录、耗时与异常恢复

每次运行保存：

| 文件/目录 | 内容 |
|---|---|
| `config.json`、`manifest.json` | 本轮配置、输入及方法文件摘要 |
| `inputs/`、`skills/`、`skill-index.json` | 参考图、分角色 Skill 快照、角色与版本绑定 |
| `initial/` | 初始完整基线及实际 LR 预览 |
| `photo-selection.json` | 绑定的原图、照片 ID、目录及是否新导入 |
| `export.json` | 最终本地 JPEG 路径、实际像素尺寸与文件摘要 |
| `goal.json`、`goal-lock.json` | 独立固定目标与自动锁定记录 |
| `goal-planner/` | 模型制定目标时的输入、prompt、schema、输出；使用 --goal-file 时没有该调用 |
| `pending.json` | 已保存目标，或据此生成的绑定参数计划 |
| `round-XX/` | 每轮分析输入/输出、执行计划、响应、结果预览及质检；各 agent 的 skill-usage.json 标识实际加载版本 |
| `bridge/` | 每次桥接命令的 stdout/stderr，含错误与请求线索 |
| `state.json` | 当前状态、轮次、恢复点和错误信息 |
| `events.jsonl`、`summary.json` | 各阶段耗时，区分分析、LR 执行/预览、质检 |

```bash
python3 harness.py status --run-dir './runs/first-photo'
```

正常修图的 `complete` 表示应用/回读、质检与正式 JPEG 导出均完成；`export_failed` 表示导出或本地保存失败；`limit_reached` 表示仍有差异但已用完纠偏轮数，正常退出（退出码 0），不记为异常；当前编辑及预览保留，不标记质检通过、不导出正式 JPEG；`blocked` 表示质检发现阻塞；`stopped` 表示分析、导入、校验等阶段中止；`write_uncertain` 表示可能已写入，需先查实际状态。

仅 `goal_ready` 或旧版 `awaiting_confirmation` 的未执行目标支持 `continue`，无需确认。没有独立目标契约的旧运行不能继续。进程若在发送写入后被强制终止，可能停留在 `applying`，也禁止自动继续。查看 `bridge/` 的日志、迟到响应和 LR 实际状态，再决定基于当前状态开启新任务；不要重发旧计划。原桥接会创建 LR 快照/备份，但本主程序没有自动快照恢复接口。

预览前后设置不一致时会停止；这可能来自用户修改，也可能来自 LR 的设置规范化。程序不自行判定并覆盖，重新读取稳定基线后再开新任务。

## 使用明确目标，或只复评历史结果

本项目提供 `goals/golden-landscape.json`，专用于本次金黄山湖参考图，不是所有照片的默认风格。它将曝光细节、金黄植被、山体影调分离、天空与水面色彩关系分别验收；没有锁死去雾、曲线或白平衡。

```bash
python3 main.py run --target '原图.DNG' --reference '最终成品图.JPG' \
  --goal-file goals/golden-landscape.json
```

目标不合适时，复制并修改该 JSON，再开启新 run。不要直接修改旧 run 内的 goal.json；摘要不一致会停止。固定的是本次目标，参数策略允许每轮重审。已经调过某项参数，不代表对应目标已经达到；不做候选分支或 A/B 渲染。

只使用保存的预览重跑新版评审：

```bash
python3 main.py review-run \
  --source-run runs/20261004-165919-7b901b39 \
  --goal-file goals/golden-landscape.json
```

此入口不创建 Lightroom 客户端、不应用参数、不导出新成片，只调用 reviewer；仍消耗模型时间和额度。默认结果放在 `reviews/`，包含固定 goal、角色规范快照、复制的证据图、各轮 prompt/输出及 `comparison.json`。先校验源运行输入摘要、预览摘要和已保存的参数回读，完成后检查所用证据未变；源 run 保持不变。源 run 需要完整初始图、逐轮结果、参考和执行记录。

这里 `complete` 仅表示历史复评完成，不代表旧成片通过或已经改好。每轮 verdict 以 comparison.json 为准。

## 已完成的验证

运行不连接模型或 Lightroom 的行为测试：

```bash
python3 -B -m unittest discover -s tests -v
```

测试另覆盖无交互运行、批量独立副本与防重放、目标先于参数生成、目标锁定与摘要防篡改、评审上下文隔离、改善与达标分离、历史复评不调用 Lightroom 及证据变更拦截。原测试覆盖通过、两轮纠偏上限、基线变化、目标/目录不匹配、超时不重放、质检误报通过、模型改目标、非法参数/曲线/蒙版、空操作、素材库筛选与选择、批次遇错停止、导出失败与防覆盖，以及与原桥接编码器的离线兼容性。还使用本机 Adobe Lua 运行库，在模拟 SDK 对象上检查了导入/复用、目录校验、全尺寸导出配置、导出期间设置变化和 JSON 数组序列化；没有启动插件或访问真实目录。

这些验证使用合成数据，不证明模型对真实照片的视觉判断质量，也不替代一次真实 Codex + Lightroom 联调。本次开发未修改真实照片。

SDK 依据：[Adobe 开发入口](https://developer.adobe.com/lightroom-classic)；[Adobe SDK API 文档镜像：LrDevelopController](https://lrc.mcor.dev/modules/LrDevelopController.html)。函数存在仅表示接口可调用，不保证每张照片都能检测到主体或天空。
