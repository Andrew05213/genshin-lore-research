# Genshin Lore Research

《原神》文本证据库的解析、构建与检索源码。项目将上游游戏文本转换为带来源记录的 SQLite 数据，供剧情研究和 Agent 检索使用。原文、来源关系与研究者的解释分别处理，便于回查具体材料。

## 当前范围

源码发行版提供 **完整有界数据构建入口**：35 个独立输入、Schema20 的 33 个 core providers，以及统一 Research 文本、关系和候选索引。原 14 类基础组件入口继续保留，适合小范围验证。

历史完整链路曾在新解压目录从固定源码重建通过：35 个输入、33 个 core contracts、统一索引及 MCP 调用完成来源核验，索引包含 3,761,013 条物理文本。详见 [完整重建实验](docs/history/full-rebuild.md)。最新工程修订的测试、最小来源 A/B 和未运行范围见 [工程质量记录](docs/engineering-quality.md)，不以历史成功替代新代码的完整验收。此前失败记录保持不变。

修改后的全链路已实际重试，当前仍有验证返回合同接线缺口，**尚未完成新的完整重建验收**。已执行范围、失败及最小修复见 [重试记录](docs/full-rebuild-retry.md)。

仓库分发代码、SQL 定义和必要的来源校验清单；**不分发游戏数据库、游戏正文、模型权重或向量索引**。数据由上游源码在构建环境中生成。

## 快速开始

环境要求：Python 3.12+、Git；当前验证平台为 Linux。以下命令在仓库根目录执行：

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

完整入口生成本次数据的 bundle、profile 和运行配置；单个组件成功不等于整库可用。literal 是该入口的默认发现方式，semantic overlay 未随基础数据自动构建。生成数据经过独立来源核验，但不自动继承原部署的正式 release 验收身份，也不声明全游戏覆盖。详见 [架构说明](docs/architecture.md)。

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
