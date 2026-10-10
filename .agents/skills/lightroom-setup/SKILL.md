---
name: lightroom-setup
description: 安装、更新和诊断本仓库的 Lightroom Classic Lua 插件与 Python harness，核对 Codex 登录和连接。适用于首次配置、Download ZIP 或 Git 更新后的插件同步以及连接故障；不执行照片编辑。
---

# 安装 Lightroom harness

本 Skill 位于 `<仓库>/.agents/skills/lightroom-setup/SKILL.md`，向上三级是仓库根。只用该目录下 `harness.py` 的绝对路径。第一版支持 macOS、Python 3.10+、本机 Lightroom Classic 和已登录的 Codex CLI。

1. 先用 `python3 --version` 确认解释器为 3.10+，再运行 `python3 -B <仓库>/harness.py doctor`。此命令只检查，不改照片或安装软件。缺 Python 时根据错误指导用户安装 Python 3.10+；不要把 `python3` 存在当作版本满足。首次尚未安装插件、尚未连接时，对应诊断失败是预期情况，不能因此阻止后续安装；解释器或 CLI 问题则先处理。
2. 用户已要求安装/更新时，先运行 `harness.py setup --dry-run` 确认目标。若连接还在运行，请用户手动 Stop connection 后继续。不要在任务执行中 git pull、替换插件或重载。
3. 运行 `python3 -B <仓库>/harness.py setup`。脚本安装到用户 Lightroom Modules 目录、写入该仓库的通信路径、记录文件摘要、备份旧安装；重复运行不重复安装。对项目外文件写入按宿主权限流程处理，不通过其他工具绕开拒绝。遇到同名非托管插件或其他仓库绑定时停止，报告冲突路径，不删除它。
4. 请用户重启 Lightroom Classic；如未出现，按输出路径在「文件 → 增效工具管理器 → 添加」加载插件。停用以前手动添加的旧副本。用户手动在「图库 → 增效工具额外信息 → Start connection」连接。
5. 再运行 doctor，确认安装摘要、插件版本、当前仓库通信目录和心跳。安装成功不等于 LR 已加载或连接成功。
6. 首次在当前 Codex 宿主使用时，运行 `python3 -B <仓库>/harness.py doctor --smoke-model` 验证同一 Python runner 能启动内部 `codex exec`。该测试消耗少量额度、不传照片。CLI 登录检测只证明凭据存在；不能代替网络/模型测试。

如果 smoke 或实际任务被外层沙箱拒绝，使用宿主的正常命令授权重试（安装、诊断可重复；修图不确定写入不能重放）。保留内层 `--sandbox read-only` 与工具关闭配置。不要读取/复制 auth.json、关闭沙箱、改模型提供商配置或自行重写内部调用流程。只有用户明确提出开发任务时才修改源码。

更新时先阅读仓库根目录的 [更新指南](../../../docs/UPDATING.md)，区分 Download ZIP 与 Git。ZIP 用户保留原固定目录，核对发布清单、备份并仅更新源码；没有旧摘要清单时不能假定本地未修改。Git 用户先检查本地改动，再 git pull --ff-only。更新必须在无活动修图任务并手动 Stop connection 后进行；之后运行 setup、重载、Start 和 doctor。不要自动创建远程仓库或发布用户文件。

本仓库自带 Skill 可在项目中发现。不要自动安装其他全局 Skill 或移动仓库；安装绑定仓库绝对路径，移动后需要重新配置。诊断完成后，照片处理转交同级 `../lightroom-edit/SKILL.md`。
