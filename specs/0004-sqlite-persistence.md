# SQLite：书籍身份、章节索引与任务持久化

状态：架构提案，尚未实现。日期：2026-09-16。

实施顺序与验证方式以 [人机协作实现 TODO](../TODO.md) 为准：SQLite 随可用功能逐步接入，Codex 提供运行/恢复脚本，用户执行并确认持久化与恢复表现。

配套文档：[总体架构](0002-conversion-architecture.md)、[书籍结构与双版本导航](0003-book-structure-and-navigation.md)。本次不创建数据库、不安装依赖、不迁移已有数据。

## 1. 引入时机和职责

建议在第一阶段引入 SQLite，先替代进程内任务状态并保存来源、书籍版本和稳定身份；章节模型落地时增加结构修订和导出位置表。不必等接入小模型才引入数据库，因为改章名、调整层级、重复导出和跨版本定位已经需要持久状态。

采用一个应用级 SQLite 数据库，按 book/edition/revision 隔离。初期适用于单机应用和同机 worker，不每本书建一个独立库；导出的书籍包通过 JSON 索引独立分发，不要求读者安装数据库。

| 放 SQLite | 放文件系统 |
|---|---|
| 书籍/版本身份、来源元数据及内容哈希 | 原始 EPUB/PDF/DOCX、网页快照 |
| 节点身份、章节层级修订、人工覆盖记录 | 完整内容 IR/页级结果等不可变快照 |
| 转换任务、阶段状态、租约与重试记录 | 页面图、OCR 原始响应、图片、模型文件 |
| 导出计划元数据、产物/位置/旧路径映射 | 整体/分章 MD、HTML/PDF、ZIP |
| 缓存元数据、文件引用及保留信息 | 缓存的较大二进制内容 |

大量二进制不默认作为数据库 BLOB；数据库只保存存储键、哈希、大小和类型。路径基于 storage root 的相对键，便于移动整个工作目录；外部来源 URL 和用户展示名独立存储，不拼入路径。

## 2. 事实来源、快照与一致性

SQLite 是身份、任务状态、已发布修订和当前导出指针的事实来源。正文使用不可变内容快照，数据库引用其存储键和哈希；结构修订采用关系记录。一个已发布 revision 同时绑定结构版本和内容快照，二者不能单独原地覆盖。

`book.json` 是该 revision 的完整可移植快照，`toc.json`、`locations.json`、MD 是该导出版本的派生产物。它们均从已提交 revision 和同一 ExportPlan 生成，不允许数据库、TOC 与 MD 各自修改章节结构。

人工修订只生成新 revision，记录基于哪个旧 revision、改变了哪些节点及原因；旧导出仍指向旧 revision，保持可复现。用户手动编辑导出的 MD 不自动回写数据库，未来若支持回导，必须走显式差异导入流程。

数据库丢失时，完整的源文件、book.json 和 manifest 可以作为恢复输入，但运行中的任务、未导出的修订或缺失的身份记录不保证重建。备份应包括数据库与被引用的文件，不能把“存在导出 JSON”当成完整备份。

## 3. 逻辑数据模型

以下是职责设计，不要求首轮一次建齐全部表。与任务/来源无关的章节表在书籍 IR 阶段引入，缓存和搜索后置。

| 逻辑表 | 主要关系和字段 |
|---|---|
| `books` | `book_id`、标题、作者、创建时间；书名不是唯一键 |
| `editions` | `edition_id`、`book_id`、来源版本说明；不同版次显式区分 |
| `sources` | `source_id`、`edition_id`、格式、内容哈希、storage key、原始文件名/URL、获取时间 |
| `revisions` | `revision_id`、`edition_id`、parent revision、内容快照键/哈希、schema/pipeline 版本、发布状态 |
| `section_identities` | `node_id`、`edition_id`、首次来源键、创建信息；跨修订复用身份 |
| `section_versions` | 复合键 `(revision_id, node_id)`，父节点 ID、同级次序、角色、标题、原书编号、范围及识别信息 |
| `section_lineage` | 旧/新 revision 与节点 ID、改名/拆分/合并/替代关系及置信；允许多对多 |
| `structure_overrides` | 基准 revision、目标节点/来源范围、修订操作、作者/规则和时间；保留审计 |
| `jobs` | job ID、source/revision、目标格式/配置哈希、幂等键、状态、lease token、lease expiry、取消标记 |
| `job_steps` | job、stage、page/region、输入/参数/引擎版本摘要、attempt、状态、结果键和错误；按需启用页级粒度 |
| `exports` | export ID、revision ID、导出配置/计划摘要、状态、主产物 ID、发布时间 |
| `artifacts` / `artifact_dependencies` | export、相对键、角色、哈希、类型、大小、状态、资源依赖；不只保存文件名 |
| `export_locations` | export、node/block、view、unit、segment、artifact、anchor、文本范围；对应 locations.json |
| `path_aliases` | 旧 export/path/anchor → 新位置或多个候选；不能仅以书名识别 |
| `cache_entries` | 阶段缓存键、资源引用、版本、访问/保留信息；可以删除和重建 |
| `schema_migrations` | 数据库 schema 版本和迁移记录 |

`section_versions` 的 parent 必须在同一 revision，引用复合键；身份归属同一 edition。导出位置必须属于同一 export，产物/视图/节点关系通过约束与应用验证保证。数据库外键不能检查树无环和内容不重不漏，这些由结构发布前校验。

阶段 1 最小实现可从 sources、jobs、job_steps、exports、artifacts 和迁移版本开始，book/edition/identity 随首个书籍闭环一起加入；无需实现全部未来关系表才允许转换。

## 4. 快速定位的查询路径与索引

| 用户动作 | 查找方式 |
|---|---|
| 展开目录某一层 | 按 `(revision_id, parent_id, sibling_order)` 索引读取子节点 |
| 获取整棵树/祖先路径 | 同 revision 的递归查询或读入缓存，不把文件目录当作章节真相 |
| 从章节跳整体版/分章版 | `(export_id, node_id, view)` 找路径和锚点，分段节点返回主位置与片段 |
| 从原 PDF 页查章节 | 查来源范围投影，页/区间与节点多对多；进一步用 bbox 定位 |
| 章节改名后解析旧位置 | 按旧导出和旧路径查询 alias/lineage，必要时返回歧义 |
| 恢复中断的 OCR | 按 job、stage、page/region 找已提交结果及过期租约 |
| 找当前可下载产物 | 查询已发布 export 的主 artifact，再经 ArtifactStore 解析文件 |

必要索引还包括 `(edition_id, source_locator)`、`(job_id, stage, page_or_region)`、`(status, lease_expiry)` 和缓存键唯一索引。源文件哈希可去重二进制内容，但不自动合并不同书籍记录。

正文检索是独立可重建投影，可在后续评估 SQLite FTS5；中文切词、子串定位、tokenizer 可用性和索引大小需实测。核心章节树导航与节点定位不依赖全文搜索，更不依赖向量数据库。

## 5. 事务、worker 与文件发布

SQLite WAL 可以让读写更好地并行，但同一时刻仍只有一个写事务，且依赖同机共享内存，不适用于网络文件系统上的多机共享数据库。[SQLite WAL 官方说明](https://www.sqlite.org/wal.html)

建议采用短事务、有界批量写入、busy timeout 和有上限的锁冲突重试。每进程独立连接，避免把连接跨 worker 传递；连接初始化显式启用外键约束。[SQLite 外键官方说明](https://www.sqlite.org/foreignkeys.html)

OCR、PDF 渲染、网络请求和大文件写入都在事务外运行。任务领取用原子条件更新（仅 queued 或租约已过期的任务），成功时生成新的 lease token；续租、提交结果和发布都检查 token，防止超时旧 worker 覆盖新 worker 结果。

任务不能先读 pending 再无条件写 running。重复提交通过幂等键处理，缓存键另行计算；缓存不能替代任务状态或取消语义。页级失败只重试失败页，已完成结果通过内容/参数摘要校验后复用。

文件系统和 SQLite 不存在自动跨介质事务，采用以下发布协议：

1. 建立 staging export 记录，记录 job、revision、计划摘要和租约。
2. 在唯一 staging 目录写入产物，计算哈希并校验全部资源、双视图锚点和内容覆盖。
3. 将 staging 目录在同一文件系统内原子更名到新的不可变 export 路径。所有产物先落盘，不能直接覆盖当前版本。
4. 短事务再次校验 lease token 与任务状态，提交 artifact/location/manifest 引用，将 export 标为 published，并更新当前导出指针。
5. 崩溃恢复检查 staging 记录与文件：未发布文件不会对外暴露，可校验后完成提交或延后回收；数据库宣称已发布但文件丢失时标为不可用，不仍显示下载成功。

目录更名本身不等同于断电持久性保证；实现时根据要求处理文件与目录同步。下载只读取 published 产物。最终取消与发布的竞争由提交事务中的状态/token 校验决定。

模型 worker 建议把阶段事件提交给应用协调器，由其汇总写数据库；若同机多个 worker 直接写入，也必须复用相同仓储、事务与租约规则。无需为了并行 OCR 长时间占用数据库写锁。

## 6. 保留、恢复和迁移

- 区分长期书库与短期上传任务：原书、身份、已保留修订/导出不使用当前“一小时目录过期”规则。临时页图、失败 staging 和可重建缓存可以另设 TTL。
- 回收先检查活跃租约、数据库引用和保留策略，标记待删除后再删除文件；支持重复执行和崩溃恢复。共享图片不能因为一个 job 到期而删掉。
- 用数据库一致性备份方式保存 SQLite，并记录对应的产物快照/哈希清单；WAL 活跃时不能只随意复制主 `.sqlite` 文件。恢复时验证数据库、身份、快照与文件引用一致。
- 数据库 schema、IR schema、流水线版本、模型版本和导出配置版本分别记录，避免混成一个版本号。迁移有版本记录和失败处理，破坏性升级前保留备份。
- Repository 隔离 SQL；模型和 Writer 不持有连接。后续若多机部署、写入争用或共享服务规模超出单机边界，可替换 PostgreSQL 等实现，书籍模型与产物协议保持不变。

建议运行目录：

```text
var/
  catalog.sqlite
  sources/                     # 长期原书/来源快照
  revisions/                   # 不可变内容与结构快照
  exports/                     # 不可变发布版本
  staging/                     # 未发布产物
  cache/                       # 可重建阶段结果
  models/                      # 可配置到其他位置
```

## 7. 分阶段验收

1. 任务状态替换后：服务重启仍可查询任务；同机两个进程不能同时成功领取同一 lease；当前 worker 的结果不会被旧 token 覆盖。
2. 书籍结构持久化后：改标题不改节点身份；旧修订和旧导出可查；章节合并/拆分有 lineage，树父子关系和顺序可复现。
3. 双版本导出后：每个目录节点能从 SQLite 和导出 JSON 得到一致的位置；不运行服务时，解压包内导航仍有效。
4. 发布与恢复：在文件更名和数据库提交之间模拟中断，不暴露半成品；重试不重复发布，不遗失已保留的书籍/导出。
5. 回收与备份：活跃任务和共享资源不误删；数据库与引用文件备份恢复后，身份、目录、链接和产物哈希均一致。

SQLite 的价值首先是保住书籍身份、目录修订和转换状态。性能指标以真实书籍数量、页数和并行任务实测，暂不预设需要消息中间件、全文索引或更重数据库。
