# 架构与代码目录

项目的核心是保存和检索可回查的原文证据，不在存储层自动生成剧情解释或合并人物身份。

## 数据流程

```text
固定上游快照
  → 各类来源解析与独立组件数据库
  → 来源、结构和投影核验
  → 数据清单、绑定与统一 Research 索引
  → 只读 MCP 工具
  → Agent 阅读、解释与引用
```

完整有界数据入口按上述流程生成 35 个输入、统一索引与本次来源绑定。它不继承原部署 acceptance，也不自动构建可选 semantic overlay；实际验证范围见 [当前状态](STATUS.md)。

## 目录

| 路径 | 职责 |
|---|---|
| `src/genshin_lore/` | 来源解析、数据库构建、独立验证、adapter、检索与服务实现 |
| `schemas/` | 独立组件 SQL 定义 |
| `migrations/` | core 数据库的版本化迁移 |
| `manifests/` | 构建范围、来源身份和结构清单；不含部署 current/receipt |
| `scripts/rebuild.py` | 公开版已接线的组件构建与验证入口 |
| `scripts/rebuild_complete.py` | 全部有界数据输入、core 装配、完整索引及独立核验 |
| `scripts/rebuilt_runtime.py` | 新生成配置的只读 MCP 启动与运行 smoke |
| `scripts/check_rebuilt_mcp.py` | 真实 stdio 传输、五工具调用和正文续读检查；不是模型验收 |
| `scripts/` 其他文件 | 复用的原组件构建器、独立 oracle 与辅助入口 |
| `tests/` | 不携带游戏原文的合成测试 |
| `data/` | 默认生成数据目录，不提交 Git |

## Research 工具实现

统一检索服务保留五个工具：

| 工具 | 职责 |
|---|---|
| `describe_corpus` | 声明数据范围、可用类型及限制 |
| `search_evidence` | 定位候选材料，返回来源与检索信息 |
| `read_evidence` | 有界读取同一物理容器内的正文和上下文 |
| `follow_evidence` | 沿有来源依据的物理关系导航 |
| `inspect_evidence` | 核对引用的身份、坐标与来源记录 |

另外包含独立的 query/inspect 便利接口、Book 物理行续读和 Story/Readable 字符续读实现。工具注册与实际服务可用性不是一回事；启动还需要经过核验的数据清单、profile、绑定和索引。

## 证据语义

- **快照**：输入 Git commit/tree 的固定来源身份，不是官方首次实装证明。
- **解析器与导入批次**：记录某次解析规则及其来源范围。
- **物理 occurrence**：具体文件/行/记录中的出现位置，不是跨来源逻辑实体。
- **观察与范围**：某个文本字段及实际读取区间。片段 SHA 与全文 SHA 不能混用。
- **候选关系**：物理定位或源字段支持的关联，不自动证明唯一 owner、speaker 或剧情时序。

Semantic 索引只扩展或排序发现候选，不能作为引用来源。引用指向回读核验过的 evidence；引用 transport 只传递模型选择的标识，不修改事实或替换选错的材料。文学解释与预测属于研究结论，不写回原文身份。
