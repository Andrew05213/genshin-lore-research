# Linux 一键安装

下载并解压源码（或 git clone），在仓库根目录执行：

```sh
bash setup.sh
```

需要降低构建并发时使用 `bash setup.sh --jobs 1`，默认两个 worker。
脚本也支持从其他工作目录以绝对路径运行，所有输出仍属于脚本所在项目。

## 安装流程

入口先检查 Git、C++ 编译器、Python 3.12、venv/ensurepip 和 Python 开发头文件。
已满足前置条件的 Linux 环境不调用系统包管理器。缺项时，apt 环境尝试安装
`git build-essential python3.12 python3.12-venv python3.12-dev`；普通用户使用 sudo，可能要求输入密码。
不添加第三方软件源、不替换系统 Python。若发行版仓库不提供 Python 3.12，或没有 apt/sudo，明确报错；准备上述依赖后重新运行。

后续与 Windows 共用 `scripts/setup_project.py`：

1. 创建独立 `.venv`，先按锁文件安装 NumPy/pybind11 等编译依赖，再安装含 semantic 的运行依赖和源码。
2. 取得固定 7.1 及历史 oracle 来源，校验干净 checkout，下载并验证固定模型。
3. 调用现有 `scripts/build.py --stage all` 完成35输入、统一索引和默认向量，仍由原有回执和独立验证决定资格及阶段复用。
4. 执行对白 MCP 启动和请求检查，通过后才写项目级 `.codex/config.toml`。

来源、模型和构建输出在 `data/`，pip 下载缓存在 `cache/pip/`；这些内容不提交 Git。
不读取其他部署作为 fallback，不修改全局 Codex 配置。已有项目配置先备份，保留其他服务器，同名连接冲突拒绝覆盖。
完成后在 Codex 打开并信任该目录、重启 MCP，以 `/mcp` 核对五工具。
该步骤不替代客户端授权，也不自动安装或登录 Codex。默认 literal，semantic/hybrid 显式可选；原生客户端不自动获得项目 citation renderer。

## 空间与恢复

依赖、来源和模型另计，完整构建前仍须22 GB项目新增预算及23 GB文件系统空闲。
hard 为90,000,000,000 B，warning 为77,309,411,328 B；沿现有项目归属口径统计，不用主机空闲代替项目余量。
约16 GB RAM＋4 GB swap 是既有完整构建参考，低内存并发与其他负载仍须另行评估。
完整范围、预算和回执规则见 [数据构建](building-dataset.md)、[向量构建](building-semantic.md)。

系统准备及安装日志分别保留在 `data/setup-logs/linux-bootstrap-*.log` 与同目录的 Python 安装日志。
失败返回非零，不删除来源、数据库、sidecar 或原始失败记录。
重复运行仍由原构建协调器核对可复用阶段；脏来源、损坏或实现不匹配的回执不会被重置或放宽接受。
中断的下载/构建目录保留诊断，必要时处理冲突或另选新的源码安装目录，不承诺任意失败都能无人工处理地续传。

恢复配置时退出客户端，用 `.codex/config.toml.before-setup-*.bak` 中相应备份恢复。
首次没有配置文件时仅移除安装器添加的服务器表，不删除其他配置或数据。

## 验证边界

CI 用合成工具实际执行 Bash 的环境发现、参数传递、失败退出和日志保留，并运行共享安装编排测试。
这些检查不下载游戏来源或模型，不代替一次完整冷安装、35输入重建或模型自主答案验收。
Windows 入口 `setup.cmd` 与原 PowerShell 命令保持兼容，Windows 实机验证单列。
