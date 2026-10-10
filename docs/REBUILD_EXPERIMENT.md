# 公开源码包重建实验

结论：`PARTIAL_PUBLIC_DATABASE_REBUILD`。14 类基础组件可以从固定上游源码重新构建；完整 Research v1.1 数据链和运行服务尚不能由当前公开包完整重建。

## 实验边界

- 使用公开 ZIP 解包后的源码，不复制或读取既有正式数据库作为构建输入。
- 使用 commit `9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99` 的干净上游 Git checkout。该输入已经存在，未重新从网络下载。
- 完整目标按既有有界 Research v1.1 范围冻结：35 个 artifact inputs、62 条组件绑定、33 个 core providers。14 个基础组件不是此目标的完成数量，也不是游戏覆盖率。
- 原始失败、验证报告及生成数据库保留在实验环境；公开包只包含不带游戏正文的精简记录。

## 组件构建与修复

1. 首轮对 14 类组件逐项新建：11 类成功；NPC、Dialog、Reminder 在验证阶段因相对路径不能转换为文件 URI 而失败。
2. 归一化来源和输出根目录后，三类叙事来源 oracle 通过，但入口错误地只检查 `status`，未适配验证器的 `verdict=PASS`。
3. 修正结果交付适配，保留原始 `verdict`，三类组件定向新建与核验通过。验证器、来源范围和 PASS 门槛未改动；新增负测继续拒绝 `verdict=FAIL`。

全部 14 类组件最终都有成功的来源核验记录，独立数据库总计 1,372,856,320 字节。A/B 数据库字节一致；其中 11 类完整构建报告一致，三类叙事报告含运行耗时、容量和 inode，不声明报告字节一致。

新虚拟环境的依赖安装、可编辑安装以及 AGPL 包元数据检查通过。首次受限环境安装失败保留；之后通过批准的网络执行路径下载公开依赖。全部组件轮次和新环境中的叙事定向轮次分别记录，不冒称所有轮次都在全新环境中执行。

## 整库阻碍

| 实际执行路径 | 首个失败依赖 |
|---|---|
| Schema20 core 装配 | `manifests/m3-7.1-provider/accepted-exact-mappings-v1.json` |
| Character 后续文本，输入为实验新建的 foundation | `manifests/character-7.1-physical-foundation/immutable-runtime.json` |
| 统一 Research 装配 | `manifests/research-7.1/7.1-capability-registry-v1.json` |

另外，special core 来源合同/映射、Document/Readable 范围、Quest/Talk shapes 与完整索引 oracle 等构建引用仍未移植到公开包。相关文件清单见 [机器可读记录](REBUILD_EXPERIMENT.json)。首个完整探针曾因实验器的相对 PYTHONPATH 出错，该记录保留但不作为模块缺失的结论；修正实验器后才得到上表中的有效失败。

这些是构建与资格依赖缺项，不是已经证明上游没有正文，也不是容量不足。补入旧文件名或沿用既有数据库 seal 不足以解决问题：仍需为新建 inputs 生成可移植的绑定，按顺序装配并独立核验，再构建完整索引和验证服务启动。

当前没有新建的完整统一索引，也未执行新数据库的 MCP 可用性验收。不能将组件构建通过写成“整库重建通过”。现有正式部署未被覆盖或改动。

## 记录

[REBUILD_EXPERIMENT.json](REBUILD_EXPERIMENT.json) 包含组件 size/SHA、各轮失败和复验、原始记录摘要、完整目标及容量测量。原始最小 Activity A/B 记录仍保留于 [BUILD_VALIDATION.json](BUILD_VALIDATION.json)，未改写其历史验证范围。
