# 数据重建指南

本指南适用于公开源码包的组件重建入口。所有命令均在仓库根目录执行；生成数据默认写入已被 Git 忽略的 `data/`。

本页介绍保留的 **14 类基础组件** 入口。需要统一数据产品、Research 索引和服务时，使用 [完整重建指南](building-dataset.md)。原基础实验的失败与复验仍保留于 [历史重建实验](../history/component-rebuild.md)；完整链路的实际验证结果另行记录，不将两者混为一谈。

初始最小 Activity A/B 验证保留于 [BUILD_VALIDATION.json](../history/component-build-validation.json)，其历史范围不因后续实验而改写。

## 环境要求

- Python 3.12 或更新版本、Git；当前验证平台为 Linux。
- 固定版本的 AnimeGameData 源码，包含构建所需的 TextMap 等来源文件。
- 足够容纳上游源码、生成数据库和构建临时文件的磁盘空间。

组件入口要求输出所在文件系统至少有 3 GB 可用空间。这是启动检查下限，不是所有组件的峰值容量估算。部分构建器还执行项目占用检查；相关阈值定义在 `src/genshin_lore/storage_policy.py`。

## 1. 安装依赖

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip install -e .
```

`requirements-build.txt` 固定已验证的直接依赖版本，不是完整的传递依赖锁文件。

## 2. 获取上游源码

上游项目：<https://github.com/DimbreathBot/AnimeGameData>。
构建使用以下固定 Git 身份：

| 项目 | 值 |
|---|---|
| Commit | `9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99` |
| Tree | `aa578c08c402af39b41f1ffc95124d10f5410272` |
| Snapshot | `anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99` |

```sh
mkdir -p data
git clone --no-checkout https://github.com/DimbreathBot/AnimeGameData.git data/AnimeGameData
git -C data/AnimeGameData checkout --detach 9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99
.venv/bin/python scripts/build_component.py --source data/AnimeGameData --check
```

`--check` 核对 commit、tree 和工作区清洁状态。快照或工作区不匹配时构建拒绝执行，不自动选择最新版本。若上游无法提供该 commit，可使用包含相同 Git 对象的镜像，再执行同一检查；公开包不附上游快照。

`--source` 可指定其他源码目录。构建器只读取显式指定的输入，不自动查找其他部署的数据。固定快照中的文本存在不代表该文本首次加入于对应游戏版本。

## 3. 构建组件

列出组件标识：

```sh
.venv/bin/python scripts/build_component.py --list
```

最小构建示例：

```sh
.venv/bin/python scripts/build_component.py --source data/AnimeGameData \
  --component activity_archive --output-root data/rebuilt
```

`activity_archive` 处理两条活动配置，适合检查构建流程；其结果不是完整活动剧情库。

入口注册的组件如下：

| 类型 | 组件标识 |
|---|---|
| 配置与文本投影 | `activity_archive`、`achievement_archive`、`avatar_config_archive`、`dungeon_archive`、`monster_archive`、`npc_archive`、`scene_archive`、`weapon_config_archive`、`reliquary_config_archive`、`material_config_archive` |
| 角色物理基础 | `character_foundation` |
| NPC、对白与提醒 | `narrative_npc`、`narrative_dialog`、`narrative_reminder` |

配置与文本投影组件依赖 CHS/EN/JP 的 main/Medium TextMap。`npc_archive` 与 `narrative_npc` 是不同投影，不可互相替代。

其他组件示例：

```sh
.venv/bin/python scripts/build_component.py --source data/AnimeGameData \
  --component character_foundation --output-root data/rebuilt
.venv/bin/python scripts/build_component.py --source data/AnimeGameData \
  --component narrative_dialog --output-root data/rebuilt
```

`character_foundation` 保存角色资料、故事及语音的物理基础结构，不会同时生成全部后续文本组件或统一 Research 索引。未判明的关系和提醒字段保留候选或 unresolved 状态。

## 4. 输出与验证

每个组件按“创建新输出目录 → 构建 → 独立来源核验”的顺序执行。例如：

```text
data/rebuilt/activity_archive/
├── invocation.json
├── artifact.sqlite
├── build.json
└── verify.json
```

- `invocation.json`：组件与固定源码身份。
- `artifact.sqlite`：生成的数据。
- `build.json`：构建结果、来源记录和数据库 size/SHA。
- `verify.json`：独立验证结果，核对来源字节、字段结构、行数与投影内容。

入口统一交付 `status`；叙事验证器的原始 `verdict` 字段同时保留。只有独立验证通过时才交付成功，不能通过修改结果字段跳过来源检查。

命令正常结束且 `verify.json` 为 `PASS`，表示该组件通过来源核验，不表示完整 Research 部署已通过验收。生成数据库和报告可能包含游戏原文，不应提交到公开仓库。

现有输出目录不会被覆盖。构建失败时保留已生成文件；重试应指定新的 `--output-root`，避免混用不同尝试的产物。

## 5. 两次独立构建对照

```sh
.venv/bin/python scripts/build_component.py --source data/AnimeGameData \
  --component activity_archive --output-root data/rebuilt-a
.venv/bin/python scripts/build_component.py --source data/AnimeGameData \
  --component activity_archive --output-root data/rebuilt-b
.venv/bin/python scripts/build_component.py --compare \
  data/rebuilt-a/activity_archive data/rebuilt-b/activity_archive
```

`--compare` 要求两侧独立验证成功，重新计算数据库 size/SHA，并比较组件身份与构建报告。两侧应分别从源构建，不能用复制数据库代替第二次构建。

上述示例已针对 `activity_archive` 验证。叙事组件的报告包含耗时、inode 和容量实测，数据库字节相同也不保证完整报告相同；当前严格对照命令会拒绝这类报告差异。数据库 determinism 和报告字节一致性须分别说明，不通过忽略差异制造 PASS。

## 常见问题

| 现象 | 检查与处理 |
|---|---|
| 固定快照检查失败 | 核对 `git -C <source> rev-parse HEAD HEAD^{tree}` 和 `git -C <source> status --porcelain`；不改用最新版本绕过检查。 |
| 上游 commit 无法获取 | 确认上游或镜像包含该 Git 对象。仅有文件副本而没有可核对的 Git 身份，不符合当前入口要求。 |
| 输出目录已存在 | 使用新的 `--output-root`；保留失败报告用于排错，不覆盖原产物。 |
| 可用空间不足 | 检查输出文件系统空间、项目容量策略和预计构建峰值。3 GB 启动下限不是完整部署所需容量。 |
| 来源文件缺失或字段不符合预期 | 查看具体构建器报错，核对固定快照及组件的来源清单；不以其他文件、宽松解析或跳过验证替代。 |
| A/B 对照失败 | 检查两侧 `verify.json`、`build.json` 和数据库 seal；对照命令不会重新运行来源验证，也不会修复差异。 |
| 单组件成功，但 MCP 无法启动 | 单组件产物不是完整运行配置；核对下方列出的统一部署缺项。 |

## 完整统一部署与当前可执行范围

`scripts/build.py --stage all` 接线了原始来源归档、33 个 core providers、全部 35 个独立输入、统一索引及运行配置。它使用本次来源核验报告，不要求已有数据库或原部署的 receipt。具体步骤和容量要求见 [完整重建指南](building-dataset.md)。

可选 semantic overlay、模型权重以及原部署的正式 release/current 不由该数据入口自动生成。完整数据重建与正式发布资格是不同结论；实际执行范围见 [完整重建实验](../history/full-rebuild.md)。

安装 Python 包或完成单个组件构建，不会自动配置 MCP/Codex。检索服务需要相互匹配的数据清单、运行配置、来源绑定和索引；缺失依赖时应按错误定位缺项，而非跳过校验。研究 Agent 从合格 MCP 读取和引用游戏原文；构建器直接读取上游源码是独立的数据建设步骤。

开发安装与测试见 [开发指南](../development.md)；源码与生成数据的分发边界见 [分发说明](../distribution.md)。
