# Genshin Lore Research

《原神》文本证据库的解析、构建与检索源码。项目将上游游戏文本转换为带来源记录的 SQLite 数据，供剧情研究和 Agent 检索使用。原文、来源关系与研究者的解释分别处理，便于回查具体材料。

## 当前范围

源码发行版提供 **完整有界数据构建入口**：35 个独立输入、Schema20 的 33 个 core providers，以及统一 Research 文本、关系和候选索引。原 14 类基础组件入口继续保留，适合小范围验证。

最近的 Linux 同条件验证在两个新目录完成全部 35 输入、统一索引和默认向量的单命令构建。阶段重叠与推理调度使总耗时从 **48 分 2 秒降至 33 分 54 秒（约 29.4%）**；完整来源核验保持，3,761,013 条文本的物理坐标和原文一致，106,589 个窗口按来源对齐后向量值完全一致。每侧一次，不能保证其他机器相同提速；实测参数为 `--jobs 3`，保守默认为 2。条件、内存代价和限制见 [完整冷构建性能](docs/cold-build-throughput.md)，此前的 [非向量记录](docs/nonvector-build-performance.md) 保留历史执行范围。

Ubuntu 与 Windows CI 均运行无游戏数据测试。原生 Windows 的完整来源构建另有 [既有验证记录](docs/guides/windows.md)，最新 Linux 优化尚未在 Windows 重跑完整 35 输入；CI 不能代替这项验证。历史成功、原始失败、[工程质量记录](docs/engineering-quality.md) 和各次实现的执行范围分别保留，入口见 [当前状态](docs/status.md)。

仓库分发代码、SQL 定义和必要的来源校验清单；**不分发游戏数据库、游戏正文、模型权重或向量索引**。数据由上游源码在构建环境中生成。

## 快速开始

环境要求：Python 3.12+、Git；Linux 与原生 Windows 的源码检查由 CI 分别执行。Windows 安装见 [PowerShell 指南](docs/guides/windows.md)。以下命令在仓库根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip install -e .

mkdir -p data
git clone --no-checkout https://github.com/DimbreathBot/AnimeGameData.git data/AnimeGameData
git -C data/AnimeGameData checkout --detach 9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99

.venv/bin/python scripts/build_component.py --source data/AnimeGameData --check
.venv/bin/python scripts/build_component.py --source data/AnimeGameData \
  --component activity_archive --output-root data/rebuilt
```

`activity_archive` 是处理两条活动配置的最小构建示例，不是活动剧情全集。成功后，`data/rebuilt/activity_archive/` 包含数据库、构建报告及独立验证报告。输出目录已存在时拒绝覆盖。

列出其他组件：

```sh
.venv/bin/python scripts/build_component.py --list
```

固定快照检查、组件说明、两次构建对照和错误处理见 [基础重建指南](docs/guides/building-components.md)。完整数据库、统一索引和 MCP 启动见 [完整重建指南](docs/guides/building-dataset.md)，其中包含历史来源 oracle、容量预算及启动命令。

## 设计

- **来源可追溯**：保存快照、解析器与导入批次、物理位置、语言及正文范围。
- **检索与证据分离**：literal 是默认检索方式；lexical/semantic/hybrid 用于显式发现候选，最终引用仍回读原始证据。
- **关系保留限制**：同名、相同 ID 或相似文本不自动证明同一人物、说话者、归属或剧情顺序。
- **按需读取**：实现包含 Book/Story 有界续读和引用检查，不以少量摘录冒充完整正文。
- **对白上下文**：显式对白阅读路径保留来源角色标注、分支、续读与可核验的任务标题候选；不按编号或相同台词猜说话者和任务归属。启动与限制见 [对白阅读指南](docs/guides/reading-dialogue.md)。

完整入口生成本次数据的 bundle、profile 和运行配置；单个组件成功不等于整库可用。`--stage all` 默认包含固定本地模型的向量索引，须先按 [向量构建说明](docs/guides/building-semantic.md) 取得模型并安装 semantic 依赖。`--without-semantic` 明确选择仅构建基础数据；不将跳过向量写成完整向量验收。**默认搜索仍为 literal**，semantic/hybrid 仅显式选择。生成数据不自动继承原部署的正式 release 身份，也不声明全游戏覆盖。详见 [架构说明](docs/architecture.md)。

## 文档与开发

- [文档导航](docs/README.md)
- [开发指南](docs/development.md)
- [工程质量与 CI 验证范围](docs/engineering-quality.md)
- [目录与命名规范及旧命令迁移](docs/naming.md)
- [源码导航](src/genshin_lore/README.md)
- [数据分发与许可状态](docs/distribution.md)
- [Agent 开发规则](AGENTS.md)

## 许可证

项目原创代码与文档采用 **Apache License 2.0**，SPDX 标识为 `Apache-2.0`。许可证全文见 [LICENSE](LICENSE)，许可边界见 [分发说明](docs/distribution.md)。

游戏原文、上游数据、外部模型权重和第三方依赖不因本项目许可而被重新授权；各自的权利与许可仍适用。
