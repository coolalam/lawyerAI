# jw_db 完整迁移与验收

## 已确认的源结构

以 `migration/jw_manifest.json` 中 60 张表为固定迁移范围，来自对 `jw_db` 的实际只读结构检查。`migration/jw_dictionaries.json` 保存旧系统数据字典，包含案件状态、当事人关系、案件类型、币种及用印审批状态；不包含业务人员信息或密码。

| 业务 | 主表 / 关键依赖 | 必须保留的关系 |
| --- | --- | --- |
| 案件 | `t_case`, `t_caseend`, `t_case_court`, `t_case_pay_detail`, 案件进度附表 | `ca_office_id + ca_case_id` 复合键 |
| 当事人 | `t_client`, `t_case_client_relation_info` | 客户与案件都要包含分所，不能只按名称或案号关联 |
| 律师 | `t_employee`, `t_case_lawer_ref` | 案件律师包含分所、案号、员工及工作类型 |
| 利冲 | `t_conflict_search_mstr`, `t_conflict_search_det` | 主表检索 ID + 明细行号，保留全部历史结果 |
| 财务 | `t_invoice`, `t_invoice_share_set`, `t_payment`, `t_payment_sharer` | 案件分配、发票分配、到账分配分别保存，发票号不默认全局唯一 |
| 文档 | `t_document`, `t_doc_version`, 文档审批和分类表 | 保留原文件路径、文件起始偏移、长度、私有标记及全部版本 |
| 用印 | `xz_seal_request` 及审批、操作历史 | 源表没有案件外键，需要明确关联；不按名称猜测 |

## 两层存储

1. **完整原始层 `src__*`**：每一列、每一行都保存。源主键、复合主键、无主键表的重复行、NULL、空字符串、长文本和二进制均保留。额外使用 `_migration_row_id` 保留行位置，避免无主键表数据被去重。原始数值以文本保存，BLOB 以 BLOB 保存；源类型由元数据记录。不会因 SQLite 类型亲和性把大整数或 DECIMAL 变成浮点数。
2. **业务层 `records`**：将明确的字段投影到案件、当事人、律师、财务、文档、用印、利冲等模块。每条记录保存 `source_key`、完整 `legacy_data` 和历史状态；内部 ID 由源表及完整主键确定，避免不同分所同案号碰撞。合同标记为真时生成历史合同记录，其用印与归档状态仍须确认。

未直接投影为新业务对象的关联表仍完整保存在原始层，可以在管理员源表浏览器查看；案件相关表、利冲明细和文档版本还提供按业务记录查询的入口。保留原始表并不意味着已经完成了这些旧系统自定义业务行为的复刻，迁移报告会单独列出 `raw_only_tables`。

## 只读预检

密码可通过交互输入，或通过进程环境变量 `SOURCE_MYSQL_PASSWORD` 提供；不要写进命令行参数或版本库。

```bash
.venv/bin/python scripts/migrate_mysql.py \
  --host 192.168.3.2 --port 3366 --user delitest --database jw_db \
  --plan --count --output artifacts/preflight-new.json
```

预检输出表结构变化、存储引擎、准确记录数。源库访问使用 `READ ONLY` 一致性快照，全部源操作都是查询。目标文件存在时直接拒绝覆盖。

本次预检结果保存在 `artifacts/jw_migration_plan.json`，结构错误为 0、非 InnoDB 表为 0。正式迁移前重新预检，以实际新快照为准。

## 状态与关联规则

`migration/rules.example.json` 是规则模板。只有确认旧系统含义后才填写：

- `statuses.t_invoice` 的键为 `iv_status|iv_app_status`，值为 `draft/submitted/approved/issued/collected/rejected/red_requested/red_reversed/void`。
- `statuses.t_payment` 的键为 `pay_status`，值为 `draft/confirmed/void`。
- `statuses.t_conflict_search_mstr` 的键为 `csm_status`，值为 `pending/cleared/waived/blocked`。
- `seal_case_links` 的键为原始 `xzsr_id`，值为 `[分所ID, 原案号]`。

从已读取的数据字典确认的案件和用印状态已内置。发票申请状态已按 JianWeiOA 源码核对（见下表）。到账状态也已核对（见下文）。其余未确认的值保留为 `legacy:原值`，不计入已确认财务汇总，并写入 `migration_issues`。旧利冲审查可以作为历史记录查询；恢复新的立案审批时应按当前当事人信息重新生成检索快照，避免把旧审批直接当作当前授权。

发票映射依据 `源代码/financeinvoice/invoiceReceiveList.aspx.vb` 的 `getApplyStatus`，并以同目录的 `invoiceApplyList.aspx.vb`（Z）和 `invoiceReceiveList.aspx`（S）补全待审批编码。`iv_status` 是到账 / 作废状态，审批阶段取 `iv_app_status`：

| 原申请状态 | 新状态 | 页面名称 |
| --- | --- | --- |
| N | draft | 未提交 |
| Z / S | submitted | 行政审批 |
| A | approved | 财务开票中 |
| B | issued | 已开票待领取 |
| C | collected | 发票审领成功 |
| D | rejected | 申请被退回 |
| X（或 iv_status=X） | void | 已作废（历史） |

例如 `A|Z` / `AZ` 为行政审批，`A|D` / `AD` 为申请被退回。历史作废不等同于新增的“已冲红”。已有业务记录在读取、筛选、导出和金额汇总时统一解释编码，保留原始 JSON、版本及源字段；新导入记录直接使用已确认状态，显式迁移规则仍优先。已开票待领取、审领成功、冲红申请均计入开票金额，财务确认已冲红后排除；历史作废和未知状态不计入。

到账状态依据 `源代码/financereceive/receivecaseregister.aspx.vb`（新登记 A）、`receivemain.aspx.vb`（发送合伙人 S、删除 X）、`myreceivelst.aspx`（确认待分配 Y、已分配 B），并由 `invoicemain.aspx.vb` 的非 X 到账合计口径交叉验证。A/S/Y/B 均表示款项已到账，映射为 `confirmed`（已到账）；X 映射为 `void`（已冲销）。后续分配阶段的源状态保留在原始字段中，不等同于款项未到账。未知值继续保留为 `legacy:原值`，页面显示“到账状态待核对”。读取、列表过滤、导出、财务汇总、重复流水检查和冲红前到账检查使用同一映射；无需批量改写已迁入记录。

律师分配中的异常比例、原金额小数位超过新业务精度、负数/红冲金额、自由文本收费描述、无法唯一关联的发票或人员等均保留原值并进入异常清单；不能通过将它们改成 0 或强制缩放比例来让验收通过。

## 迁移到新 SQLite 文件（第一阶段：数据库记录）

```bash
.venv/bin/python scripts/migrate_mysql.py \
  --host 192.168.3.2 --port 3366 --user delitest --database jw_db \
  --output data/jw-db-records-20261113.sqlite3
```

已人工确认的状态或用印关联规则可通过 `--rules artifacts/reviewed-rules.json` 额外传入；未提供规则时保留未确认原值和待核对问题，不阻止完整原始记录迁移。

过程：

1. 校验源结构、全部表为 InnoDB，启动跨表一致性只读快照。
2. 分批读取每张表，写入新数据库的原始层；有主键按完整主键排序，无主键不去重。
3. 对每张表核对精确行数、逐列内容 SHA-256，并对 DECIMAL 列核对数据库 SUM。
4. 投影业务对象，将无法确认的字段、状态、孤立关联和待迁移附件列入异常清单。附件实体不在本阶段复制，但文档原始路径、偏移、长度、版本号和完整 `legacy_data` 会保留。
5. 写入迁移报告，检查点完成后将 `.partial` 文件发布为指定的新数据库；不会覆盖在用数据库。

失败时保留 `.partial` 供诊断，不作为线上库使用。源快照关闭后不能按旧游标继续读线上库，否则会混合两个时间点的数据；应使用新输出路径重新建立完整快照，或从冻结的源备份重新迁移。大表迁移时间和磁盘用量应在正式演练时测定。

## 文件实体迁移（第二阶段：后续执行）

数据库记录迁移可先独立验收；文档会显示为待补齐附件。历史业务状态和关联异常仍需核对后才能按当前业务流程使用。`attachment_pending` 只表示文件实体尚未导入，不表示数据库记录丢失。先用记录层验收：

```bash
.venv/bin/python scripts/verify_migration.py \
  data/jw-db-records-20261113.sqlite3 --data-dir data --records-only
```

该模式仍会核对 60 张源表的行数、逐行 SHA-256、DECIMAL 汇总、NULL/空字符串和重复行，并逐条比对文档原始存储定位字段与完整源记录；它会报告待迁移附件数量，但不会要求 `uploads/` 中已有文件。`record_migration_accepted` 和此模式的 `accepted` 只表示数据库记录及文档位置保留通过。历史状态、金额映射和孤立关联等仍列在 `business_review_issues`；`business_projection_accepted` 单独表示这些业务映射是否完成核对，记录验收不会自动解除任何异常。

如原始层已全部完成、业务投影被中断，可执行 `scripts/resume_projection.py <原文件.sqlite3.partial> --output <新文件.sqlite3>`。工具要求业务投影已完整回滚，重新校验所有本地源表和原提取报告后，仅从本地快照重建业务层，不连接 MySQL、不混入新的源数据；原始层不完整时拒绝继续。

源库检查显示文档主要使用原服务器/共享盘路径，部分有偏移和长度字段；数据库记录本身不包含可直接使用的全部文件内容。需要原文件存储目录、共享盘挂载，或旧系统可用下载接口。加密或私有容器格式需要旧系统导出，不能假设任意字节范围就是可读文件。

先生成映射清单：

```bash
.venv/bin/python scripts/import_attachments.py \
  --database data/jw-db-records-20261113.sqlite3 --data-dir data \
  --make-manifest --manifest artifacts/attachments.json
```

每项包含目标文档 ID、原路径参考、原偏移和长度参考。人工或受验证的转换程序填写相对 `path`、零起点 `offset`、`length`、文件名和可选的已知 `sha256`。原偏移的 0/1 基准尚未确认，因此不会自动套用。历史版本需为同一 `record_id` 添加对应清单项，`source_version` 使用原始 `dv_version` 的字符串值；当前版使用 `source_version: "current"` 和 `is_current: true`。

```bash
.venv/bin/python scripts/import_attachments.py \
  --database data/jw-db-records-20261113.sqlite3 --data-dir data \
  --source-root /mounted/original-documents \
  --manifest artifacts/attachments.json
```

文件源目录只读；路径不可越出该目录。导入时核对源文件范围和目标 SHA-256，保存结果报告。清单的当前版本和历史版本完整后才解除该文档的 `attachment_pending`。仍须抽查实际文件格式与可打开性；校验和相同仅说明复制过程一致，不能替代旧容器解包正确性的验证。

## 独立验收与切换

```bash
.venv/bin/python scripts/verify_migration.py data/jw-db-records-20261113.sqlite3 --data-dir data
```

验证器检查 60 张源表全部存在、提取批次已完成，重新扫描行数及校验和，再核对当前/历史文件版本集合、实际文件大小和 SHA-256，并输出未解决问题分类。存在任何异常时返回退出码 2，`accepted` 为 false；不会把有缺失源表、附件或孤立记录的迁移标成通过。

验收还须包含：不同分所同号案件、多律师分配、发票多次到账、特殊币种、作废/红冲记录、利冲明细、实际文件及历史版本、历史审批语义、角色访问范围，以及全量数据性能。业务规则确认、异常修复和文件存储接入完成后，应针对冻结源快照重新演练并核对报告。

切换前备份现有 SQLite 和附件。停止服务后，以 `LAW_DB_PATH=data/jw-db-records-20261113.sqlite3` 和正确的 `LAW_DATA_DIR` 启动。新库不会导入旧密码和会话；在服务器本机创建管理员，再为业务成员建立新账号并关联律师档案。旧库和原文件存储保持保留，以便回退。
