# 源码打包与发布

本指南用于构建和发布源码；创建 GitHub 仓库或上传内容需由维护者明确指定目标。许可证暂未选择；正式分发前由维护者决定。VERSION 是产品发行版本，独立于 Lightroom 插件自己的版本号。

## 构建

在项目根目录执行：

```bash
python3 -B -m unittest discover -s tests -v
python3 -B scripts/package_source.py
```

脚本按源码清单打包，生成 `dist/Codex-Auto-LR-<VERSION>-source.zip`、旁置 SHA-256 校验文件，并更新兼容名称 `dist/lightroom-harness-source.zip`。包内根目录为 `Codex-Auto-LR/`，包含 `SOURCE_MANIFEST.json`，逐个列出源文件的摘要。生成物不参与源码自身清单。

包内包含代码、插件、项目技能、文档、测试和 CI。个人 RAW、参考图、社交素材、实验记录、导出、账户配置和安装路径绑定文件均不包含。私人实验脚本作为开发工具保留，依赖的数据集不分发。

## 上传前

1. 更新 VERSION 和 CHANGELOG，选择许可证后新增 LICENSE；打包器会自动带上根目录 LICENSE 文件。未选许可证时不宣称已采用开源许可。
2. 重新构建，解压到独立目录，运行全部离线测试和 `harness.py --help`、`harness.py library`。检查 SOURCE_MANIFEST 的全部摘要。
3. 按 [首次使用验收](FIRST_RUN_CHECKLIST.md) 完成另一台 Mac 的实机测试；把真实验证范围写入发布说明。
4. 将解压后的纯源码目录作为 GitHub 仓库内容，包含 `.agents`、`.github` 和 `.gitignore` 等隐藏文件。不要把整个本机工作目录拖入上传页面，也不要仅在仓库中放一个 ZIP。
5. 在用户选定的 GitHub 仓库中提交并检查 CI。添加许可证或发布说明后再次打包，保证 Release 包与相应 tag 的源码一致。
6. 发布 Release 时附版本 ZIP 和 `.sha256` 文件。对外说明 macOS、Lightroom Classic、Python 和 Codex 登录要求，链接 README 的首次使用步骤。

CI 在 macOS、Python 3.10 和 3.14 下执行离线测试，从新生成的 ZIP 解压后再次测试，并保存可下载的构建产物。CI 不安装 Lightroom、不登录模型、不改照片、不自动发布 Release。没有 Adobe Lua 运行库时对应测试跳过。

## 已知发布边界

- 没有跨机器实测结果前，以测试版分发，不宣称零配置或全平台支持。
- 安装依赖当前项目的固定路径；ZIP 更新与目录迁移步骤不同。
- 当前上限结束保留编辑与预览，仅通过质检的任务正式导出 JPEG。
- 仓库内的素材演示应另外精选并确认可分发，不将私人素材目录整体提交。
