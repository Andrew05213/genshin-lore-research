# 非向量构建：性能与完整来源验证

测量日期：2026-10-28。基线实现为 `c5e7617e85861f09cef0250f7344c3d50a97c950`，优化实现为 `6d14f18a7575ec1c7d58f7ec2ea78733dc4a6577`。两侧使用同一干净的固定 7.1 checkout、历史字段连续性来源、Python/SQLite/依赖环境和 `--stage all --without-semantic`。所有构建与独立验证保持开启。计数、seals、全部阶段耗时及本地原始记录摘要见 [机器可读结果](reviews/nonvector-build-performance.json)。

这是源码重建和性能验证，不是修改已部署的数据库或正式 release。游戏文本、数据库、模型、向量和机器配置不进入 Git。

## 保留的修改

- CodexQuest 将 Git 查询从 builder/oracle 的 9/531 次减至各 3 次。两者分别取得自己的固定树清单，不共享投影结果；522 个 root 的已检查字节各自只读一次，仍完整核对来源及六张表的每一行。
- 七个支持文件的实际字节新增 Git blob 核验。此前组件 oracle 比较了报告中的 Git 身份和实际文件 SHA，却没有独立证明支持文件的实际字节属于该 Git blob；完整入口的干净来源检查仍保留。
- 独立 CodexQuest oracle 逐行比较数据库与来源重建行集，逐行累积原编码的 SHA。缺行、多行、不同内容仍拒绝；不再同时保留第二份完整查询结果与整表拼接序列化。
- CodexQuest 私有 writer 的 SQLite page cache 预算设为 128 MiB；这是单连接缓存设置，不是进程总 RAM 的硬上限。没有改变 schema、写入顺序、约束或 VACUUM。
- R0 仅在固定 JSON decoder 创建无环值树期间延后循环 GC，引用计数照常工作。退出解码区间前恢复原状态，包括非法 JSON、意外异常和调用方原本禁用 GC 的情况。解析、词法边界交叉核对、完整来源复验及独立 R0 oracle 不变。

CodexQuest 的新代码按原规则产生新的 parser/run 身份；派生绑定使用本次实际身份。旧 artifact、receipt 和已交付引用不改签。R0 的词法内容与坐标合同不变，GC 调度不是新证据资格。

## 定向交错对照

每组按 A–B–B–A 串行运行，两个独立进程重复；源字节、样本顺序、schema 和独立 oracle 相同。未清空操作系统页缓存，不宣称物理磁盘冷启动。

| CodexQuest，522 roots + 7 支持文件 | A：约 2 MiB writer cache | B：128 MiB |
|---|---:|---:|
| 第一次，含完整 oracle | 236.103 s | 150.176 s |
| 第二次，含完整 oracle | 230.920 s | 147.274 s |
| 总耗时中位数 | 233.511 s | 148.725 s |
| builder 中位数 | 151.188 s | 64.287 s |

总耗时中位数减少 **36.31%**。四个输出均为 870,895,616 B，SHA-256 均为 `b1f631bb258cff2a9efe506cb7edd8efbbfcec736fbeabccef8414c2706c9fa7`，六张表的完整行集 seal 也逐项相同。每轮均有 522 roots、118,866 children、1,044 navigation、1,037 candidates、710,709 text slots、705,107 text candidates。不能将这一组件比例外推为整库比例。

R0 从固定树按大小选择两个 TextMap 和两个 Excel JSON 文件，消费全部文件字节和 occurrence。正常 GC 的两次扫描为 11.897/11.952 s，局部延后 GC 为 10.472/10.368 s；中位数从 11.925 降到 10.420 s（**12.62%**）。所有文件和完整 occurrence digest 一致，峰值 RSS 均约 683 MiB。一次与容量盘点重叠的预跑保留记录，排除后先重做干净的 A1，再开始 B1；未替换失败或挑选更快结果。

## 未采用的试验

按基线实测投影成本，预先选择完整 Quest/Talk 与 Character foundation 两个输入，保持同一来源、1,388,601 条文本及两次完整独立 oracle。Research writer cache 从 16 增到 128 MiB，只使投影中位数减少 3.17%；总耗时从 181.852 增至 186.161 s，慢 2.37%。四个 1,826,783,232 B 输出的 SHA 完全相同，但未达到预定采用门槛，**继续保留 16 MiB**。

用相同四个 R0 文件试验编译正则扫描四种 JSON 空白，完整扫描中位数从 10.258 增至 10.480 s，慢 2.16%，未采用。没有更换 JSON parser、裁剪来源或减少验证来制造提速。

## 全链路与资源

| 完整非向量进程，每侧一个新输出 | 基线 | 优化版 |
|---|---:|---:|
| 进程 wall | 39:31.15 | 37:33.63 |
| max RSS | 4,118,424 KiB | 4,067,648 KiB |
| 已核验数据库阶段 / 最终输入 / core providers | 60 / 35 / 33 | 60 / 35 / 33 |
| 完整来源 oracle、FTS、运行时 smoke | PASS | PASS |

整个进程实际减少 **117.52 s（4.96%）**，改善约 5%，不能称为整库大幅提速。CodexQuest 阶段从 207.545 降至 141.273 s，R0 从 497.253 降至 431.480 s。R0 的 4,711,841,792-byte 数据库 size/SHA 与基线一致。其余未修改阶段的波动和必要验证耗时也计入整个进程，没有只展示快的部分。

阶段回执中的总时长和末尾资格分项分别采样，因此分项之和不保证精确等于阶段总时长。上表的整个进程 wall 使用独立进程计时，不通过相加这些分项计算。

统一索引均有 921,382 containers、3,761,013 texts、663,590 relations 和 446,132 candidates；两次完整独立来源核对通过。35 个输入的 family/contract、六张索引表计数相同。CodexQuest 的六张表另按来源物理坐标、完整原文字节/文本、状态和导航目标逐行对照通过；声明变化的 parser/run/派生 ID 分别通过各自来源 oracle，不将其抹去或宣称逻辑身份相同。

补充实际 stdio 检查通过：五工具请求、Book 85 个物理行经 12 页读至文件 EOF、Story 续读 400..576 字符至当前记录 EOF并 inspect 区间 hash、对白两页正文/provenance 回读，以及错用 page limit 的 cursor 拒绝。EOF 仅属于该文件或记录。

两次完整重建均使用新输出目录，没有恢复旧数据阶段；固定来源通过本地 Git 对象创建干净工作树，不是一次从零联网复现实验。整个非向量进程每侧一次，不能估计完整运行的波动或保证其他机器提速。两侧的代码 checkout、输出目录和未触发上限的容量预算不同；CPython 3.12.3、SQLite 3.45.1、mcp 2.1.1、NumPy 2.5.3、jsonschema 4.26.0、来源和性能参数相同。向量阶段另作集成验证，单独报告，不计入上述非向量比较。

完整来源的索引压缩峰值保留约 25.78 GB，因此基础预检从 25 收紧到 **26 GB**，默认含向量预检相应为 **28 GB**。项目 hard 仍为 90,000,000,000 B，warning 仍为 77,309,411,328 B。预算不包括尚未取得的来源和依赖。对照副本在核验 seal、关闭数据库并保留全部报告后逐份退休；不删除部署输入、来源、WIP 或原始失败记录。

## 默认向量路径的单独集成验证

在上述优化版基础数据上，按相同实现/来源/历史来源重新核对 v3 回执，执行 `--stage semantic`。没有重装 35 输入，也没有读取旧向量：新建 105,265 个物理 occurrence、106,589 个窗口对应的全部向量行。沿用已有单次构建内精确窗口推理复用，实际推理 67,104 个不同窗口，39,485 个重复窗口保留各自物理映射。

进程 **10:00.13**；builder 519.747 s，其中 embedding 478.303 s、ANN 18.496 s；max RSS 1,412,548 KiB。模型仍为 multilingual MiniLM、384 维，FastEmbed 0.7.4 / ONNX Runtime 1.30.0 / hnswlib 0.8.0 / NumPy 2.5.3，构建 4 线程、batch 64。完整来源选择、窗口/ref/坐标、lexical 行集与存储向量绑定 oracle PASS；ANN 取回与原向量最大绝对差 `1.043081283569336e-7`，保持原验证门槛。

新配置的实际 MCP 检查 PASS：literal、lexical、semantic、hybrid 都可发现候选并正式 read/inspect；默认与显式 literal 一致，literal cursor 不重复，五工具顺序不变。默认配置切换只发生在这次生成目录，literal 配置保留；没有更改项目日常部署。没有运行新的语言模型答案矩阵，也不宣称新的 relevance 或逻辑身份资格。

这一集成运行没有对照 arm，不能声称向量提速；也不能把它与 37:33.63 相加后冒称单个 `--stage all` 进程的成绩。模型、embedding 算法、来源配方和向量规模未修改，向量和模型保持本地留存。

## 回归、留存与限制

实现提交的 Ubuntu / Windows 工程 CI 各 **619 项通过**，分别 144.92 / 655.18 s；包括完整性、恢复、MCP、cursor 与新来源/GC/容量负测。既有重点模块覆盖率为 77%，不是整个代码库覆盖率。编译、六个公开命令 help、Markdown 链接、Apache-2.0、wheel 与两平台源码/依赖包检查通过。CI 不替代上述真实数据构建，原生 Windows 的完整 35 输入性能仍未重跑。

最终本地全套 **619 项 / 113.65 s**，真实 hnswlib 合成路径另 **7 项 / 1.69 s**。wheel 核对的是 Apache-2.0、代码模块与不夹带数据库/模型/向量；项目仍按 [源码 checkout](development.md) 分发，SQL/合同随源码包，不能把单个项目 wheel 当作独立部署。

两次完整来源构建均通过。与测量相关的原始诊断保留，包括与容量盘点重叠的预跑和未采用的优化。

最终 WIP 检查曾因 ONNX Runtime 新生成的 51-byte `:memory:.ses` 文件失败。隔离进程仅构造 `InferenceSession(bytes)` 就复现该文件；精确归档两个本轮生成文件后，**同一个** authority/WIP 检查与初始快照完全一致。原失败、文件原始字节和归档回执保留，没有忽略差异或改验收条件。ONNX/现有 FastEmbed 提示也保留；此次不修改模型或第三方 telemetry 行为。该库在工作目录生成少量 session metadata 是尚未整改的运维边界，不属于游戏数据或引用资格。

相同范围的项目 allocated 从 52,043,014,144 B 到 **71,904,923,648 B**，保留新的完整来源数据及向量；hard 余量 **18,095,076,352 B**，低于 warning，主机空闲 146,015,932,416 B，unknown 为 0。按 device/inode 去重，对照冗余已真实 unlink；session 文件只归档，不冒称其移动释放空间。已部署数据与原有 WIP 的完整快照核对通过。

整套新数据仍为本地来源核验产物；没有修改正式 current、release receipt、SQL schema、证据格式、关系证明或发布数据。无新数据层、缓存服务或通用构建框架。

## 复现

按 [完整重建指南](guides/building-dataset.md) 准备干净固定来源、历史 oracle 来源和构建依赖。在基线和优化两个代码 checkout 中，分别使用相同 Python 环境、来源和以下参数，输出到不同的新目录：

```sh
python scripts/build.py \
  --source data/AnimeGameData \
  --historical-source data/AnimeGameData-history \
  --output-root data/nonvector-build \
  --stage all --without-semantic \
  --textmap-cache-mib 1024 \
  --available-allocated-bytes HEADROOM_BYTES
```

`HEADROOM_BYTES` 必须使用实际项目 allocated 盘点，不用主机空闲代替。两个完整输出不适合在同一预算下同时重建；已核验的离线对照可按留存规则先保留报告与比较所需数据，再退休可重建冗余。不要覆盖旧失败目录或用 `REUSE_VERIFIED` 当作新的冷构建。
