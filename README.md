# 易律网 · 律所智能办公平台

Vue 3 + TypeScript 前端，Python FastAPI 后端，SQLite 数据库。以案件为中心，采用 Overview 总览 + 明细 Tab，包含律师工作台和角色化管理后台。

## 启动

当前工作区已安装依赖并完成前端构建。运行：

```bash
./scripts/start.sh
```

访问 **http://127.0.0.1:8000**。首次打开在本机创建管理员账号（密码至少 12 位），可选择载入 8 件明确标注的虚构演示案件。没有内置通用密码。API 文档：`http://127.0.0.1:8000/docs`。

全新环境安装（Python 3.11+、Node.js 22.12+）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci
npm run build
./scripts/start.sh
```

前端开发：后端保持启动，另一个终端执行 `npm run dev`，访问 Vite 输出的地址。

## Docker 部署

项目提供了多阶段镜像和 Compose 配置。镜像只包含应用和前端构建产物，当前迁移数据库通过绑定卷使用，不会把约 7GB 的 SQLite 文件复制进镜像：

```bash
# 在包含当前 data/jw-db-records-20261113.sqlite3 的项目目录执行
docker compose up -d --build
docker compose ps
docker compose logs -f law-office
```

访问 `http://localhost:8000`。`docker-compose.yml` 默认将 `./data` 挂载到容器 `/app/data`，并将 `LAW_DB_PATH` 指向 `/app/data/jw-db-records-20261113.sqlite3`，因此启动后直接使用当前迁移库；同目录的上传文件和附件位置也会保留。停止服务使用 `docker compose down`，不会删除数据库卷中的文件。部署前请先备份 `data/`，并确保 Docker 进程对该目录有读写权限。

如果数据库位于其他目录，修改 Compose 的卷映射和 `LAW_DB_PATH`，两者必须指向同一个容器内文件。例如 `- /srv/law-data:/app/data`。生产环境建议在反向代理终止 HTTPS，并设置 `LAW_ALLOWED_ORIGINS` 为实际前端来源。反向代理还需要转发原始请求的 `Host`、`X-Forwarded-Host` 和 `X-Forwarded-Proto`；应用会据此识别容器部署时的同源请求。若代理不转发这些请求头，必须显式设置来源，例如：

```yaml
environment:
  LAW_HTTPS: "1"
  LAW_ALLOWED_ORIGINS: https://law.example.com
```

### 打包后上传到 Ubuntu

启动 Docker 后，在当前项目目录执行 `./scripts/package_docker.sh`。脚本会构建镜像、导出镜像、压缩当前 `data/`（包含迁移数据库和附件目录），并生成校验文件。

输出目录为 `artifacts/docker-package/`，包含镜像压缩包、数据库压缩包、Compose 文件和 `SHA256SUMS`。将整个目录上传到 Ubuntu：

```bash
scp -r artifacts/docker-package user@ubuntu:/srv/lawyer-ai/
```

在 Ubuntu 上加载并启动：

```bash
cd /srv/lawyer-ai/docker-package
sha256sum -c SHA256SUMS
gunzip -c lawyer-ai-office-image.tar.gz | docker load
mkdir -p data
tar -xzf lawyer-ai-data.tar.gz -C data
docker compose up -d --no-build
docker compose ps
```

首次打包前请停止本机服务，避免 SQLite 正在写入。第一次启动迁移库会建立查询索引，可能需要几分钟；镜像健康检查已预留 10 分钟启动窗口。镜像和数据库压缩包都可能较大，传输前确认 Ubuntu 剩余磁盘空间充足。

## 业务功能

| 模块 | 已实现行为 |
| --- | --- |
| 工作总览 | 案件数量、待审批事项、近期日程、业务分布、到账与开票汇总、操作动态 |
| 案件 Overview | 案件资料、当事人、主办及协办律师、三条流程进度、财务汇总、日程 |
| 立案流程 | 利冲创建 → 合伙人利冲审批 → 填写立案申请单 → 提交申请 → 合伙人立案审批；退回及重新提交 |
| 利益冲突 | 委托人、对方、当事人、别名和证件标识匹配；保存结果快照；无冲突通过、注明依据的豁免通过、禁止承接；主体变更后审查失效 |
| 案件详情 | 编号、分所、部门、类型、案由、法院、标的、服务费、收费方式、委托事项、期限、保密标记、结案及归档信息 |
| 合同流程 | 上传合同 → 发起盖章 → 审批 → 上传已盖章文件并确认盖章 → 登记纸质位置及归档编号 → 合同归档；同步用印明细 |
| 财务流程 | 开票申请 → 财务审批 → 登记票号及确认开票 → 关联到账 → 财务确认；作废、到账冲销、流水重复检查 |
| 多律师分配 | 案件、发票、到账三种分配对象；合计 100%；金额按分精确分摊；开票及到账确认后保存不可变快照 |
| 财务信息 | 服务费、标的额、收款计划、发票抬头、税号、含税额和税额、银行信息、到账时间、付款方；不同币种分别汇总 |
| 文档 | 实体文件上传、分类、元信息编辑、历史版本、下载权限、SHA-256 校验；审批中的文件禁止替换 |
| 用印 | 印章类型、份数、送达单位、事由、附件、审批、盖章归档文件、领取人及领取记录 |
| 任务 | 负责人、优先级、开庭、答辩/举证/上诉/执行期限、完成及重新打开 |
| 后台 | 律所配置、账号与角色、启停账号、重置密码、操作审计、源表迁移范围、迁移批次报告、完整源记录浏览 |
| 部门与成员 | 部门新增、名称及启停管理；成员分页搜索、部门筛选、资料维护和调动；为成员创建账号、调整角色和启停权限、签发一次性密码重置码 |
| 元数据 | 案由、案件状态及其他历史编码字典的分类搜索、新增、改名、排序、父编码和启停管理；历史编码保持稳定，现有审批流程不随字典编辑改变 |
| 通用 | 搜索、状态筛选、分页、CSV 导出、版本冲突检查、历史快照与审计 |

原始历史字段与业务投影同时保留。源案件的附属明细可在“源数据 / 历史”中查询；源利冲明细、文档版本也有独立的历史明细入口。

管理员可通过“管理后台 → 部门与成员 / 元数据管理”维护上述资料。首次启动从已迁移的源表补入管理表，后续启动只补充缺失项，不覆盖管理员修改；`src__*` 表保持原样。停用字典项仍可用于历史显示，案由和部门输入提供启用项建议。员工在职状态与账号登录权限分别维护，离职时请同时停用相关账号。无初始密码的新账号需要管理员核验身份后签发重置码。

案由和接口案由按 `parent_code` 展示可展开的树，搜索保留祖先路径，按根节点分页；父级缺失或循环的历史记录仍列出供核对。后台“部门与成员”和“律师团队”统一采用左侧“分支机构 → 部门”树、右侧人员列表，点击分所显示其全部成员，点击部门显示该部门成员；支持姓名、员工编号搜索、在职状态筛选和人员分页。后台可直接新增部门、成员，选择部门后可编辑部门，人员行保留账号权限与密码重置入口。

迁移库的 `emp_office_id` / `dp_office_id` 是所属机构编号，实际分所通过 `t_suboffice_info` 的 `(soi_office_id, soi_area)` 关联：部门使用 `dp_area`，员工优先使用 `emp_area`，缺失时使用部门地区。管理员明确调动部门后以新部门归属为准。分所中文名称单独展示，原始机构编号、地区编码及历史记录保留；同名人员不合并，同名不同编码部门分别展示。

员工源状态 `X` 表示离职，即使未填写离职日期也按离职展示。旧版迁移中仍为初始版本的记录会在读取时补正，管理员已编辑的记录保留现有状态。

案件管理的“分支机构”筛选按案号开头匹配：`CS` 为长沙，`HZ` 为杭州。可与关键词及状态组合使用，分页数量和 CSV 导出使用相同条件，并保持案件权限控制。

案件管理搜索使用 `case_search` 检索表。在当前权限、分所和状态范围内，完整案号优先按索引精确匹配（忽略英文大小写及输入首尾空格），同案号的不同机构记录均保留；没有精确匹配时，按案号片段、案件名称、客户或对方当事人做包含匹配。搜索不再扫描完整历史 JSON。统计和分页完成后，只读取当前页的完整案件记录；CSV 导出使用同样的匹配规则。检索表随案件新增、修改和删除自动更新，升级后首次启动会自动补建案号、排序及律师分配权限索引，大库首次建索引需要等待启动完成。

## MCP 案件查询

服务地址为 `http://localhost:8000/mcp`（`/mcp/` 也可用），采用标准 MCP Streamable HTTP。管理员或其他已登录用户可在后台接口创建只读令牌：

```http
POST /api/mcp/tokens
Content-Type: application/json

{"name":"案件查询客户端","expires_in_days":30}
```

返回的令牌只显示一次，调用 MCP 时放在 `Authorization: Bearer <token>`。令牌继承创建者的案件可见范围，可在 `GET /api/mcp/tokens` 查看或 `DELETE /api/mcp/tokens/{id}` 撤销。MCP 工具名为 `query_cases`，参数为 `filters` 对象，至少填写一个案件名称、客户名称、案件类型、主办律师、分支机构名称、收案日期起止、对方当事人或案件状态条件；多个条件为 AND。日期区间含首尾，支持只填起或止日期，使用 `page` / `page_size` 分页。

客户端的传输方式请选择 **Streamable HTTP**（部分客户端称为 HTTP，配置字段以客户端要求为准），URL 填 `https://你的域名/mcp`，请求头填 `Authorization: Bearer <完整 API Key>`。不要选择旧版 SSE，也不要使用 `/sse` 或令牌管理地址 `/api/mcp/tokens`。此服务采用无状态 Streamable HTTP，POST 返回 `application/json` 是正常行为。

Docker 部署通过前端 Nginx 的 80 端口访问 `/mcp`。部署环境需设置 `LAW_ALLOWED_ORIGINS=https://你的域名`，MCP 会同时允许该来源及其 Host；额外域名或 IP 可通过 `LAW_MCP_ALLOWED_HOSTS` 配置，多个值用逗号分隔，非默认端口需保留（例如 `law.example.com:8443`）。修改后执行 `docker compose up -d --build`，使后端环境和 Nginx 配置同时生效。直接运行 Python 时请通过 `export` 设置环境变量。

遇到 `SSE error: Invalid content type, expected "text/event-stream"` 时，先检查客户端是否选错传输方式，或在 Streamable HTTP 失败后回退到了 SSE。查看最初的 `/mcp` 响应：`text/html` 表示代理落到了网页，401 表示 Bearer 密钥缺失或无效，421 表示 Host 未获允许，403 可能是 Origin 未获允许。Streamable HTTP 客户端必须支持 JSON 响应。

每个结果返回案件详情、主办律师、明确关联的客户源信息和案件当事人、财务汇总及开票 / 到账 / 收款计划 / 律师分配、盖章信息、日程与待办、合同信息。历史附件只返回原路径、偏移和长度等位置字段，不读取附件实体；未明确关联的历史记录不会按名称猜测挂接。

当前本机 `localhost:8000` 已使用 `data/jw-db-records-20261113.sqlite3`。再次启动同一迁移库请运行：

```bash
LAW_DB_PATH="$PWD/data/jw-db-records-20261113.sqlite3" LAW_DATA_DIR="$PWD/data" ./scripts/start.sh
```

## 角色

- **管理员**：账号、配置、迁移数据及所有业务。
- **合伙人**：业务统筹，利冲、立案、用印审批，审计查看。
- **律师**：本人主办或参与案件；创建利冲、填写立案、申请开票/用印、案件文档和任务。
- **财务**：可访问的非保密案件财务，开票审批、开票确认、到账核销及冲销。
- **行政**：可访问的非保密案件文档、用印执行、领取、合同归档。

权限由后端检查。密码使用加盐 PBKDF2，登录使用 HttpOnly / SameSite Cookie，账号权限调整会使已有会话失效。

## 参考数据库与迁移状态

已按用户确认，以 **`jw_db`** 为权威来源，只读核对指定 MySQL 测试环境。没有将数据库密码写入项目文件。

2026-11-12 的预检：60 张核心及关联表，全部 InnoDB，未发现结构偏移；一致性快照中共 **7,440,015** 条源记录。包括 155,689 件案件、26,406 条发票、25,451 条到账、210,164 条文档、13,519 条用印和 4,540,449 条冲突明细。估算行数和精确计数区分保存，源库后续变化会导致数量变化。

当前交付包含可运行平台、源结构清单、只读迁移程序、附件迁移程序与核对工具。迁移分两阶段：第一阶段迁移全部数据库记录，同时保留原始附件路径、偏移、长度、文件名和版本信息；第二阶段再按清单复制附件实体。记录层迁移可先用 `scripts/verify_migration.py --records-only` 验收，附件待办会保留在 `migration_issues`，不会被误报为记录缺失。原始附件存储位置、打包文件读取方式、部分财务/利冲状态编码，以及无案件外键的历史用印关联，需要结合旧系统确认。详细边界、命令、异常处理与切换步骤见 [迁移说明](docs/MIGRATION.md)。

2026-11-13 已完成上述 60 张表、7,440,015 条记录迁移，独立记录验收通过。新库为 `data/jw-db-records-20261113.sqlite3`（约 6.9 GiB），当前本机服务已切换至此库。210,164 条文档保留完整源记录及原始位置，附件实体未复制。初次验收报告在 `artifacts/jw-records-verification-20261113.json`，附件待迁移清单在 `artifacts/jw-attachments-pending-20261113.json`。初次验收记录了 639,216 条业务映射待核对事项（不是互不重复的记录数），以及一次约 144 秒的全量 Overview 试读；这些是早期检查结果，当前异常数以管理后台为准。本机切换便于继续核对，不代表所有旧业务语义及正式上线容量已通过验收。详见 [本次迁移结果](docs/MIGRATION_RUN_20261113.md)。

## 数据与运行配置

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `LAW_DATA_DIR` | 项目下 `data/` | 上传文件与默认数据库目录 |
| `LAW_DB_PATH` | `$LAW_DATA_DIR/office.sqlite3` | 指定数据库，可用于迁移后的切换 |
| `LAW_HTTPS` | 未设置 | HTTPS 部署时设为 `1`，启用 Secure Cookie |
| `LAW_ALLOWED_ORIGINS` | 本机同源和 Vite 开发地址 | 额外允许的请求来源，逗号分隔 |
| `LAW_PORT` | `8000` | 启动脚本的本地端口 |

SQLite 开启 WAL，业务变更使用事务。金额在 API 和 SQLite 中以十进制文本保存，前端格式化也不把精确金额转换为浮点数。数据库、上传文件、个人凭据、迁移导出和测试产物都在 `.gitignore` 范围内。

备份数据库与附件：

```bash
.venv/bin/python scripts/backup.py --output artifacts/office-backup.zip
```

恢复时停止服务，将备份中的 `office.sqlite3` 和 `uploads/` 解压到一个新目录，再使用 `LAW_DATA_DIR` 指向该目录启动。保留旧目录供回退。

## 验证

```bash
npm run build
.venv/bin/python -m pytest -q
npm run test:e2e
```

浏览器测试会启动独立临时数据库，不接触实际业务数据。macOS 默认使用已安装的 Chrome；其他环境先执行 `npx playwright install chromium`。测试覆盖立案、财务、合同用印归档、Overview/明细 Tab、后台部门成员与字典管理、密码重置和移动端。截图与测试报告位于 `artifacts/`。

源库结构和数量预检不能替代全量迁移演练、附件可读性验证和实际并发容量验收。新系统的审批流程已实现为明确状态机；旧系统自定义审批链的历史原始数据保留，尚未确认的旧状态不会自动冒充审批通过。
