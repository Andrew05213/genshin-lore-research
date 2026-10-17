# 完整构建链合同核对

本核对针对 [三次实际重建](full-rebuild-retry.md) 暴露的调度接线缺口，不重审游戏关系语义、不修改历史验收，也不替代下一次全新完整重建。

## 范围与返回合同

完整入口实际包含 60 个调度阶段；它们共同生成最终 35 个独立输入及统一索引。阶段、合同和游戏内容覆盖率不是同一个分母。

| 阶段组 | 阶段数 | 本次核对依据 |
|---|---:|---|
| Core 组件及 Schema20 装配 | 24 | 前次真实执行的报告形状、独立验证输出、文件 seal 与现有接线 |
| Character | 4 | 同上；foundation manifest 显式传入后继组件 |
| NPC / Dialog / Reminder | 3 | 同上 |
| Document / GCG / LoadingTips / Mail / Native Message | 5 | 同上 |
| Quest / Talk | 5 | 同上；Quest/Talk 与 derived manifests 为输出内绑定 |
| CodexQuest | 1 | 同上；使用输出内 special-core mappings |
| R0 | 1 | 原生报告已在调度边界规范化，独立 oracle 必须实际完成 |
| Readable / Subtitle | 2 | 已执行报告与 R0 / Document 显式依赖 |
| Event | 9 | 六项已有成功报告；Event13、16、18 完成状态修复；实际阶段编排合成测试 |
| Native 后继 | 5 | 全部 builder 的 seal 返回、verifier 完成返回、依赖签名与实际编排合成测试 |
| Research index | 1 | 现有真实合成索引发布与独立 oracle 测试；完整来源索引尚未执行 |

前次 51 个成功阶段的验证输出为：38 个 `status=PASS`、6 个 `status=pass`、6 个 `verdict=PASS`、1 个 `verdict=PASS_PHYSICAL_PROJECTION`。这些均由既有严格状态集合支持，不需要放宽调度器。

剩余的同类缺口集中在四个验证器，影响五个阶段：

- `event13_7_1_physical_verify`；
- `event16_18_7_1_selection_verify`（分别执行 16 与 18）；
- `native21_7_1_verify`；
- `native26_7_1_verify`。

它们现在只在原有来源、完整行集、schema、seal 和写保护检查完成后返回 `status=PASS`，保留原 `verified=true` 及所有诊断字段。此状态表示**验证执行完成**，不表示产品 accepted、关系确定或语料完整。检查抛异常时仍无成功结果。

通用调度器仍拒绝缺失、未知、NOT_RUN、SKIPPED、PARTIAL、冲突状态和单独的 `verified=true`；出现 `verified` 字段时，还要求它是布尔 `true`，不能与 PASS 自相矛盾。不修改通用成功集合。

## 动态配置读取

| 动态输入 | 生产调用的显式接线 |
|---|---|
| Core mappings | `metadata_root` → Schema20 builder/oracle |
| Character foundation | `foundation_manifest` → text/lore/voice builder/verifier |
| Quest/Talk 与 derived | 传入输出内 manifest 路径，独立核对依赖 |
| Filmfest | 候选发现、构建、验证三处均传入 `qt_manifest_path` |
| Event16/18 输入 | scope 绑定 artifact、运行 manifest 绝对路径及 SHA |
| Native Book/MusicGame | scope helper 显式接收 `--message-runtime`，独立 verifier 显式接收 `message_runtime_path`，该文件进入阶段输入身份 |
| Native Aux/Quest | 外部候选推导及独立 verifier 均接收 `metadata_root`；Quest/Native Bulk manifests 进入阶段输入身份 |
| Native21 | `metadata_root` 及 scope 中精确 Event13 binding 路径/SHA |
| Native26 | `metadata_root`、精确 Event16 manifest SHA 与 Quest/Talk 输入 |
| 统一索引及运行配置 | corpus 所属 metadata root；配置相对自身解析，无 sibling fallback |

真实来源尾部诊断又发现 Book/MusicGame verifier 仍读源码树 Message runtime；随后逐行核对还发现 Aux/Quest 的外部候选推导和 verifier 有相同的固定 manifest 读取。两处都已补显式参数，并将相应文件纳入阶段输入身份；默认历史参数形式保留，当前完整入口不依赖它。原始诊断失败不删除，也不把静态检查当成已通过的来源验证。

历史源码范围清单仍是静态合同，不应迁出源码或被动态运行配置替代。这是对当前调用路径的核对，不声称覆盖任意未来新增组件。

## 防止重现的测试

- Event13 使用 30 份合成来源和六份合成 TextMap，执行实际 builder、独立 rowset/schema oracle、写保护负测和阶段回执；仅将固定上游 Git pin 检查替换为合成来源 byte-seal 检查，不含游戏正文。
- 故意删除合成数据库的 trigger，独立验证必须失败且不能生成成功回执。
- 四个实际 verifier 的完成返回静态检查；这是返回合同测试，不冒称执行 Native 的真实来源 oracle。
- 运行实际 `Rebuild.events()` / `native()` 编排，使用真实函数签名及实际完成标志，builder 只生成微型合成 SQLite。检查两套输出的 Filmfest、Event16/18、Native21/26 和 scope helper 绑定，拒绝错误参数及串目录。
- 保留未知/未执行/冲突状态负测，新增 `verified=true` 单独返回及非布尔/矛盾完成标志拒绝测试。
- Book/MusicGame 与 Aux/Quest 的显式 manifest、缺文件拒绝、无源码树/sibling fallback、错配 artifact 拒绝；实际编排测试要求传入这两处新增参数。

合成编排测试不能证明游戏数据投影正确；真实来源定向复验与全新完整构建必须分别记录。未完成后者之前，不得把本修复标记为完整 35-input 重建通过。
