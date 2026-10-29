# 完整冷构建：阶段重叠与 token 分组

2026-10-29，在相同 Linux 环境、固定来源和模型上，各运行一次新目录的完整默认向量构建。串行 main 基线 `d7c51817f30c093e87143965c14e05cb2add73c0` 为 **48:01.72**；优化实现 `2bafeca315746326b58994dfacb3633c3016ff29`、`--jobs 3` 为 **33:53.58**，减少 **14:08.14（29.43%）**。40 分钟目标达到，30 分钟目标尚未达到。

这是两个完整单命令进程的 wall time，包含 35 输入、33 个 core providers、60 个数据库阶段、统一索引、向量和生成配置的运行时检查；没有相加历史分段成绩。来源、依赖及固定模型已事先取得，不包含下载和安装时间。新输出与进程内输入缓存为空，操作系统页缓存未清空；每侧一次，不能估计全程波动或保证其他机器同样提速。详细计数、seals、各阶段时长和本次检查见 [机器可读记录](reviews/cold-build-throughput.json)。

## 保留的优化

- **固定独立链重叠**：默认 `--jobs 2` 将 R0 提前；`--jobs 3` 另提前 Quest 六组件链。只并行这两条已核对依赖的链，不自动推断任务图。进程隔离 GC、输入缓存和 SQLite writer；到原消费屏障时等待，并核对每个 artifact、v3 回执、来源/实现/报告身份。退出码为零不代替资格验证。`--jobs 1` 保留串行路径。
- **独立 oracle 批量物理回读**：每批最多 256 个请求，按来源和声明的物理键读取。重复请求保留各自 ordinal，缺失、歧义和键作用域错误仍拒绝。容器关联及候选数量使用完整关联检查；压缩前后两次完整 oracle、来源重建与关系 rowset 检查均保留。
- **按实际 token 数推理**：使用同一个固定 FastEmbed 后端 tokenizer，分块计算非 padding token 数，稳定分组后推理，再回填原物理 vector ID。推理 batch 为 32，ANN 插入仍按原 vector ID 顺序。模型、精度、384 维、输入范围、窗口、归一化和 HNSW 参数不变；不建立跨构建向量缓存。

本次环境有 8 个可用 CPU、约 16.76 GB RAM 和 4.29 GB swap；两侧 ONNX 构建线程均为 4、TextMap reader 缓存均为 1024 MiB。独立 worker 不保留 TextMap 输入缓存。并行使用更多内存，不能把 worker 数当作内存上限。

| 指标 | 串行基线 | 优化版（jobs=3） |
|---|---:|---:|
| 整个默认向量构建进程 | 2881.721 s | 2033.584 s |
| R0 自身构建与验证 | 436.166 s | 480.431 s |
| Research 索引完整阶段 | 634.100 s | 582.514 s |
| embedding（含本轮 token 计数） | 481.829 s | 372.429 s |
| 向量 builder（含 ANN、lexicon） | 521.096 s | 411.681 s |
| 采样进程树 RSS 最大值 | 4.247 GB | 6.291 GB |

R0 在并行争用下自身更慢，但与主链重叠，故总 wall time 降低。索引阶段包括投影、FTS、两次完整来源 oracle、VACUUM 和资格封存。进程树 RSS 每 0.5 秒采样并累加父子进程，可能重复计入共享页，也可能漏掉短峰值；不是主机实际物理内存峰值。阶段回执分别取时，不能相加充当整个进程计时。

## 正确性与身份边界

两侧均通过完整来源核对：921,382 containers、3,761,013 texts、663,590 relations、446,132 candidates。适配覆盖行集一致；关系/候选完整 rowset、来源 seals、二跳和绑定分别由每侧独立 oracle 核对，不用代表性抽样替代。

**不声明两个输出数据库或所有 evidence ref 字节相同。** 既有 core 配置保存本次输入 artifact 路径，派生 run 绑定本次映射/依赖 seal，部分 Native scope 还绑定本轮资格回执。因此新目录和新回执会产生不同的封装 SHA、parser/run 或派生 ID；严格原始行集比较保留这些差异。没有重标旧 ref，也没有将某侧新身份解释成另一侧身份。

补充全量对照独立比较每条文本的 family/contract、容器物理坐标、来源文件 SHA、locale、字段角色、ordinal、TextMap 物理位置及完整原文，保留重复项数量。**3,761,013 条文本的物理坐标与原文 multiset 一致。** 这证明对应固定来源材料一致，不合并逻辑身份。

两侧都有 105,265 个物理 occurrence、106,589 个窗口，实际推理 67,104 个不同窗口，重复窗口保留独立映射。按完整来源坐标和窗口范围对齐后，**全部 106,589 个 float32 向量值逐字节一致，最大差为 0**。派生 ID 导致部分行序不同，不能按相同数组下标盲比。每侧完整窗口/ref/范围/lexical/ANN 绑定 oracle 也单独通过，ANN 取回与各自原向量最大绝对差均为 `1.043081283569336e-7`，原门槛不变。

实际 stdio MCP 检查通过：五工具请求、Book 85 行经 12 页到当前文件 EOF、Story 400..576 字符续读与 range inspect、对白两页及错误 cursor 参数拒绝。优化版另通过 literal/lexical/semantic/hybrid read/inspect、默认 literal 一致性及不重复分页。这是程序化 transport/readback 检查，不冒充新的模型自主答案矩阵或正式 release 验收。

## 定向测量与未采用的实验

同一固定索引的完整 oracle A–B–B–A，原实现中位数 163.860 s，批量回读 130.255 s，减少 20.51%；四次完整返回报告完全一致。新目录全程中的索引阶段改善较小，以上表实际结果为准。

4096 个均匀抽取的真实物理窗口、固定模型与 4 线程的暖模型调度实验中，字符分组/实际 token 分组各两次交错运行，后者中位数减少 19.09%，包含额外计数成本，向量完全一致。32/64/128 batch 的双向顺序实验中，32 比 64 中位数快约 4.46%，六次向量完全一致。它们是局部调度实验，不是完整冷构建重复次数。

容量上限 4096 条的 owner 查询缓存对照反而慢约 8.52%，已撤回；线性 source-column materialization 的完整 oracle 变化约 0.29%，属波动范围，不据此宣称提速。原记录均保留，不只保留快的结果。

## 验证与留存

实现的完整合成回归 **652 项通过**；另 15 项仓库/help/链接/许可和真实 hnswlib 合成检查通过。初轮两个模拟 tokenizer 子类初始化失败保留，补齐 fixture 初始化后用原断言复验通过。Ubuntu/Windows CI 各 652 项通过，编译、六入口 help、wheel、源码/依赖包检查通过；重点构建/配置/传输/对白模块覆盖率为 78%，不是整个项目覆盖率。新并行参数的原生 Windows 完整来源性能仍未运行。

完整原始日志、失败、来源 oracle、逐文件 seal 和退休回执保留在本地实验目录，不随源码公开。完整对照通过后仅退休已核实不再使用的实验副本；优化版完整输出、固定来源/模型、部署和其他 WIP 保留。项目采用 90,000,000,000 B hard、77,309,411,328 B warning，构建前按完整项目范围去重盘点；warning 不是停止条件。未改正式 current、receipt、schema、证据语义、检索模式或数据覆盖。

## 复现

按 [完整构建](guides/building-dataset.md) 与 [模型准备](guides/building-semantic.md) 取得干净固定来源、历史 oracle 来源、依赖及模型。每侧顺序运行，使用各自新目录和实际测得的项目预算余量：

```sh
python scripts/build.py \
  --source data/AnimeGameData \
  --historical-source data/AnimeGameData-history \
  --model-cache data/model-cache \
  --output-root data/rebuilt-throughput \
  --stage all --jobs 3 \
  --textmap-cache-mib 1024 --embedding-threads 4 \
  --available-allocated-bytes HEADROOM_BYTES
```

`HEADROOM_BYTES` 至少满足 28 GB 全构建预检，不能用主机空闲代替。上面的 `--jobs 3` 是本次实测参数；保守默认仍为 2，低内存可显式选 1。旧串行 main 基线尚无 `--jobs` 参数，控制组省略该参数。不要同目录启动两个协调进程，不覆盖失败输出，不把 `REUSE_VERIFIED` 写成新的冷构建。不同 output-root 的完整映射与 sealed 身份必须分别核验。
