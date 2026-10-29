# 默认完整构建与向量索引

完整构建默认在基础数据和 Research index 核验后，继续构建独立向量 overlay。它不重建或修改基础 Research index，默认搜索仍为 `literal`；`lexical`、`semantic`、`hybrid` 是显式的候选发现模式，不是证据或身份 authority。

## 准备

```sh
.venv/bin/python -m pip install -e '.[semantic]'
.venv/bin/python scripts/maintenance/fetch_semantic_model.py --cache-root data/model-cache
.venv/bin/python scripts/maintenance/fetch_semantic_model.py --cache-root data/model-cache --check
```

模型下载是上述命令明确执行的网络操作；构建器只读取本地固定模型，不自动下载、选择 latest 或换模型。模型来源为 `qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`，固定 revision `faf4aa4225822f3bc6376869cb1164e8e3feedd0`，逐文件与目录 SHA 见 [模型配方](../../recipes/embedding-model.json)。取得约 267 MB 原始模型文件所需空间需另外计入项目预算。下载失败的 `.part` 保留，确认失败后再明确处理，不冒充成功文件。

使用 multilingual MiniLM、384 维 float32、cosine HNSW，沿用 400 字符窗口、360 字符步长及既有高价值 CHS 字段选择。名称/标题另建 lexical 索引。范围见 [静态选择配方](../../recipes/semantic-input.json)，不为 ID、hash、纯玩法配置构建全文语义。

推理按窗口长度稳定组批，减少同批短文本的 padding，batch size 保持 64。Linux 构建默认最多使用 4 个 affinity-visible 线程，Windows 等其他平台保持 2；查询线程仍为 2。向量写回原 vector ID，ANN 仍按原 ID 顺序构建，物理 occurrence 不去重；来源选区、模型及 ANN 参数不变。`build.json` 的 `timings_seconds` 分别记录 selection、embedding（含模型初始化）、ANN 和 lexical 构建耗时。优化后的向量 SHA 和 ANN seal 仍须实测，不根据组批策略推断逐字节相同。

同一次构建中，逐字相同的窗口只推理一次，归一化后分别写入每个原始 vector ID；匹配使用完整字符串相等，不仅比较 hash。所有来源身份、物理窗口和 metadata 行分别保留，复用集合不跨构建保存。`embedding_batching` 记录 `unique_inference_windows` 和 `reused_occurrence_windows`；这些计数描述计算复用，不表示物理证据被合并。独立 oracle 仍枚举并核对全部窗口。

Windows 使用 `.venv\Scripts\python.exe`；缺少适用 `hnswlib` wheel 时需 MSVC C++ Build Tools。离线环境可提前取得相同模型文件，但必须通过同一逐文件/目录核验。

hnswlib 的原生二进制保存、独立核验和运行时加载在 Windows 使用 ASCII 临时路径，Python 负责中文路径的文件读写；不改变二进制格式或向量核验。系统临时路径含非 ASCII 字符时尝试 Windows 短路径；没有可用 ASCII 别名则明确失败，不修改代码页或进程工作目录。保存先核对原生预期字节数，再刷盘并排他发布；失败的私有文件保留，计入项目临时空间。临时卷还须容纳一个 ANN 文件副本。

安装 semantic extra 后，可运行不下载模型的真实 hnswlib 路径回归：`.venv/bin/python -m pytest -q tests/semantic_native.py`（Windows 替换 Python 路径）。此显式模块使用真实 builder、独立 oracle、运行时和 ANN，仅 embedding 使用合成向量；基础无 semantic 安装的源码测试仍用合成 ANN。两类测试都不能代替完整固定来源验收。

## 构建与运行

完整命令见 [数据构建指南](building-dataset.md)，默认 `--stage all --model-cache data/model-cache` 包括向量阶段。明确不需要向量时使用 `--without-semantic`；缺模型或依赖会在大型构建前拒绝，而不是静默跳过。

构建按文本字符长度稳定组批，减少无效 padding；写回时恢复原物理 vector ID，HNSW 仍按原顺序、单线程插入。相同文本不合并，来源窗口、模型和证据资格不改变。Linux 默认使用进程 CPU affinity 可见范围内最多 4 个 ONNX 线程，其他平台保持 2 个；CPU quota 较紧或需减少占用时可显式设置，例如：

```sh
.venv/bin/python scripts/build.py --source data/AnimeGameData \
  --historical-source data/AnimeGameData-history --output-root data/rebuilt \
  --model-cache data/model-cache --available-allocated-bytes HEADROOM_BYTES \
  --stage all --embedding-threads 2
```

该参数只控制构建，必须为正整数；不改变日常查询的线程预算。实际线程、组批策略和 embedding/ANN 耗时记录在 `semantic/build.json` 的 `build_parameters` / `timings_seconds`。构建线程进入恢复身份，不能更改参数后复用旧成功回执。实际测量与完整等价验证见 [Linux 性能记录](../linux-build-performance.md)。

基础数据配置为 `runtime-config.literal.json`，向量配置为 `runtime-config.semantic.json`。两份 profile 与绑定独立，不原地修改基础 profile。向量来源 oracle 和新运行配置 smoke 均通过后，才交付默认 `runtime-config.json`。对已经由**同一实现与输入**验证的基础输出，可显式执行 `--stage semantic`，仍须完整回执与模型参数；保留原 literal 配置，仅在默认指针精确匹配本次基础配置时切换。未知或被修改的默认配置拒绝覆盖；旧代码或损坏/失败回执不能借此恢复。

单独补建时保留初次基础构建的 source、historical-source 和 output 路径，例如：

```sh
.venv/bin/python scripts/build.py --source data/AnimeGameData \
  --historical-source data/AnimeGameData-history --output-root data/rebuilt \
  --model-cache data/model-cache --available-allocated-bytes HEADROOM_BYTES \
  --stage semantic
```

这会复核本次基础阶段的完整身份并新建向量，不重装 35 个输入，不读取另一部署的旧向量。来源路径也属于恢复身份，不能省略或猜测历史来源。

每个窗口保留原 evidence ref、完整正文 SHA、窗口范围和窗口 SHA。重复字符串不合并物理 occurrence。独立 oracle 从来源索引重新枚举完整选择范围，核对遗漏、顺序、正文坐标、ref、向量及 lexical 行集，不只信任构建报告自己的计数。

向量不是 citation。搜索命中仍需正式 read/inspect；score 不是 confidence，相似性不证明人物、说话者、归属或剧情顺序。A/B 核对输入行集、向量值及确定性元数据；ANN 二进制是否逐字节相同另行报告。

## 验证范围

合成 fixture 覆盖窗口遗漏、重算 seal 后缺项、向量/来源篡改、重复物理文本、容量不足和运行时精确绑定；不下载游戏来源或模型。固定真实模型小型 A/B 另行记录，不代表完整 35 输入或全部游戏向量已经重建。查看 [当前状态](../status.md) 中的真实完整来源验收状态。
