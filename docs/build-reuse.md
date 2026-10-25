# 冷构建与阶段复用

构建和独立来源验证继续执行，优化范围是重复输入解析与不相关实现变更造成的阶段失效。没有缩减来源范围、关系 rowset、负面探针或资格条件。

## 单次构建的输入复用

`scripts/build.py` 的 `--textmap-cache-mib` 默认 1024，每个独立 reader 最多缓存 1 GiB，`0` 关闭。仅 M3 显式启用；其 builder 与 oracle 分别完整扫描 TextMap 语法、类型、重复键和文件末尾，各自保存原文和词法位置，不共享解析器或投影结果。命中时仍核对全部输入字节 SHA，再取得所需 key 的原始 token、ordinal 和 pointer。共享 reader 的其他调用保持原行为。缓存在 core 阶段结束即释放，不驻留到内存密集的 Research oracle 或向量阶段，不写数据库或常驻缓存服务。

此限额约束缓存，不代表整个进程的内存上限；首次解析、原始文件和数据库仍占内存。实测三个组件峰值 RSS 从约 514 MiB 增至 1,877 MiB。内存受限时关闭缓存，不关闭验证。512 MiB 的诊断曾发生六文件循环淘汰、没有命中且更慢，因此不采用该默认值。

M3 的解析器身份现在显式绑定缓存 helper 和新的词法输入实现标签。新建 artifact 使用新的 parser/run 身份；旧产物和记录不改签。同一新实现中，缓存开/关的三个组件 artifact 字节一致。

## 阶段回执 v3

`recipes/build-code-scopes.json` 是简单的显式所有权表，按领域而非自动 import 推断选择代码。阶段绑定本域及声明的传递依赖；未分类代码、全部 SQL/静态 manifest/recipe、共享入口和身份检查保持保守绑定。未知阶段仍绑定全部实现。新增跨域调用必须更新该表并补漂移测试，不通过文件名推断数据资格。

恢复继续检查 artifact、sidecar、实现、来源路径、显式输入、构建参数、环境与 build/verify/invocation 报告的完整身份。parser、verifier、共享 helper、合同或依赖变化仍拒绝恢复。旧 v2 回执与失败目录拒绝自动复用，不重签、覆盖或跳过原失败。

因此，已核验阶段可以承受明确隔离的对白运行时编辑；修改来源 scope 或解析器仍必须重建。复用不是重新执行独立来源核验，而是核对其已封存结果与完整适用身份。

## 本次同条件测量

Linux / Python 3.12，同一固定 7.1 源，串行 A–B–B–A，两组各两次独立进程、空应用缓存，保持相同组件顺序和完整独立验证。操作系统页缓存没有清空，不宣称物理磁盘冷启动。

| 三个真实 M3 组件合计 | A：关闭缓存 | B：1024 MiB/reader |
|---|---:|---:|
| 第一次 | 25.873 s | 16.825 s |
| 第二次 | 25.918 s | 16.894 s |
| 中位数 | 25.896 s | 16.859 s |

Activity、Avatar、Achievement 的构建与独立 oracle 合计减少 9.036 s（34.90%）。每组均完整读取固定六个 TextMap；B 的 builder/oracle 各 6 次编译、12 次复用。十二个 artifact 的 size/SHA 按组件完全一致。加入显式 opt-in 和生命周期限定后重新执行这组测量，之前约 36.15% 的初测也保留在机器记录中。

真实 Activity 阶段另测：首次构建和核验 8.212 s；相同身份恢复 0.047 s；仅修改隔离的对白 runtime 后恢复 0.046 s，均保留同一 artifact seal。后两次没有调用 builder/verifier，仍逐项检查回执及 artifact。

Research 延后创建五个非唯一索引也做了同条件 A–B–B–A。投影加建索引约 1.08 s，对照约 1.08 s；总耗时差异主要来自 oracle 运行波动，未采用此修改。

这些是定向组件测量，不是本提交的完整 35 输入重建或整库提速比例。完整重建需要额外约 27 GB 峰值，本机项目预算余量不足，本轮不执行。原始失败、诊断和测量文件本地保留；不将游戏正文或数据库放入性能报告。来源身份、计数、digest 与本次结果见 [机器可读记录](reviews/cold-build-reuse.json)。

复现单次测量：

```sh
python scripts/maintenance/benchmark_textmap.py --source /path/to/pinned-source --output-root data/bench-a1 --cache-mib 0
python scripts/maintenance/benchmark_textmap.py --source /path/to/pinned-source --output-root data/bench-b1 --cache-mib 1024
```

然后串行执行 B2、A2，使用不同的新输出目录；计时包括首次完整词法解析和独立来源验证。不要与其他构建/测试并发，也不把热进程重复调用当冷构建。

## 仅代码和依赖的预构建包

见 [分发说明](distribution.md)。平台 wheelhouse 避免重复解析依赖和编译 hnswlib；不省略数据构建或来源核验，不公开向量缓存。
