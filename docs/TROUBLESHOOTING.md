# 故障排查

在 Codex 中说“使用 lightroom-setup 检查环境”，或在项目目录运行 `python3 -B harness.py doctor`。诊断不修改照片。只提供相关错误输出；不要发送登录令牌或整个账户配置目录。

| 情况 | 处理 |
|---|---|
| Python 不存在或低于 3.10 | 安装受支持的 Python，再重新打开 Codex 并检查版本；不要把 macOS 自带命令存在当作版本满足 |
| 找不到 Codex CLI | 程序会查找 PATH 和应用内置 CLI；让 Codex 检查实际可执行文件位置。若均没有，再安装官方 CLI |
| `codex_login` 失败 | 使用诊断所找到的 CLI 完成 `login`，再运行 `login status`；GUI 已登录不代表当前 CLI 一定能读取同一登录状态 |
| 首次 `plugin_install` 失败 | 执行 `harness.py setup --dry-run`，再 setup；这是首次安装前的预期状态 |
| 安装提示另一仓库绑定 | 按更新指南保留原项目路径；不要删除安装回执来绕过检查 |
| 同名非托管插件 | 保留旧插件，在 Lightroom 停用，确认来源后备份到 Modules 外，再安装当前版本 |
| `connection` 失败 | 打开 Lightroom Classic，在“图库 → 增效工具额外信息”手动 Start connection；空闲超时后也要重新 Start |
| `loaded_plugin` 失败 | 重载已安装插件，停用重复副本，重新 Start；安装成功不代表 Lightroom 已加载新版本 |
| doctor 通过但模型不可用 | 执行 `doctor --smoke-model`；检查模型权限、网络和额度，登录凭据存在不能证明模型可调用 |
| CLI 不支持必要参数 | 核对 CLI 版本和 `exec --help`；不要删除隔离参数来让旧版本勉强运行 |
| 权限拒绝或无法写 CLI 状态目录 | 使用 Codex 正常的命令授权流程重试诊断。保留内部只读 sandbox，不复制登录凭据、不改认证提供商 |
| 照片不可读 | 确认原文件在本机且 Lightroom 有该目录访问权限；聊天预览不一定是可用原图 |
| 达到两轮上限 | `limit_reached` 正常结束，保留编辑与预览，但未通过质检，不会正式导出 |
| 写入不确定或进程中断 | 用 `harness.py status --run-dir '…'` 查看，保留日志，不重放写入。查清 Lightroom 当前状态后才能考虑新任务 |

模型阶段可能持续数分钟。保留原进程并查询状态，不要因暂时没有输出重复启动。安装诊断可以重复；照片写入不确定时不能按安装重试方式处理。

项目默认忽略用户 CLI 配置，适用于已验证的本机 Codex 登录环境；用户自定义模型提供商不会自动生效。内层模型与当前 GUI 聊天的模型选择也不自动同步。
