# Smart Factory · 智慧工厂知识助手

基于 FastAPI、Streamlit、Chroma 和通义千问的工厂文档问答系统。

腾讯云 Docker 部署、数据迁移及 Git 更新步骤见 [部署说明](deploy/README.md)。云端使用独立的 `compose.deploy.yaml` 和 `.env.deploy`，不影响现有本地配置。

## 本次 RAG 升级

- 默认按标题层级和段落组织，章节内超长内容递归切分（默认 800 字、120 字重叠），保留章节路径、文件名、PDF 页码和正文位置。
- 中文双字词与设备/故障代码 BM25 + 向量双路召回，默认每路最多 20 个候选，以 RRF 融合排序。
- 故障代码精确约束、相似内容去重、每份文档最多 2 个片段、上下文长度限制。
- 检索前按租户、工厂、产线及角色过滤；返回候选后再次校验元数据。空 line_id 表示工厂内共享。
- 回答携带 [S1] 引用、原文、页码、版本、查询日期和诊断信息；没有候选时不调用回答生成模型（仍可能调用问题规划模型）。
- 版本先过滤再排序；多轮指代改写、最多三步依赖检索、独立回答支持性核验。citation_valid 表示通过引用、步骤覆盖和模型核验，仍不等于已证明正确。
- 按内容、元数据和切分结果生成稳定 ID；同范围重复上传不重复嵌入，支持失败后重试。
- BM25 索引按访问范围缓存，上传成功后失效；最多缓存 32 个范围，默认 60 秒更新。
- MySQL 保存版本目录、会话、摘要和显式长期记忆；Redis 缓存近期上下文，Redis 不可用时降级到 MySQL。服务启动仍采用懒初始化。

检索链路：会话与相关记忆 → 指代改写及步骤规划 → 按适用日期选择有效版本 → 每步 BM25/向量召回 → 依赖证据解析 → 汇总引用回答 → 支持性核验 → 保存会话。
向量过滤与分页读取的接口依据 [Chroma 官方说明](https://github.com/chroma-core/docs/blob/main/docs/usage-guide.md)。

## 本地启动（Windows PowerShell / Python 3.11–3.12）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 在 .env 中填写 DASHSCOPE_API_KEY
# 同时配置 MYSQL_*；已有数据库可运行下面的增量初始化
.\.venv\Scripts\python.exe scripts/init_rag_state.py
.\.venv\Scripts\python.exe -m uvicorn mains:app --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app_frontend.py
```

前端选择专家角色，进入“系统设置 → 知识库管理”上传文档，然后在知识助手提问。
API 文档位于 http://127.0.0.1:8000/docs 。PDF、TXT、MD、DOCX 默认最多 20 MB；扫描 PDF 需要先 OCR。
TXT/MD 支持 UTF-8 和 GB18030。普通文本没有页码，page=0；PDF 页码从 1 开始。

## MySQL 怎么配置

MySQL 存业务表、权威版本目录与会话记忆；Chroma 存文档片段和向量。新版问答依赖 MySQL，Redis 是可选缓存。
原有 6 张表在 `database/01_schema.sql`，新增 5 张 RAG 表在 `database/03_rag_state.sql`，演示数据在 `database/02_demo.sql`。新增表不修改或清空业务表。演示设备参数不能作为生产操作依据。

### 方案一：Docker（新建本地开发库）

先安装并启动 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)。首次运行时复制 `.env.example` 为 `.env`，已有 `.env` 就直接补齐下面配置：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=factory_kb
MYSQL_USER=factory_app
MYSQL_PASSWORD=这里填写你设置的应用账户密码
MYSQL_ROOT_PASSWORD=这里填写另一个管理员密码
```

在项目根目录运行：

```powershell
docker compose up -d mysql
docker compose ps
docker compose logs --tail 50 mysql
```

Compose 读取 `.env`，首次创建数据卷时自动建库、创建应用账户、建表并导入演示数据。
初始化 SQL 只在空数据目录运行；重启不会再次导入，修改 SQL 也不会自动迁移已有库。这是 [MySQL Docker 初始化机制](https://dev.mysql.com/doc/refman/8.4/en/docker-mysql-more-topics.html)。
数据保存在命名卷 mysql_data；端口仅绑定本机。默认 3306 已被占用时，把 MYSQL_PORT 改为 3307 后重启后端。
Docker 创建的应用账户拥有该开发数据库的权限；正式部署应改用按接口需求配置的最小权限账户。

### 方案二：已安装 MySQL / Windows 原生安装

未安装时，从 [MySQL Community Server 官方下载页](https://dev.mysql.com/downloads/mysql/) 选择 Windows 的 MySQL 8.4 MSI 安装包，完成配置并启动 MySQL 服务，记下管理员密码和端口。
`.env` 中填写上述 MYSQL_HOST、MYSQL_PORT、MYSQL_DATABASE、MYSQL_USER、MYSQL_PASSWORD。MYSQL_ROOT_PASSWORD 只供 Docker 启动使用，原生初始化会交互式询问管理员密码。

```powershell
.\.venv\Scripts\python.exe scripts/init_mysql.py --admin-user root --seed
```

脚本创建数据库和缺失的表，导入演示记录，创建 factory_app 应用账户；业务表授予 SELECT，新增 rag_* 表额外授予 INSERT/UPDATE/DELETE。不需要演示数据时去掉 `--seed`。
已有同名账户不会重置密码、已有记录不会覆盖；已有不同表结构需要另行迁移，CREATE TABLE IF NOT EXISTS 不会自动补列。
如果已有 factory_app 账户，请在 `.env` 中填写它实际的密码。

### 检查连接

重启后端以重新加载环境变量，然后运行：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/db-status
```

返回 status=connected 即已连通。可用 tenant_A / plant_A1 / line_a1 在设备参数页查询“激光焊接机”，在故障库查询 F0012。
连接被拒绝时检查服务和端口；Access denied 时检查账户、密码及允许连接的主机；Unknown database/table 时先执行初始化。
工艺文档目录初始为空；RAG 上传同步 rag_document_versions 和 Chroma，不同步旧 process_documents 目录，不保存原文件供下载。

## 版本、生效日期与历史比较

上传时填写文档系列、版本号、生效日期，可选失效日期及是否发布。**同一标准的不同文件必须填写相同系列名称**；系列按租户/工厂/产线隔离，不能根据相似文件名自动判定同一标准。空系列回退到标题；空标题回退到文件名。

- 生效区间为 `[effective_from, effective_to)`，失效日期当天起不再适用。
- 默认每系列选择查询日期之前生效最晚的已发布版本，再检查访问权限和失效日期。未来版本、草稿不会提前替换当前版本。
- 最新版本撤回、过期或权限不足时不回退到旧版。撤回会阻止该版本生效日起的查询；如果需要完整追溯撤回前的发布状态，后续需增加双时间审计日志。
- 同系列版本号不允许对应不同内容；切分方式、标题、范围或内容变化请用新版本号。相同生效日期的两个版本不能同时发布。
- 先登记草稿，再完整写入向量，最后发布。中途失败保持草稿，可用相同参数重试；发布接口核对全部片段是否存在。
- 上传或状态变更会使本进程检索缓存失效；每次查询仍读取权威目录，回答期间版本改变会拒绝给出确定结论。
- “去年”按去年12月31日，“今年”按今天；支持明确的 YYYY-MM-DD、YYYY年M月D日、去年M月D日，也可在知识助手选择日期或对比两个日期。每次最多两个日期，不支持任意自然语言日期区间。
- **升级前没有版本登记的向量片段默认不参与问答**。请重新上传并明确登记系列、版本、生效日期；不会猜测旧文档属于哪一年。

## Redis、多轮、摘要和长期记忆

已有本地 MySQL 时只启动 Redis：

```powershell
docker compose up -d redis
```

`.env`：

```dotenv
REDIS_URL=redis://127.0.0.1:6379/0
MEMORY_TTL_SECONDS=86400
MEMORY_SUMMARY_CHARS=6000
RAG_MAX_HOPS=3
```

未安装 Redis/Docker 也可以使用：Redis 失败会明确报告 `unavailable_mysql_fallback`，会话仍写 MySQL；服务重启后可恢复。只使用 MySQL 时将 REDIS_URL 留空。Redis 恢复后自动重新使用；缓存附带会话修订号，不能复活旧会话。Redis TTL 只控制缓存，MySQL 会话不会因此自动删除。

- 前端每个会话使用 UUID，接口根据用户、租户、工厂、产线及角色校验归属。同会话并发提问返回409，防止消息顺序错乱。当前登录为演示逻辑，身份仍由客户端传入，尚不具备生产身份认证保证。
- 每轮使用最近4轮及摘要改写独立问题；指代不明确时追问。历史回答不作为技术依据，每轮重新检索适用版本。
- 未摘要消息超过字符阈值且多于8条时，按批次压缩较早消息，保留最近8条原文。结构化摘要保留实体、已确认事项、待解决问题及来源ID；失败保留原始记录，不写入失败摘要。原始消息仍保存在 MySQL。
- 在“会话与长期记忆”明确保存偏好、背景或审核经验。最多50条；注入最多3条偏好及5条关键词相关背景/经验。关联来源失效或过期的记忆不注入；模型回答不会自动变成长期知识。记忆不替代正式文档。
- 会话 ID 可复制并恢复；清除聊天会清除该会话消息、摘要和有效缓存。长期记忆独立管理，可按ID删除。

## 多跳与证据判断

规划器最多生成3个检索步骤；后续步骤根据前置原文补齐实体、型号和工况后检索。支持文档工具、限定租户/工厂/产线的只读设备参数工具，以及按租户共享的只读 SQL 故障工具，不允许模型自由生成 SQL。设备和故障库是当前快照，历史日期不能拿当前记录充当历史事实。比较与计算在回答阶段完成，不额外伪造检索步骤。

故障问题优先查询 MySQL `fault_knowledge`，支持代码精确匹配、设备类型和现象 / 处理步骤关键词筛选。明确代码的简单查询及“那严重程度是什么”等短追问使用本地路由，不依赖模型规划；设备限定、现象检索和混合任务由规划器处理。同一代码对应多个设备类型时先追问，不混用处理方案；超过10条匹配时要求缩小条件。SQL 无匹配时补查有效文档，引用和检索轨迹会区分 MySQL 与文档来源；两者都无证据时不生成处理结论。故障表单新增、修改后，问答下一次查询直接读取最新数据，无需重新上传文档或生成向量。也支持先查 SQL 故障，再按设备与代码联查文档，以及由前置文档提取代码后查询 SQL。回归命令：`.\.venv\Scripts\python.exe -m unittest test_fault_tool test_business test_api test_rag test_chunking test_workflow`。

接口返回 `retrieval_steps`，包含日期、问题、依赖、工具和缺失原因。任何必要步骤缺证据就拒绝完整结论；证据超上下文预算时要求缩小范围。

`confidence` 显示“证据充分/证据有限/无法判断”及原因，不显示伪正确率。规则核对版本、权限、引用和步骤覆盖，另一次模型调用检查数值、单位、条件、冲突及问题覆盖。模型核验仍可能误判，未做概率校准，关键结论应由人员核对原文。检索、改写、摘要和核验会增加模型调用与响应耗时。

## 补充工业演示数据

已提供 `demo_content.py` 和 `scripts/enrich_demo.py`，覆盖现有4条产线、24类设备，包含168条补充设备参数、48个虚构故障案例及16份带版本的演示资料。全部明确标注为演示，不是实际设备参数、报警码或正式标准。

```powershell
# 查看业务表数量和产线归属，不写入
.\.venv\Scripts\python.exe scripts/enrich_demo.py --inspect
# 仅向 MySQL 补充设备参数和故障案例
.\.venv\Scripts\python.exe scripts/enrich_demo.py
# 后端启动后，同时注册文档版本、建立向量索引并补充工艺文档目录
.\.venv\Scripts\python.exe scripts/enrich_demo.py --with-knowledge
```

脚本从本地 `.env` 读取连接配置，按业务唯一键跳过已有记录，不覆盖原数据；设备参数与故障案例同一事务提交。资料按“点检规则2025版/2026版、设备与工序说明、故障案例”每线4份组织，版本内容固定，重复运行不重复嵌入。文档逐份提交，途中失败可重试。

演示范围：tenant_A/plant_A1/line_a1（模组装配）、tenant_A/plant_A2/line_a2（电芯封装）、tenant_B/plant_B1/line_b1（机械加工）、tenant_C/plant_C1/line_c1（SMT）。登录时需选择对应租户、工厂及产线。
可查询“RB1001 是什么故障？”、“PLC控制柜有哪些演示参数？”、“模组装配线今年和去年的演示点检规则有什么变化？”。旧记录 F0012/F0013 保留。

## 按标题、段落和递归切分

原实现已经使用 RecursiveCharacterTextSplitter，现增加结构优先模式：

```text
标题层级（章节边界）
  → 章节内优先保留段落，短段可合并
  → 超长段落按换行、句号、分号等递归拆分
  → 仍超长才按字符拆分
```

- `RAG_SPLIT_STRATEGY=structured`：默认模式，不把不同章节的正文合并。每个片段附带章节路径，例如“设备手册 > 焊接 > 故障排查”。长章节拆成多块时，每块都带章节路径。
- `RAG_SPLIT_STRATEGY=recursive`：纯递归模式，按段落、换行、中文句子到字符逐层拆分。
- Markdown 识别 `#` 到 `######` 标题，并忽略代码围栏中的伪标题。DOCX 读取 Heading 1–6 / 标题 1–6 样式，同时保留表格文字。
- TXT/PDF 识别“第一章 …”“第二节 …”“一、…”及“1.1 …”等文本编号。PDF 保留页码，连续页面正文继承上页章节路径；不依赖字号、粗体或 PDF 目录推断标题，因此复杂版式仍需预览确认。
- `RAG_CHUNK_SIZE=800` 是包含重复章节路径的片段字符上限，`RAG_CHUNK_OVERLAP=120` 是章节内正文重叠目标；不会跨章节做重叠。
- start_index 指向抽取文本中该片段正文的起点，不包含额外重复的章节路径。

进入“系统设置 → 知识库管理”，选择切分方式，先点“预览切分结果”确认，再上传；预览只解析文件，不调用嵌入模型、不写数据库。
上传及预览接口可传 multipart 字段 split_strategy 覆盖默认配置。
切分策略变化会生成新的片段 ID，需登记新版本号；旧片段保留，版本目录决定是否参与检索。修改集合后必须重新入库对应版本，不能只保留旧目录。
实现基于 [LangChain 递归切分器](https://reference.langchain.com/python/langchain-text-splitters/langchain_text_splitters) 和 [python-docx 文档块读取接口](https://python-docx.readthedocs.io/en/latest/api/document.html)。

## API

`POST /query` 与 `POST /retrieve` 使用相同请求格式；后者仅检索，不调用回答模型：

```json
{
  "question": "F0012故障如何排查？",
  "top_k": 5,
  "user_ctx": {
    "tenant_id": "tenant_A",
    "plant_id": "plant_A1",
    "line_id": "line_a1",
    "user_id": "engineer_li",
    "role": "操作员"
  }
}
```

`/query` 可额外传 session_id（UUID）、as_of（YYYY-MM-DD）或 comparison_dates（两个不同日期），后两者互斥。返回 session_id、rewritten_question、query_dates、answer、sources、citation_valid、confidence、retrieval_steps、diagnostics 和 source_data。`top_k` 是每步每日期的片段数，总上下文仍受 RAG_CONTEXT_CHARS 限制。
`/retrieve` 按相同日期及版本规则直接检索，不做问题规划或会话写入。
`/upload-document` 使用 multipart：file、user_ctx（JSON字符串）、可选title、category、access_level、family_name、version、effective_from、可选effective_to、publish。未传版本默认为1，未传生效日期默认为今天；返回 document_id、版本状态、chunks、added_chunks、duplicate。

管理接口（POST）：`/documents/list`；`/documents/{id}/status`（status=published/withdrawn）；`/sessions/{id}/history`；`/sessions/{id}/clear`；`/memories/list`；`/memories`；`/memories/{id}/delete`。每个请求均传 user_ctx；保存记忆另传 content、kind（preference/background/reviewed_experience）、可选source_id、valid_until。`GET /memory-status` 查看 MySQL/Redis 状态。

## 调参与迁移

配置见 `.env.example`。RAG_MAX_DISTANCE 是 Chroma 原始距离的上限（默认 1.2，越小越严格），不是概率；应根据真实评测集与集合的距离度量调整。
BM25 命中可以独立进入候选，因此距离阈值只约束向量通道。RRF 为排序分数，也不是置信度。
修改 EMBEDDING_MODEL 必须使用新集合并重新导入，避免混合不同维度或语义空间。
旧演示数据缺少 line_id 时不会被检索；缺少范围或权限字段的旧文档请通过上传接口重新导入。
稳定 ID 支持相同参数重复上传；修改内容、切分或权限需要新版本号。发布/撤回入口位于知识库管理页。
进程内缓存适用于中小规模知识库，多进程写入后其他进程最长等待缓存 TTL；更大规模应迁移到独立关键词索引。
多轮已接入服务端持久化与指代改写，尚未接入专门的外部 reranker 或语义长期记忆索引。

## 验证与评测

离线回归测试不调用模型、不依赖 MySQL：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

原 `tests.py` 保留为显式运行的 DashScope 联通检查，导入或测试发现时不会访问外部模型。

在 tenant_A / plant_A1 / line_a1 范围，以操作员可见权限上传 `examples/故障手册.txt` 后执行：

```powershell
.\.venv\Scripts\python.exe evaluate_rag.py examples/evaluation.jsonl --output evaluation-report.json
```

评测输出文件级 Recall@K、MRR、无答案问题的空召回正确率，以及逐题结果与耗时。
示例集只用于检查链路；请用真实业务问题和正确来源文件名建立评测集，比较调整前后指标。向量检索仍会调用付费嵌入接口。

## 问答意图与会话回顾

问答入口会区分技术检索、日常对话和会话回顾。纯问候、感谢及常见“我刚刚问的什么”直接读取当前会话或本地回复，不调用嵌入、向量库或回答模型；其他闲聊通过意图规划直接回复。只有技术问题和明确技术追问才检索。模型规划不能将问候、会话回顾、换话题改写为旧故障。前端会标明“未检索知识库”，不显示技术证据置信度。会话回顾引用当前可用原始记录，不将摘要中的推断当成原话。

意图规划失败时先追问，不自动回退到知识库检索，避免无关消息再次被强行关联到旧技术问题。

## 不懂 SQL 的人员如何维护业务数据

系统设置按任务分为「文档上传」「版本管理」「运行状态」「账户与权限」，只加载当前入口。上传页默认只展开基本信息，版本日期、发布选项和切分方式在折叠区；版本管理提供中文目录、筛选和文档选择，不需要手填 ID。运行状态提供手动刷新与最近检查时间。账户页明确展示演示登录的权限说明，原来未落库的“添加用户”表单已移除。

登录后进入「设备参数」或「故障库」，页面会直接列出已有数据。上方可以按关键词、设备筛选；故障还可以按严重程度筛选，每页显示 50 条，并显示完整记录总数。

工程师、专家可在「新增记录」中填写中文表单，点击「保存新增」直接写入 MySQL。设备参数填写设备名称、参数名称、带单位的数值，以及可选的允许偏差；故障填写设备类型、故障代码、故障现象、处理步骤和严重程度。设备参数自动归属当前产线，故障在当前租户内共享，不需要手填数据库 ID 或执行 SQL。

修改时先筛选定位，在「修改记录」选择条目，修改后点击「保存修改」。重复参数 / 代码会提示使用修改入口；遇到他人已修改的冲突，请点击「刷新数据」并重新编辑。操作员仅有查看入口，后端也限制操作员写入。

当前表单维护的是业务关系表，保存不会自动生成文档向量。需要进入文档 RAG 的资料，请整理成文档后在知识库管理上传。演示记录不是实际生产参数或维修依据。

原生 MySQL 安装若使用 `factory_app` 等独立账户，管理员需赋予 `device_parameters`、`fault_knowledge` 的 SELECT、INSERT、UPDATE 权限；更新后的 `scripts/init_mysql.py` 会配置这些业务表权限，无需重置现有数据。

业务维护接口：`POST /business/equipment/list`、`POST /business/equipment/save`、`POST /business/faults/list`、`POST /business/faults/save`。新增省略 `id`；修改必须携带列表返回的 `id`、`revision`，接口在事务中检查访问范围、重复和并发修改。离线回归：`.\.venv\Scripts\python.exe -m unittest test_business test_api test_rag test_chunking test_workflow`。

## 部署前尚需完成的事项

当前登录页面为演示逻辑，user_ctx 由客户端提交，尚未接入身份认证。记忆归属校验不能防止客户端冒充其他用户名/角色，正式使用前必须改为服务端可信身份。检索范围校验不能替代认证，本版本适用于本地开发。
旧 `/execute-sql` 已停用（410），避免任意 SQL 绕过新增会话与记忆隔离；故障页改用绑定参数的 `/query-faults`，工艺文档页展示真实有效版本目录。旧客户端需要改用专用查询接口。
仓库旧版本曾包含疑似密钥字符串，当前已移除并改为环境变量读取；如果该密钥曾有效，请在服务商控制台轮换。

## 文件

- mains.py：API、懒初始化、知识上传。
- rag.py：BM25、过滤、融合、去重和引用生成。
- state_store.py / workflow.py：权威版本选择、MySQL/Redis 会话、摘要、显式记忆、多跳与证据核验。
- ingestion.py / chunking.py：加载、章节组织、递归切分、元数据和稳定 ID。
- compose.yaml / database / scripts/init_mysql.py：MySQL 启动、建表、演示数据和原生初始化。
- init_chramdb.py / models.py：模型与向量库配置。
- app_frontend.py / business_ui.py / settings_ui.py：问答、业务中文表单和按任务分区的设置页面。
- test_rag.py / test_api.py / test_workflow.py：离线回归测试。
- evaluate_rag.py / examples：可复用检索评测入口和演示资料。
