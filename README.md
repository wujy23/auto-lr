# Codex-Auto-LR

在 Codex 桌面应用里，用参考图或文字要求驱动 Lightroom Classic 修图。

Codex 负责理解需求和展示结果；本地 Python 程序负责调用模型规划、执行 Lightroom 调整、回读校验、独立评审和导出。初次调色后最多纠偏两轮，无需逐轮确认。

当前为 **macOS 测试版**。源码包不附带照片、参考素材或历史任务；请使用自己的本地图片。暂未添加许可证，发布前另行决定授权方式。

## 使用条件

- 在运行 Lightroom Classic 的同一台 Mac 上使用 Codex 桌面应用，打开**本地项目文件夹**。
- Python **3.10 或以上**；项目仅用 Python 标准库，无需 `pip install`。没有 Python 时，可从 [Python 官网](https://www.python.org/downloads/macos/) 安装，再让 Codex 检查解释器版本。
- 可运行、已登录的 Codex CLI。程序优先查找 PATH，也会查找 Codex/ChatGPT 应用内置 CLI；不要求重复安装已有可用 CLI。
- Lightroom Classic 已安装且能读取你的照片格式。

当前实际检查环境：Python 3.14.0、Codex CLI 0.160.0、Lightroom Classic 15.1。代码要求 Python 3.10+；其他版本组合需以环境诊断和真实试用为准。Windows 尚未支持。

## 第一次使用

### 1. 下载并打开

从 GitHub 下载源码 ZIP（或 Release 中的版本 ZIP），解压后把项目放在一个准备长期保留的位置，例如 `~/Pictures/Codex-Auto-LR/`。不要直接在压缩包、临时目录或每次变化的下载文件夹里运行。

在 Codex 桌面应用中添加或打开这个文件夹。选到能直接看到 `README.md`、`AGENTS.md` 和 `harness.py` 的那一级。不要只上传文件夹内的某个文件。

### 2. 让 Codex 安装并检查

新建项目聊天，发送：

> 请阅读本项目的 AGENTS.md，使用 lightroom-setup Skill 安装并检查 Codex-Auto-LR。先检查 Python 和 Codex，再安装 Lightroom 插件，告诉我需要手动完成的 Lightroom 操作。

项目自带 `.agents/skills/lightroom-setup` 和 `.agents/skills/lightroom-edit`，无需另行全局安装。如果技能没有出现，让 Codex 读取对应 `SKILL.md`，或重新打开应用。[官方本地技能说明](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)

首次诊断报告插件未安装、连接未启动是预期情况，继续安装即可。Python 缺失、CLI 无法启动或登录失败时，先解决对应问题。安装可能请求向用户 Lightroom 插件目录写入的系统授权。

### 3. 在 Lightroom 中连接

安装后重启 Lightroom Classic。如插件未出现，在「文件 → 增效工具管理器 → 添加」选择安装脚本输出的 `LightroomLink.lrplugin` 路径，通常位于：

```text
~/Library/Application Support/Adobe/Lightroom/Modules/LightroomLink.lrplugin
```

停用以前手动加载的重复副本。然后手动选择「图库 → 增效工具额外信息 → Start connection」。插件空闲 30 分钟后会停止，再次使用时重新 Start。

回到 Codex 发送：

> 我已启动 Lightroom 连接，请运行 doctor 和首次 model smoke 检查。

`doctor` 只检查环境；`doctor --smoke-model` 通过实际 Python runner 发一个很小的模型请求，不发送照片，但消耗少量模型额度。首次使用必须区分“登录存在”和“模型真正可调用”。

### 4. 开始修图

> 使用 lightroom-edit Skill 修图。原图路径是「/本机路径/原图.NEF」，参考图路径是「/本机路径/参考.jpg」，要求是「参考图的光影和色彩，保留原图细节」。直接执行。

也可以只给原图和文字要求，并明确不使用参考图。原图支持常见 RAW、JPEG 和 PNG；参考支持 JPEG、PNG 和 WebP。聊天附件如果只有预览，仍需提供原始照片的本地路径。

处理期间不要手动调整同一张照片，一次只运行一个修图任务。图片预览与参考会传给模型分析，原图由本机 Lightroom 处理。模型调用使用本机 Codex 登录和对应额度；聊天窗口选择的模型不会自动传给内部调用，可在需求中明确指定。

## 结果在哪里

- `exports/`：质检通过后导出的全尺寸 sRGB JPEG。
- `runs/任务目录/`：初始预览、每轮修后预览、评审和运行状态。
- `complete`：通过质检且正式导出完成。
- `limit_reached`：两轮纠偏后仍有差异，正常结束，保留当前编辑和预览，但不生成正式导出。
- `write_uncertain` / `applying`：可能已经写入，先查询实际状态，不能重放旧任务。

## 当前支持的编辑

| 范围 | 内容 |
|---|---|
| 基础影调 | 白平衡、曝光、对比度、高光、阴影、白色、黑色、鲜艳度、饱和度 |
| 曲线和色彩 | RGB 点曲线、参数曲线、八色 HSL、色彩分级、相机校准 |
| 质感和效果 | 纹理、清晰度、去朦胧、锐化、传统降噪、颗粒、暗角 |
| 智能蒙版 | Lightroom 原生 AI 主体、天空、背景选取 |
| 局部调整 | 线性/径向渐变，以及已有蒙版的局部数值调整 |

精细人物部位、对象和范围选区需先在 Lightroom 建立；裁切、透视、配置文件切换、生成式移除、AI 降噪和镜头虚化尚未自动化。具体可用项由当前照片和 Lightroom 能力决定。

## 更新与常见问题

更新请看 [更新指南](docs/UPDATING.md)，区分 Download ZIP 和 Git。固定项目位置可以避免插件仍绑定旧目录。无法连接、CLI 登录或权限问题见 [故障排查](docs/TROUBLESHOOTING.md)。

需要手动运行时，公开入口如下（在项目目录执行）：

```bash
python3 -B harness.py doctor
python3 -B harness.py setup --dry-run
python3 -B harness.py setup
python3 -B harness.py doctor --smoke-model
python3 -B harness.py run --target '/路径/原图.NEF' --reference '/路径/参考.jpg' --style '自然温暖，保留细节'
python3 -B harness.py status --run-dir '/路径/运行目录'
```

只按文字修图时，用 `--no-references` 替代 `--reference`。日常操作通过 `harness.py`，不用调用内部引擎或桥接脚本。

## 开发与发布

- [内部引擎、离线测试与私人实验脚本](docs/DEVELOPMENT.md)
- [源码打包与发布检查](docs/RELEASING.md)
- [另一台 Mac 的首次使用验收](docs/FIRST_RUN_CHECKLIST.md)
- [版本记录](CHANGELOG.md)

当前目录中的批量实验脚本依赖作者的私人素材，不是安装步骤，也不附带测试照片。项目本地验证不等于已通过另一台 Mac 的真实首次安装验收。
