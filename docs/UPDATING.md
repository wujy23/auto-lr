# 更新 Codex-Auto-LR

更新前先等待当前修图结束，手动在 Lightroom 执行 Stop connection。保留原图和任务记录；不要在运行中更换代码、Skill 或插件。

## Download ZIP 用户

首次安装后固定项目目录。更新时不要直接把新解压目录作为另一个项目安装，否则已安装插件会报告绑定另一份仓库。

可以在原项目的 Codex 聊天中发送：

> 我下载了新版源码 ZIP，路径是「…」。请按 docs/UPDATING.md 更新当前项目。先检查无活动修图任务，比较文件并备份，保留照片和历史运行记录；发现本地源码修改时先列出差异。

由 Codex 按以下步骤执行：

1. 将 ZIP 解压到独立临时目录，检查包含 `README.md`、`harness.py`、`scripts/`、`.agents/skills/` 和 `LightroomLink.lrplugin/`，拒绝压缩包中的外部路径或符号链接。不要直接覆盖整个项目目录。
2. Release ZIP 含 `SOURCE_MANIFEST.json`，先核对所列文件的 SHA-256。GitHub 自动生成的 Download ZIP 不含生成的清单，需核对下载来源和版本，再按 `scripts/package_source.py` 的源码清单检查内容。
3. 比较旧源码与更新文件。旧项目若保留上一版本 `SOURCE_MANIFEST.json`，可以据其发现本地修改；没有旧清单时不能假定本地源码未修改。发现用户修改时保留并说明差异，不静默覆盖。
4. 将要替换的旧源码和旧清单备份到项目外的独立目录，记录路径。只更新发行版清单中的源码文件，包含隐藏目录 `.agents/` 和 `.github/`。保留 `raw/`、`references/`、`data_set/`、`runs/`、`exports/`、`work/`、实验和社交素材目录。新包不含这些目录，不意味着应该删除它们。不要复制 `InstalledConfig.lua` 或安装回执。
5. 在原项目运行离线测试，再执行 `harness.py setup --dry-run` 和 `harness.py setup`。如失败，停止使用新版本，按备份还原源码；不要直接继续修图。
6. 在 Lightroom 增效工具管理器重载已安装插件，再手动 Start connection。运行 `harness.py doctor`；CLI 或运行环境发生变化时加做 `doctor --smoke-model`。

## Git 用户

确认没有活动修图任务，Stop connection 后先检查 `git status`。保留本地改动，再 `git pull --ff-only`；有冲突或无法快进时先解决，不强制覆盖。之后执行上述步骤 5–6。

## 移动项目或换一份 checkout

当前安装器会拒绝覆盖绑定另一项目路径的安装。优先保留原路径更新。确需迁移时，先停止旧任务和旧连接，在 Lightroom 停用旧安装，将旧托管插件及回执备份到 Modules 外，然后从新目录运行 setup、重载并重新连接。需要系统文件授权时走正常权限流程。

历史任务保存了绝对路径和方法摘要，移动后不能改写这些记录以续跑。新版本建议新开任务；仅尚未执行且校验通过的 `goal_ready`（兼容旧 `awaiting_confirmation`）任务可以 continue，其他状态不能重放。
