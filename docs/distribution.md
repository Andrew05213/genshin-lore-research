# 源码分发与数据留存

## 分发内容

公开包包含 Python 源码、SQL schema/migrations、构建入口、结构与来源校验清单，以及开发文档。包内不含原开发仓库的 Git 历史。

当前可执行及已验证范围见 [当前状态](status.md)。源码中保留的完整部署实现不等于所有依赖和构建步骤都已随包提供。

以下内容不随源码分发：

- 上游游戏文本、生成数据库和检索索引。
- embedding、向量索引和模型权重。
- 历史会话正文、构建运行记录与部署验收材料。
- 凭据、客户端个人配置和部署路径绑定。

[初始源码清单](history/initial-source-files.json)（原名 `PUBLIC_SOURCE_FILES.json`）保存最初源码归档的文件 size/SHA，仅用于该归档的完整性核对，不是后续 Git 提交的当前文件 seal。后续代码版本按 Git commit 定位。

## 发布前检查

- 确认 `LICENSE`、README 与包元数据均声明 `Apache-2.0`，保留适用的版权与许可通知。
- 仅导入源码包内容，另建公开仓库历史；不推送原开发仓库的完整历史。
- 保留生成数据与个人配置的忽略规则，检查暂存区没有数据库、游戏正文、凭据或会话记录。
- 运行 [开发指南](development.md) 中的测试，核对 README、重建命令及文件清单。
- 生成新的源码归档时为该归档生成新的 size/SHA 清单；不沿用旧 ZIP 的校验值，不改写历史归档清单来冒充原始内容。

## 代码与依赖预构建包

手动运行 GitHub Actions 的 **Source and dependency bundles**，为 Linux x86-64 / Windows x64 的 Python 3.12 分别构建 wheelhouse，完成全新环境离线安装后生成 CI 附件。此工作流不自动创建正式 release、不改 current；经维护者审查后才可将相应附件放入 GitHub Release。

附件包含该 commit 的 tracked 源码、SQL/静态合同、锁定的公共 Python 依赖与平台 hnswlib wheel，以及 `bundle.json` 文件 SHA 和第三方许可元数据。原 wheel 内的第三方许可文件保留。它不包含游戏来源、数据库、运行绑定、模型权重、embedding 或向量缓存。代码继续使用 Apache-2.0，第三方依赖仍按各自许可分发。

`requirements-tools.lock` 固定运行/semantic 依赖输入；`requirements-wheel-build.lock` 固定原生 wheel 构建依赖。编译后 wheel 的字节与上游 sdist 不同，因此附件 `install.lock` 使用实际平台 wheel 的新 SHA，而不伪称二者字节相同。附件只能用于标注的平台、架构和 Python ABI；不能复制已有 venv 跨平台使用。

解压附件后，在解压目录执行：

```sh
python -m venv .venv
# Linux: .venv/bin/python；Windows PowerShell: .venv/Scripts/python.exe
.venv/bin/python -m pip install --no-index --find-links wheelhouse --require-hashes -r install.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation --no-index -e source
.venv/bin/python -m pip check
.venv/bin/python source/scripts/build.py --help
```

运行入口仍依赖完整源码 checkout 资源，不将单个项目 wheel 声称为独立数据部署。之后按 [重建指南](guides/building-dataset.md) 显式取得固定来源、准备本地模型并构建；预构建依赖只减少安装和编译时间，不跳过来源验证。

维护者本地生成时，在全新构建环境先安装构建 lock，再用 `pip wheel --no-build-isolation --require-hashes -r requirements-tools.lock -w wheelhouse` 与同样的构建 lock 命令补齐 wheelhouse，最后运行 `python scripts/maintenance/package_tools.py --wheelhouse wheelhouse --output tools.zip`。源树 tracked 文件必须与 commit 一致；输出必须不存在。未追踪文件、Git 历史和部署数据不会被打包。

## 生成数据留存

重建输出默认位于 Git 忽略的 `data/`。数据库和构建/验证报告可能包含游戏原文，不应作为公开源码附件提交。使用其他输出目录时，需要相应配置 Git 忽略规则。

## 许可与运行状态

项目原创代码与文档自本次修订起采用 `Apache-2.0`，全文见 [LICENSE](../LICENSE)。包元数据使用相同 SPDX 标识。本次授权不改写旧提交、旧归档及其当时的 AGPL-3.0-only 声明；历史实验中的许可字段是当时的检查结果，不是当前许可配置。

游戏原文、上游数据、外部模型权重和第三方依赖不因源码分发而取得或改用项目许可；各自的权利与许可仍适用。

完整入口生成本次数据的来源绑定和运行配置，但不复制既有部署的正式验收状态。实际范围与可选 semantic 限制见 [完整重建指南](guides/building-dataset.md) 和 [实验记录](history/full-rebuild.md)。
