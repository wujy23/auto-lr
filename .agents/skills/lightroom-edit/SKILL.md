---
name: lightroom-edit
description: 使用本机 Python Lightroom harness 按参考图或文字要求修图，自动执行目标、查询进度并展示成片。适用于 Lightroom Classic 照片处理请求；不负责修改工具源码。
---

# 用 Python harness 修图

你是外层操作员。调用现成程序，Python 才是流程控制者。不要自己分析生成显影参数、执行质检或模拟 Python 的步骤。

## 定位和检查

本 Skill 位于 `<仓库>/.agents/skills/lightroom-edit/SKILL.md`，向上三级就是仓库根。以下 `harness.py` 均指该仓库中的绝对路径；从任何工作目录都可以调用。只使用本机执行环境。

首次在窗口中使用时运行 `python3 -B <仓库>/harness.py doctor`。需要安装或更新时读同级 `../lightroom-setup/SKILL.md`。如果模型调用尚未在当前宿主验证，可运行 `doctor --smoke-model`；它会消耗少量模型额度，不发送照片。权限拒绝时走宿主正常授权，不修改内层 sandbox、不复制凭据。

确认原图是本机可读的 RAW/JPG/PNG 文件。附件只有预览时请用户提供原图路径，不用预览代替 RAW。必要时用 `harness.py library` 列出仓库输入。参考图需为本地 JPEG/PNG/WebP，可重复传入；无参考时显式使用 `--no-references`。

## 唯一执行路径

1. 调用 `python3 -B <仓库>/harness.py run --target <原图路径> --reference <参考图路径> --style <用户要求>`。无参考时将 `--reference ...` 换成 `--no-references`。通过参数数组或正确 shell 引号原样传递路径和文字。只在用户指定时加 `--planner-model` / `--reviewer-model`；窗口的模型不会自动继承。不要调用 `main.py`。
2. Python 自动制定并固定目标，再完成参数规划、LR 执行、回读、质检、纠偏和导出，不再请求用户确认。保留目标摘要、输入和基线校验。
3. 记录输出中的 `harness_invocation.invocation_id` 和 `run_dir`。等待原命令完成；如果工具返回进程 session ID，持续等待同一个 session。没有新输出不等于失败，不重启任务。可用 `harness.py status --run-dir <目录>` 查看进度。
4. 仅当用户需要先查看目标时使用 `harness.py plan`。其状态为 `goal_ready`，可通过 `harness.py continue --run-dir <目录>` 执行，不需要 `--approve`。兼容旧版 `awaiting_confirmation` 的未执行目标；不得继续其他状态。
5. 根据 `state.json` 和 `export.json` 报告结果。仅 `complete` 且存在实际导出文件时交付成片，使用绝对路径链接/图片展示。查看成片是展示，不得自行宣布质检通过或触发你设计的下一轮编辑。

不要在修图途中更新仓库或重载插件。每个连接一次只运行一个任务。用户改变要求时重新发起任务；不要改旧 goal。普通状态查询只调用 status，不能变成继续执行。

## 停止与证据

- 写入不确定、失败、阻塞或纠偏超限时报告真实状态。仅 `goal_ready` 或旧版 `awaiting_confirmation` 的未执行目标可 continue；`applying`、`write_uncertain`、已完成状态均不能重放。
- 外层禁止直接调用 `lr_link.py`、写 requests 队列、用 GUI 改滑块、读取内部 planner/reviewer Skill 后自己执行、生成或修改 goal/proposal/review/状态 JSON、绕开失败校验。不得为修一张照片临时改程序。
- 保留公开入口回执及运行目录。`work/harness-invocations.jsonl` 证明调用了 main.py；各阶段产物和实际 export 证明执行结果。成功启动或 exit code 0 不等于成片已完成（plan 会正常退出并保留未执行目标）。
- 若宿主终止了进程，先读取状态，不能假设进程仍运行或自动重试。完整重跑应基于当前 LR 状态建立新任务，并向用户说明之前的结果。
