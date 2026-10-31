# Windows 源码使用

## 一键完整安装

在解压后的仓库根目录双击 `setup.cmd`，或在 PowerShell 运行 `./setup.cmd`。
低内存机器可运行 `./setup.cmd --jobs 1`；默认使用两个构建 worker。

安装器只把流程串起来，不另建构建器或降低来源验证：

1. 检查 Git 与 Python 3.12；缺失时尝试 winget 安装。系统安装可能要求确认；没有 winget 时给出所缺软件，不修改系统策略。
2. 检查 MSVC C++ 工具链，缺少时尝试通过 winget 安装 Build Tools 的 VCTools 与推荐 SDK（额外数 GB 系统空间，可能需要提权或重启）；再安装独立 `.venv` 和 semantic 依赖。失败会停止并保留日志，不能跳过向量。
3. 下载固定 7.1 与历史 oracle 来源（浅 checkout），下载并校验固定模型。来源都放在本项目 `data/`，不读其他部署的数据库。
4. 运行完整35输入构建及默认向量，通过已有阶段回执决定复用；随后执行对白服务启动和真实 MCP 程序化检查。
5. 写入 `.codex/config.toml` 的 `genshin-lore-research71` 连接；保留其他配置、备份旧文件，同名冲突拒绝覆盖。安装器不改用户级配置。

成功后在 Codex 打开并信任该项目，重启 MCP 连接，以 `/mcp` 确认五工具可见。
这仍需客户端正常的信任操作；脚本不替用户关闭权限保护，也不把程序化检查称为模型答案验收。
原生 Codex 不自动获得项目专用 citation renderer。

构建前与来源/模型取得后各检查一次项目余量；完整构建还需至少22 GB新增项目预算、23 GB磁盘空闲，下载来源、模型、依赖另计。约16 GB RAM＋4 GB分页文件为既有参考；机器负载与临时卷也影响运行。默认不执行额外 VACUUM。容量政策仍是十进制90 GB hard、72 GiB warning。

全部输出与失败日志保存在 `data/`，安装日志为 `data/setup-logs/*.log`。重复双击会重新核验来源，并交由原构建协调器恢复合格阶段；实现/输入变更、损坏回执或脏来源不会自动重置。遇到拒绝时保留旧目录，按诊断处理，不盲删数据库或 sidecar。中断的来源下载也保留，不能冒充完整 checkout。

恢复 Codex 配置：退出客户端后，用 `.codex/config.toml.before-setup-*.bak` 中对应备份恢复；首次没有配置文件时，删除安装器添加的单个服务器表即可。不要删除其他 MCP 配置或数据库。机器配置和下载数据均已被 Git 忽略。

安装器的合成编排测试可在 Linux/Windows CI 执行；原生 Windows 实机完整安装仍须单独记录，不能用 Linux 测试替代。

## 手动准备与诊断

目标环境为 Windows 11 / Windows Server、NTFS、Python 3.12 和 Git。源码测试在 GitHub Actions 的 `windows-latest` 执行；完整游戏来源构建必须另有真实记录，不能由 Windows 合成测试代替。

在 PowerShell、仓库根目录运行：

```powershell
$env:PYTHONUTF8 = '1'
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.venv\Scripts\python.exe scripts/build_component.py --help
.venv\Scripts\python.exe -m pytest -q
```

UTF-8 模式保持已有来源/清单的编码，不修改游戏原文。构建、检查和服务参数与 [完整指南](building-dataset.md) 相同，将 Python 路径换为 `.venv\Scripts\python.exe`。路径含空格时加引号。上游 checkout 需保留原始字节，禁止 Git 自动转换其 JSON/文本换行。

文件锁采用真实的 Windows 字节范围锁；发布仍禁止覆盖。Windows 没有 Python 可移植的目录 fsync，文件 fsync 和 exclusive publication 仍保留，该差异不表示 POSIX 等级的目录掉电持久性保证。性能计数来自操作系统；不可取得的值明确为空或 unavailable。

M3 与 NPC/Dialog/Reminder 构建的发布路径使用同一可写文件刷盘及平台目录 helper；临时目录在绝对化前解析为系统临时目录，显式指定的其他路径不改写。测试执行小型合成构建和容量门禁，而不只是 import helper。此修复改变构建实现 fingerprint，受影响组件须重新构建并核验，不沿用旧 parser/run 的成功回执。

MCP 在 Windows 使用固定 SDK 的公开 stdio，在 POSIX 保留已有已验证兼容实现。五工具、对白 cursor 和引用映射不变。不得使用 WSL 成功代替原生 Windows 的验证。

R0 扫描器、旧 inventory 兼容入口和容量发现共用 `batch_space.temporary_root`：Windows 默认使用系统临时目录，显式自定义工作目录保留。R0 的合成入口测试实际执行 `Rebuild.r0()`、扫描、SQLite 发布和独立来源行集 oracle；仅模拟合成来源无法具备的固定 Git 身份，不替换 builder 或行集检查。R0 核验失败时保留本次 staging、已发布数据库和 sidecar，不生成成功回执；已有产物拒绝覆盖。

原生 Windows 的完整 35 输入及默认向量构建记录对应 [PR #9](https://github.com/Andrew05213/genshin-lore-research/pull/9) 的 `5c421f7`。后续 Linux 优化的完整来源验证见 [性能记录](../nonvector-build-performance.md)，其 Windows CI 结果不能代替相同实现的 Windows 全量重建。

历史 Schema 晋级工具不是公开的从源码新建路径。Phase 4E5/4F5、Talk comparison C3B 与 Avatar Codex G2 仍有 Windows 目录刷盘、可写句柄或 POSIX 权限检查限制，不承诺这些历史发布路径在 Windows 可用。它们不参与 `scripts/build.py` 的 35 输入路径，也不由该路径的成功自动取得资格。

semantic 依赖单独安装；若 `hnswlib` 缺少适用 wheel，构建其扩展需要 MSVC C++ Build Tools，不能把缺依赖静默当作向量构建成功。

当前完整来源入口在构建、刷盘或核验失败时保留已创建的私有数据库、已发布文件和现存 sidecar，传播失败，不以删除现场完成回滚。成功后的私有镜像清理仍执行。产物路径存在不代表验收成功；仅完整匹配的独立核验及阶段回执允许恢复，失败目录须保留并另选新输出。统一 Research 索引使用目标目录内的私有磁盘 staging 和有限 SQLite 缓存；默认不做 VACUUM，显式压缩模式才在压缩前后刷盘并完整核验。两种模式都在关闭写连接、刷盘及最终映像核验后发布。失败时现存磁盘库直接留存；容量口径与限制见 [完整指南](building-dataset.md)。
