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

Windows 使用 `.venv\Scripts\python.exe`；缺少适用 `hnswlib` wheel 时需 MSVC C++ Build Tools。离线环境可提前取得相同模型文件，但必须通过同一逐文件/目录核验。

## 构建与运行

完整命令见 [数据构建指南](building-dataset.md)，默认 `--stage all --model-cache data/model-cache` 包括向量阶段。明确不需要向量时使用 `--without-semantic`；缺模型或依赖会在大型构建前拒绝，而不是静默跳过。

默认完整配置仅在向量来源 oracle 通过后生成。基础数据配置为 `runtime-config.literal.json`，完整配置为 `runtime-config.json`。两份 profile 与绑定独立，不原地修改基础 profile。对已经由**同一实现与输入**验证的基础输出，可显式执行 `--stage semantic`，仍须完整回执与模型参数；旧代码或损坏/失败回执不能借此恢复。

每个窗口保留原 evidence ref、完整正文 SHA、窗口范围和窗口 SHA。重复字符串不合并物理 occurrence。独立 oracle 从来源索引重新枚举完整选择范围，核对遗漏、顺序、正文坐标、ref、向量及 lexical 行集，不只信任构建报告自己的计数。

向量不是 citation。搜索命中仍需正式 read/inspect；score 不是 confidence，相似性不证明人物、说话者、归属或剧情顺序。A/B 核对输入行集、向量值及确定性元数据；ANN 二进制是否逐字节相同另行报告。

## 验证范围

合成 fixture 覆盖窗口遗漏、重算 seal 后缺项、向量/来源篡改、重复物理文本、容量不足和运行时精确绑定；不下载游戏来源或模型。固定真实模型小型 A/B 另行记录，不代表完整 35 输入或全部游戏向量已经重建。查看 [当前状态](../status.md) 中的真实完整来源验收状态。
