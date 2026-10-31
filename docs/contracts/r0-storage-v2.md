# R0物理记录存储v2

逻辑读取合同仍是固定快照的文件与top-level occurrence证据。该布局仅更改本次新artifact的存储，游戏/schema版本不因此变化；旧v1 artifact与receipt不修改。

| 项目 | v1 | v2 |
|---|---|---|
| `PRAGMA user_version` | 1 | 2 |
| metadata `schema_version` | `batch-m3-r0-physical-occurrence-v1` | `batch-m3-r0-physical-occurrence-storage-v2` |
| 物理记录存储 | `physical_occurrence`表 | `physical_occurrence_data`表 |
| 公开读取对象 | `physical_occurrence`表 | 同名只读视图，保持八列的名称、顺序和值 |
| snapshot/parser/run | 每条occurrence重复保存 | 从该记录引用的`source_file`取得 |
| value SHA-256 | 64字符hex | 32字节BLOB；视图还原相同小写hex |

v2的每条记录保留原occurrence ID、source-file ID、ordinal和pointer，重复原值仍各有物理记录。`source_file_id`提供已有物理文件归属，不推断任何逻辑实体身份。

构建器、独立source oracle分别核对固定来源完整文件集、原始token SHA、物理坐标、身份、顺序及行数。v2额外检查backing表完整数量、外键孤儿、哈希类型/长度与view可见行闭包，防止JOIN隐藏未核验的记录。未知布局拒绝。新artifact由实际size/SHA与实现身份绑定。

完整构建显式启用v2；底层`build_r0_artifact`保留v1默认供已有调用者使用。消费者对v2应使用该读取视图；写入封存证据不属于运行时合同。独立验证的破坏性测试仅修改可丢弃测试副本的backing表。
