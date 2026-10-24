# Windows 源码使用

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

历史原地数据库晋级工具不是公开的从源码新建路径；本次不承诺其 Windows 文件替换语义。semantic 依赖单独安装；若 `hnswlib` 缺少适用 wheel，构建其扩展需要 MSVC C++ Build Tools，不能把缺依赖静默当作向量构建成功。

当前完整来源入口在构建、刷盘或核验失败时保留已创建的私有数据库、已发布文件和现存 sidecar，传播失败，不以删除现场完成回滚。成功后的私有镜像清理仍执行。产物路径存在不代表验收成功；仅完整匹配的独立核验及阶段回执允许恢复，失败目录须保留并另选新输出。统一 Research 索引使用目标目录内的私有磁盘 staging 和有限 SQLite 缓存；oracle 与阶段诊断在 VACUUM 前刷盘，压缩后再次完整核验，关闭所有写连接并刷盘后才发布。失败时无需将内存整库临时导出，现存磁盘库直接留存；容量口径与限制见 [完整指南](building-dataset.md)。
