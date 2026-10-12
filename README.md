# Smart Factory · 智慧工厂知识助手

基于 FastAPI、Streamlit、Chroma 和通义千问的工厂文档问答系统。

## 常用命令：更新云端项目与网页 SSH 隧道

### 更新云端代码并让新版生效（在云服务器执行）

本地代码提交、推送到 GitHub 后，在云服务器终端逐条执行；如果项目目录不同，请替换 `~/Smart-Factory`：

```bash
cd ~/Smart-Factory
git pull --ff-only
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml build backend
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait --force-recreate backend frontend nginx
```

- `git pull`：同步 GitHub 上的代码；只拉取代码不会让正在运行的容器自动更新。
- `build backend`：用最新代码构建应用镜像；前后端在当前部署配置中共用该镜像。
- `up ... backend frontend nginx`：重建应用容器，让新版生效。更新期间网页会短暂中断，MySQL 和 Redis 不主动重建，数据库数据保留。

某一步报错就先停止，不要继续执行后面的步骤。上述命令用于已有部署的应用代码更新；新增数据库表或字段时，还需执行对应迁移步骤。更新前备份步骤见[部署说明](deploy/README.md#4-每次更新前备份)。

### 网页 SSH 隧道（在本地 Windows PowerShell 执行）

将 `服务器公网IP` 替换为实际地址；SSH 用户不是 `ubuntu` 时也需替换。需要指定私钥时，在 `ssh` 后添加 `-i "C:\路径\私钥文件"`。

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:8080:127.0.0.1:8080 ubuntu@服务器公网IP
```

连接成功后，保持此窗口打开，在本地浏览器访问 [http://localhost:8080](http://localhost:8080)。这个隧道将电脑的 `8080` 转发到云服务器的 `127.0.0.1:8080`，仅用于网页访问；更新代码不需要开启它，查看 MySQL 使用下方链接中的数据库隧道。

终端连接后没有持续输出是正常的。命令每30秒发送 SSH 保活请求，连续3次未响应会退出，但不会自动重连；断网、电脑休眠或服务器重启后若已断开，重新执行即可。按 `Ctrl+C` 可关闭隧道，云端项目仍继续运行。

服务器必须已有服务监听 `127.0.0.1:8080`（仓库默认 Nginx 映射）。如果出现 `channel ... Connection refused`，请检查 Nginx 容器和实际端口映射；公网 HTTP 的 `80` 端口能访问，并不代表这个 `8080` 隧道目标也可用。若已通过独立的公网入口访问网页，则无需为该入口另开网页隧道。

腾讯云 Docker 部署、数据迁移及 Git 更新步骤见 [部署说明](deploy/README.md)。云端使用独立的 `compose.deploy.yaml` 和 `.env.deploy`，不影响现有本地配置。

使用 Navicat、DBeaver 查看或修改云端数据库，见[通过 SSH 隧道连接云端 MySQL](deploy/README.md#通过-ssh-隧道连接云端-mysql)，包含端口映射、Windows 保活命令和断线重连说明。

## 阅读导航

- [项目概览与运行关系](#项目概览与运行关系)
- [功能与权限](#功能与权限)
- [项目目录](#项目目录)
- [本地启动](#本地启动)
- [云端部署与更新](#云端部署与更新)
- [账号与身份隔离](#账号与身份隔离)
- [数据库与数据持久化](#数据库与数据持久化)
- [SSH 隧道与访问方式](#ssh-隧道与访问方式)
- [系统使用流程](#系统使用流程)
- [知识库与 RAG 规则](#知识库与-rag-规则)
- [配置项说明](#配置项说明)
- [API 使用](#api-使用)
- [测试与检索评测](#测试与检索评测)
- [演示数据](#演示数据)
- [备份与维护](#备份与维护)
- [常见问题](#常见问题)
- [当前边界与后续工作](#当前边界与后续工作)

## 项目概览与运行关系

Smart Factory 是面向工厂人员的知识问答与业务资料管理系统。它结合文档检索、MySQL 故障知识及设备参数，按账号所属租户、工厂、产线和角色提供查询与维护入口。

| 组件 | 作用 | 是否必需 |
| --- | --- | --- |
| Streamlit | 登录、知识助手、业务表单、文档和账号管理页面 | 使用网页时需要 |
| FastAPI | 身份认证、权限校验、业务查询、上传与问答接口 | 需要 |
| MySQL | 账号、登录凭证、业务表、文档版本目录、会话与长期记忆 | 需要 |
| Chroma | 保存文档片段及向量，支持文档检索 | 使用文档知识库时需要 |
| DashScope / 通义千问 | 默认回答模型及文本嵌入服务 | 技术问答、文档入库与检索时需要相应服务 |
| Redis | 会话上下文缓存；不可用时回退 MySQL | 应用逻辑可降级；云端 Compose 默认启动并等待其健康 |
| Nginx | 云端网页入口，转发到 Streamlit | 默认云端部署使用 |

默认检索与回答流程：

```text
账号认证与范围校验
  → 识别日常对话、会话回顾或技术问题
  → 加载会话、摘要与相关长期记忆
  → 改写问题、确定查询日期、规划最多三步检索
  → 按权限和日期选择文档版本，查询文档 / SQL 故障 / SQL 设备工具
  → 汇总证据并生成带引用的回答
  → 核验引用与回答支持性
  → 保存会话
```

### 本地、GitHub、云端和 Docker 的区别

| 位置 | 保存或运行的内容 | 更新方式 |
| --- | --- | --- |
| Windows `D:\Code\Smart-Factory` | 本地源码、`.env`、虚拟环境及本地数据 | 本地编辑 |
| GitHub 仓库 | 已提交、推送的代码和文档 | `git push` |
| 云服务器 `~/Smart-Factory` 等目录 | 云端源码、`.env.deploy`、部署文件及持久化目录 | `git pull` |
| 云端 Docker 镜像及容器 | 构建时复制进去的代码，以及实际运行的服务 | 构建镜像并重建应用容器 |

“部署在云上”和“运行在 Docker 里”可以同时成立：本项目默认就是在云服务器上使用 Docker 运行。网页用户访问容器提供的服务，不会直接看到服务器的项目文件夹。

本地数据库和云端数据库默认相互独立。推送代码、拉取代码、构建镜像都不会自动同步两边数据库。Git 管理的文件删除会随提交和拉取同步；服务器自行生成的日志、缓存、`.env.deploy`、数据库目录等不会因此自动删除。

## 功能与权限

| 功能 | 操作员 | 工程师 | 专家 |
| --- | --- | --- | --- |
| 知识问答、日期查询、版本比较 | 当前权限范围 | 当前权限范围 | 当前权限范围 |
| 查看设备参数与故障知识 | 可以 | 可以 | 可以 |
| 新增、修改设备参数与故障知识 | 不可以 | 可以 | 可以 |
| 查看已发布工艺文档及版本目录 | 当前权限范围 | 当前权限范围 | 当前权限范围 |
| 文档切分预览、上传、发布、撤回 | 不可以 | 不可以 | 可以 |
| 查看系统运行状态 | 不可以 | 不可以 | 可以 |
| 创建本租户账号、启用/禁用、重置其他账号密码 | 不可以 | 不可以 | 可以 |
| 管理自己的会话和显式长期记忆 | 可以 | 可以 | 可以 |

角色按“操作员 < 工程师 < 专家”分级，文档的访问级别表示最低可读角色。专家的技术数据访问也受自身工厂和产线限制，不是跨租户管理员。

## 项目目录

```text
Smart-Factory/
├─ README.md                     项目使用与维护说明
├─ requirements.txt              Python 依赖统一入口
├─ .env.example                  本地配置模板
├─ .env.deploy.example           云端配置模板
├─ compose.yaml                  本地 MySQL / Redis
├─ compose.deploy.yaml           云端完整服务
├─ Dockerfile                    前后端共用镜像
├─ mains.py                      FastAPI 入口及服务初始化
├─ auth.py                       账号、密码哈希、登录凭证和认证边界
├─ api_client.py                 前端认证请求与登录失效处理
├─ app_frontend.py               Streamlit 入口与知识助手
├─ business.py / business_ui.py  业务接口与设备、故障表单
├─ settings_ui.py                文档、版本、状态和账号设置
├─ init_chramdb.py               Chroma 与嵌入模型初始化
├─ ingestion.py / chunking.py    文件加载、切分和片段身份
├─ rag.py                        混合召回、过滤、融合和引用
├─ workflow.py                   意图、日期、多跳、摘要和回答核验
├─ intent.py                     日常对话和会话回顾路由
├─ state_store.py                MySQL / Redis 状态及版本目录
├─ database/                     建表与演示 SQL
├─ scripts/                      初始化、演示、评测和联通检查工具
├─ tests/                        离线回归测试
├─ examples/                     演示文档和评测数据集
└─ deploy/                       部署指南与 Nginx 配置
```

下列目录或文件在本地运行时生成，不提交 Git：

| 路径 | 用途 | 清理注意事项 |
| --- | --- | --- |
| `.env`、`.env.deploy` | 连接配置和密钥 | 保留，另行安全备份 |
| `.venv/` | 本地 Python 环境 | 删除后需重装依赖 |
| `chroma_db2/` | 默认本地知识库向量数据 | 是数据，不是缓存 |
| 云端 `data/mysql/`、`data/chroma/` | 云端 MySQL 和向量持久化数据 | 不要按临时文件清理 |
| 云端 `data/redis/` | Redis 持久化目录 | 与 MySQL、向量数据一起考虑恢复策略 |
| `logs/` | 后端按日期生成的运行日志 | 可按保留期限归档、清理 |
| `__pycache__/` | Python 编译缓存 | 可重新生成 |
| `backups/` | 备份文件 | 确认另有有效备份后再清理 |

## 本地启动

以下命令在 Windows PowerShell、项目根目录执行。使用 Python 3.11–3.12。MySQL 需先可连接；仅安装 Navicat 等客户端不等于已安装并启动 MySQL 服务。

### 1. 准备 Python 和配置

```powershell
cd D:\Code\Smart-Factory
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

已有 `.venv` 或 `.env` 时直接复用，不要覆盖已有配置。编辑 `.env`，至少检查：

```dotenv
DASHSCOPE_API_KEY=填写模型服务密钥
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=factory_kb
MYSQL_USER=factory_app
MYSQL_PASSWORD=填写应用数据库密码
MYSQL_ROOT_PASSWORD=填写本地Docker数据库管理员密码
REDIS_URL=redis://127.0.0.1:6379/0
```

`MYSQL_ROOT_PASSWORD` 供 Docker 创建新 MySQL 实例使用；原生 MySQL 的初始化脚本会另外交互式询问管理员密码。只使用 MySQL、不使用 Redis 时，将 `REDIS_URL` 留空。

### 2. 启动数据库：选择一种方式

**方式 A：本地 Docker 开发数据库。** 已安装并启动 Docker Desktop 时执行：

```powershell
docker compose up -d --wait mysql redis
docker compose ps
```

这是本地 `compose.yaml`，只启动 MySQL 和 Redis；Python 前后端在下一步单独启动。空数据卷首次启动会执行全部建表 SQL，并导入 `02_demo.sql`。已有数据卷不会自动重跑初始化 SQL。MySQL、Redis 分别使用 `mysql_data`、`redis_data` 命名卷。

如果本地 3306 被占用，可在 `.env` 将 `MYSQL_PORT` 改为未占用端口，例如 3307，再重新执行上述 Compose 命令并重启后端，使映射和连接配置一致。

**方式 B：已有 Windows MySQL 或其他可访问的实例。** 先启动原有 MySQL，将 `.env` 的主机、端口和应用账号配置成该实例的实际信息，再执行：

```powershell
.\.venv\Scripts\python.exe scripts/init_mysql.py --admin-user root
```

提示时输入该 MySQL 实例的 root 密码。仅对新建的开发环境需要演示租户、工厂、产线时，可加 `--seed`：

```powershell
.\.venv\Scripts\python.exe scripts/init_mysql.py --admin-user root --seed
```

初始化会创建缺失的表并配置应用账户权限，不重置已有同名应用账户密码。已有数据默认保留，但已有列结构变化需要按版本执行迁移，不能依赖 `CREATE TABLE IF NOT EXISTS` 自动修改旧列。

### 3. 初始化账户表和首个专家

对已有数据库先执行上面的 `init_mysql.py`，以补齐账户表及读写权限。确认目标租户、工厂和产线已存在，执行：

```powershell
.\.venv\Scripts\python.exe scripts/init_auth.py --username expert_a --tenant tenant_A --plant plant_A1 --line line_a1
```

密码通过隐藏提示输入两次，长度12至128个字符。示例范围来自开发演示数据；真实环境需替换为已有范围。不能用脚本为不存在的工厂或不匹配的产线分配账号。

### 4. 启动前后端

第一个 PowerShell 窗口启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn mains:app --host 127.0.0.1 --port 8000
```

第二个窗口进入同一项目目录，启动前端：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app_frontend.py
```

| 地址 | 用途 |
| --- | --- |
| [http://localhost:8501](http://localhost:8501) | 本地网页 |
| [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) | 后端进程健康检查 |
| [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) | API 结构文档 |

用刚创建的专家账号登录，先检查“系统设置 → 运行状态”，再上传资料或分配账号。后端和前端窗口均需保持运行。前端默认连接本机8000端口；需要改地址时，在启动前设置 `$env:BACKEND_URL='http://目标地址:端口'`。

## 云端部署与更新

完整的服务器准备、首次数据迁移、备份和回退步骤见 [deploy/README.md](deploy/README.md)。本地使用 `.env` 和 `compose.yaml`；云端使用 `.env.deploy` 和 `compose.deploy.yaml`，不要混用两套数据库配置。

### 首次部署的执行顺序

1. 准备服务器、Git、Docker 和项目源码；本文常用目录为 `~/Smart-Factory`，部署指南也有 `/opt/smart-factory` 示例，两者以实际目录为准。
2. 仅在首次配置时复制 `.env.deploy.example` 为 `.env.deploy`，填写模型密钥、应用数据库密码和 root 密码。
3. 启动 MySQL、Redis；若要恢复已有数据，在这一步按部署指南导入 MySQL 备份及完整 Chroma 目录。
4. 构建应用镜像，启动完整服务。
5. 对旧库补齐迁移和权限，创建每个租户的首个专家。
6. 验证网页、登录、业务查询、文档上传、知识问答、旧会话恢复。

服务器常用启动命令：

```bash
cd ~/Smart-Factory
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait mysql redis
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml build backend
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait --wait-timeout 300
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml ps
```

云端空库不自动导入 `02_demo.sql`，也不自动创建专家账号。如果没有租户、工厂、产线，先由数据库管理员建立组织数据，再创建专家。

### 日常操作分别需要什么

| 操作 | 是否要构建、重建应用容器 | 是否要数据库 SSH 隧道 |
| --- | --- | --- |
| 修改 Python、前端代码或依赖，让云端运行新版 | 要 | 不需要 |
| 只修改 README | 通常不需要，拉取文档即可 | 不需要 |
| 用本地 Navicat 查询或修改云端数据 | 不需要 | 按本文方式连接时需要 |
| 在服务器终端执行 MySQL 查询 | 不需要 | 不需要额外数据库隧道 |
| 用网页维护设备参数、故障知识或账号 | 不需要 | 不需要数据库隧道 |
| 修改容器环境配置 | 通常需重建受影响容器，不一定要重新构建镜像 | 不需要 |
| 新增数据库表或字段 | 按变更执行迁移；若同时有代码更新，再部署新版 | 取决于管理员采用的连接方式 |

日常代码更新直接使用本文最顶部的命令。`restart` 只重启旧容器，不会把新源码复制进旧镜像。MySQL 初始化 SQL 只在空数据目录运行，更新镜像不会自动给已有库补齐任意新字段。

### 查看状态与应用日志

在云服务器项目目录执行：

```bash
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml ps
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml logs --tail=100 backend frontend nginx
```

应用内部日志在 `logs/main_日期.log`；默认云端没有单独挂载该日志目录，重建应用容器可能丢失容器内的旧日志。需长期保留时应另行配置归档或持久化。`/health` 成功仅证明进程能响应，不能代替真实登录、数据库和 RAG 验证。

## 账号与身份隔离

### 首个专家和日常分配

系统没有默认用户名、默认密码和自助注册入口。首个专家由服务器或数据库维护人员执行初始化命令创建；其他账号由本租户专家在“系统设置 → 账户与权限”中分配。

云端已有库先执行：

```bash
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml exec backend python scripts/init_mysql.py --admin-user root
```

输入云端 MySQL root 密码，再创建专家：

```bash
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml exec backend python scripts/init_auth.py --username expert_a --tenant tenant_A --plant plant_A1 --line line_a1
```

目标范围必须真实存在；不要在已有生产库上为解决范围缺失而随意导入演示数据。每个租户首个专家分别创建。`scripts/init_auth.py` 不带参数时仅初始化账户表，不创建账号；初始化缺失表需要数据库建表权限。

专家可为本租户已有工厂、产线创建操作员、工程师或专家，用户名允许英文字母、数字、下划线、点和短横线，最多100字符。用户名在当前数据库中唯一，用户身份使用服务端生成的 UUID。

当前账号管理支持创建、启用、禁用和重置其他账号密码，尚无修改已有账号角色或所属范围的页面。专家不能在此处修改自己的账号；维护时应保留可用的专家管理入口。

### 身份与数据范围

- 登录页只填写用户名和密码，不允许自行选择租户、工厂、产线或角色。
- 一个账号绑定一个租户、一个工厂、一个产线和一个角色。设备参数限定到绑定范围；故障知识按租户共享。
- 文档按租户、工厂、产线与最低角色过滤；空 `line_id` 表示同工厂共享资料。网页上传使用账号绑定产线，不提供工厂共享范围切换。
- 专家可管理本租户账号，但不能跨租户分配；自身技术数据读取范围也不会因此扩大。
- 会话与长期记忆按用户 UUID、租户、工厂、产线及角色组合隔离，不能通过猜测会话 ID 读取他人记录。
- 业务请求需要携带登录返回的 `user_ctx`，后端与服务端身份逐项核对，篡改身份或访问范围返回403。

### 密码与登录有效期

| 项目 | 当前行为 |
| --- | --- |
| 密码存储 | 独立随机盐的 scrypt 哈希，不能从表中直接读取原密码 |
| 登录凭证 | 随机 Bearer 令牌，数据库保存其 SHA-256 哈希 |
| 有效期 | 默认8小时，`AUTH_SESSION_SECONDS=28800`；配置最低有效值300秒 |
| 连续错误密码 | 同账号累计5次失败后锁定15分钟，成功登录后清零 |
| 退出登录 | 撤销当前令牌，并清空前端会话状态 |
| 禁用、启用或重置密码 | 撤销该账号所有已有登录，需重新登录 |

网页专家账号密码、MySQL root 密码、应用数据库密码、SSH 登录密码、SSH 私钥口令是不同的凭据。不要将明文密码写入 `auth_users.password_hash`，也不要为重置密码直接修改这张表；使用专家账号管理流程以同时撤销旧凭证。

从旧演示登录升级后，旧会话、记忆仍保留在数据库，但不会自动转交给新 UUID 账号。公网访问应配置 HTTPS。

## 数据库与数据持久化

### 表结构与初始化文件

| SQL 文件 | 主要内容 |
| --- | --- |
| `database/01_schema.sql` | `tenants`、`plants`、`production_lines`、`device_parameters`、`fault_knowledge`、`process_documents` |
| `database/02_demo.sql` | 开发演示组织、设备参数和故障知识，不含登录账号 |
| `database/03_rag_state.sql` | `rag_document_families`、`rag_document_versions`、`rag_sessions`、`rag_messages`、`rag_user_memories` |
| `database/04_auth.sql` | `auth_users`、`auth_sessions` |

`init_mysql.py` 执行结构文件，并为业务表、RAG 表、账户表配置相应权限；`--seed` 才导入演示记录。`init_rag_state.py` 仅用于补齐 RAG 状态表，不替代业务表与账户初始化。没有通用的数据库版本迁移系统，旧表结构变化需核对增量迁移。

租户 → 工厂 → 产线是组织主数据，当前没有网页维护入口。新环境可使用开发种子数据练习；真实环境由数据库管理员在上述三张表中建立并核对归属。

### MySQL、Chroma 和 Redis 分别保存什么

| 存储 | 数据 | 维护方式 |
| --- | --- | --- |
| MySQL | 账号、业务表、权威版本目录、会话、摘要、长期记忆 | SQL 备份与恢复 |
| Chroma | 文档片段、元数据和向量 | 保存整个持久化目录，保持模型和集合配置一致 |
| Redis | 近期会话缓存 | 可降级，MySQL 才是会话持久化来源 |

RAG 上传写入 `rag_document_versions` 与 Chroma，不同步旧 `process_documents` 目录。当前上传原文件不作长期归档，不能依靠系统恢复原始 PDF/DOCX；如需下载和归档，应单独保存原文件。

云端默认持久化目录为 `data/mysql/`、`data/chroma/`、`data/redis/`，分别挂入对应容器；本地 Chroma 默认 `chroma_db2/`。不要只备份 Chroma 的 SQLite 文件，索引目录也需要完整保存。更换 `EMBEDDING_MODEL` 应使用新的集合并重新入库，不能混用不同向量维度或语义空间。

### 在云服务器终端查看 MySQL

```bash
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml exec mysql mysql -u root -p
```

提示时输入 MySQL root 密码，进入后可执行：

```sql
USE factory_kb;
SHOW TABLES;
SELECT username, role, tenant_id, plant_id, line_id, active FROM auth_users;
SELECT l.line_id, l.plant_id, p.tenant_id
FROM production_lines l JOIN plants p ON l.plant_id = p.plant_id;
exit;
```

数据库名称以 `MYSQL_DATABASE` 为准。退出 MySQL 使用 `exit;` 或 `quit;`。直接修改 SQL 数据会作用于当前连接的真实数据库，不需要构建镜像；已打开页面可能要刷新。涉及账号、文档版本状态等业务规则时，应使用系统接口或页面，避免绕过密码处理、凭证撤销或缓存失效逻辑。

## SSH 隧道与访问方式

### 三种访问入口不要混淆

| 场景 | 本地入口 | 云端目标 | 用途 |
| --- | --- | --- | --- |
| 网页 SSH 隧道 | `127.0.0.1:8080` | 服务器 `127.0.0.1:8080` | 浏览器访问 Streamlit 网页 |
| MySQL SSH 隧道 | `127.0.0.1:13306` | 服务器 `127.0.0.1:3307`，再映射到容器3306 | Navicat、DBeaver 访问云端数据库 |
| 公网网页 | 服务器 IP / 域名的80或443端口 | 按实际公网反向代理配置 | 无需另开网页隧道 |

`localhost` 永远指当前运行程序所在的机器：Windows 浏览器的 `localhost` 是你的电脑，云端容器的 `127.0.0.1` 是该容器自己。不能在云端后端容器中用 `127.0.0.1` 代替同项目 MySQL 服务名 `mysql`。

仓库默认 Nginx 只映射服务器回环地址8080；如果公网80已经能访问，说明实际服务器另有公网入口配置。两个入口是否共用项目和数据库，以真实反向代理目标为准；浏览器登录状态也可能独立。

### 数据库 SSH 隧道快速配置

完整说明见[部署指南的 MySQL 隧道章节](deploy/README.md#通过-ssh-隧道连接云端-mysql)。在服务器项目目录首次创建：

```bash
cat > compose.mysql-access.yaml <<'EOF'
services:
  mysql:
    ports:
      - "127.0.0.1:3307:3306"
EOF

sudo docker compose --env-file .env.deploy -f compose.deploy.yaml -f compose.mysql-access.yaml up -d --no-deps --wait mysql
```

这一步重建 MySQL 容器，会短暂中断连接，但沿用持久化数据。只绑定服务器本机，不需开放公网3307。已有此文件时先核对内容，不要重复覆盖自定义配置。

Windows PowerShell 打开隧道：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:13306:127.0.0.1:3307 ubuntu@服务器公网IP
```

保持窗口运行。在 Navicat / DBeaver 建立普通 MySQL 连接：主机 `127.0.0.1`、端口 `13306`，用户名和密码取自云端 `.env.deploy` 的 `MYSQL_USER`、`MYSQL_PASSWORD`，数据库默认 `factory_kb`。工具内无需重复启用 SSH；修改并提交后就是修改云端数据。

隧道可长期保持，但没有自动重连。关闭窗口、电脑休眠、断网或服务器重启后若已断开，重新执行即可。隧道断开不影响云端应用通过 Docker 内网连接数据库。`Ctrl+C` 关闭隧道；本地13306被占用时可换13307，并同步修改数据库工具端口，服务器目标3307保持不变。

保留 `compose.mysql-access.yaml` 后，任何可能创建或重建 MySQL 的 Compose `up` 命令都应同时带上两个 `-f` 文件，尤其是启动全部服务或备份后恢复全部服务时。只重建 `backend frontend nginx` 的常用应用更新流程不需要重建 MySQL。

## 系统使用流程

### 第一次使用

1. 专家登录，检查“系统设置 → 运行状态”。
2. 在“账户与权限”分配账号，选择实际所属工厂、产线和角色。
3. 在“设备参数”“故障库”核对或维护业务数据。
4. 在“文档上传”选择资料，预览切分，填写版本及权限后上传。
5. 在“版本管理”核对发布状态和生效时间。
6. 在知识助手提问，展开引用原文和检索过程检查答案依据。
7. 退出后用普通账号验证可见范围和写入限制。

### 业务数据维护

设备和故障列表支持关键词、设备筛选；故障还支持严重程度，每页50条。工程师、专家通过“新增记录”或“修改记录”维护，不需要自己写 SQL。

设备参数填写设备名称、参数名称、值和可选偏差，自动归属账号绑定产线。故障填写设备类型、代码、描述、解决步骤和严重程度，归属当前租户。重复键返回冲突；修改请求带原记录 `revision`，遇到并发修改需刷新后重试。

故障问答会查询最新 SQL 记录，表单修改后无需重新上传文档或生成向量。设备、故障表单维护的是业务表，不会自动为这些记录额外创建文档片段。

### 文档上传和工艺文档浏览

支持 PDF、TXT、MD、DOCX，默认最大20MB。TXT/MD 支持 UTF-8 和 GB18030；PDF 页码从1开始，普通文本无页码时为0。扫描型 PDF 需先 OCR，系统不内置 OCR。

上传页面依次选择文件、标题、分类和最低阅读角色，再按需展开文档系列、版本号、生效/失效日期、发布选项和切分策略。标题留空使用文件名，系列留空使用标题。

“预览切分结果”只解析文件，不写数据库，不调用嵌入模型。“上传到知识库”会登记版本并建立向量。“工艺文档”页面显示当前权限内、当前日期有效的已发布版本；“版本管理”用于检查版本及由专家发布、撤回。

### 会话、回顾和长期记忆

纯问候、感谢和常见会话回顾由本地路由处理，不访问向量库或回答模型。其他日常对话由意图规划处理；技术问题才进入证据检索。意图规划失败先追问，不把所有消息强行送入知识检索。

前端会话使用 UUID，可复制并恢复。恢复仍须匹配原账号身份范围；同一会话并发问答返回409，以免消息次序混乱。清除聊天会清除该会话消息、摘要和有效缓存，不删除独立管理的长期记忆。

多轮使用最近8条消息及摘要改写问题。较早未摘要消息达到字符阈值且超过8条时分批摘要，保留最近8条原文；失败保留原始记录。会话回顾使用当前可用原始记录，不把摘要推断当作用户原话。

长期记忆在页面明确保存为偏好、背景或审核经验，每个身份范围最多50条。回答最多注入3条偏好及5条关键词相关背景/经验，关联来源失效或过期的记忆不注入。模型答案不会自动成为长期知识，记忆不能替代正式文档。

## 知识库与 RAG 规则

### 切分、召回与访问过滤

- 默认 `structured`：先识别章节和段落，章节内超长内容递归切分；`recursive` 为纯递归模式。不会跨章节做重叠。
- 默认片段上限800字符、重叠目标120字符。重复章节路径计入字符预算，正文位置、文件名、PDF 页码和章节路径保留在元数据中。
- Markdown 识别标题并忽略代码围栏内伪标题；Word 读取 Heading / 标题样式及表格；TXT/PDF 识别编号标题，不根据字体布局重建所有复杂章节。
- BM25 中文双字词与设备/故障代码匹配，加向量双路召回；每路默认20个候选，通过 RRF 融合排序。
- 检索前过滤访问范围和有效版本，得到候选后再次校验元数据；重复内容去重，每份文档最多2个片段，并受总上下文预算限制。
- 同范围、相同内容及元数据的重复上传使用稳定 ID，不重复嵌入。切分方式或元数据变化会改变身份，应使用新版本号。
- BM25 索引按访问范围缓存，默认最多32个范围、60秒刷新；上传和状态变化会使本进程缓存失效，多进程需考虑其他进程的缓存期限。

`RAG_MAX_DISTANCE` 是向量距离上限，不是正确概率；BM25 命中可以独立进入候选。不同模型和集合的距离分布不同，阈值应根据实际评测调整。

### 版本、生效日期与历史比较

| 规则 | 行为 |
| --- | --- |
| 文档系列 | 同一标准不同文件、年份应使用相同系列名称；系列按租户/工厂/产线隔离 |
| 生效区间 | `[effective_from, effective_to)`，失效日期当天起不适用 |
| 当前版本选择 | 按查询日期选择生效最晚的已发布版本，再检查失效与权限 |
| 草稿或未来版本 | 不会提前替代当前版本 |
| 新版本撤回、过期或无权限 | 不自动回退旧版本，避免误用旧标准 |
| 相同系列和版本号 | 不允许登记不同内容或不同切分结果 |
| 相同生效日期 | 同系列两个版本不能同时发布 |
| 上传中断 | 保持草稿，相同参数可重试；发布前核验向量是否完整 |
| 上传或发布期间目录变化 | 问答会再次核对，避免输出基于过期版本的确定结论 |

页面可以指定日期或比较两个不同日期；两种方式互斥。文本支持明确日期、年份及“今年/去年/前年”等受限表达；只说历史年份时按年末，“今年”按服务端当前日期。每次最多两个日期，不支持任意自然语言日期区间。

旧向量片段如果没有权威版本登记，默认不参与问答。应重新上传并明确系列、版本、生效日期。撤回会影响该生效日起的查询；当前未实现完整的发布状态双时间审计，不能精确重放任意历史时刻的目录状态。

### SQL 工具、多跳与答案核验

最多三步依赖检索，工具包括文档、限定范围的设备参数、按租户共享的故障知识；不允许模型自由生成 SQL。后续步骤可根据前置原文补齐设备型号或故障码，再继续查询。

简单明确故障码及短追问使用本地路由；复杂设备限定、现象检索和混合任务由规划器处理。同代码有多个设备类型时先追问，匹配超过10条时要求缩小范围；SQL 无匹配再补查有效文档。设备与故障表是当前快照，不用于伪造历史事实。

必要步骤缺证据、依赖不完整或上下文预算不足时，不生成完整技术结论。返回引用 `[S1]`、原文、查询日期和检索轨迹，并检查引用、步骤覆盖、数值、单位、条件及冲突。

`citation_valid` 表示规则和支持性核验通过，`confidence` 是“证据充分/证据有限/无法判断”及原因，不是经过校准的正确率。模型核验也可能误判，关键操作需由人员核对原文。检索、规划、摘要和核验会增加模型调用次数、费用与响应时间。

## 配置项说明

本地模板见 [.env.example](.env.example)，云端模板见 [.env.deploy.example](.env.deploy.example)。真实密钥只写入各环境配置，不提交 Git。已有数据库或账户密码不会因为编辑模板、重启容器而自动重置。

| 变量 | 默认或示例 | 作用 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 无 | 模型及嵌入服务密钥 |
| `LLM_MODEL` | `qwen-max` | 回答与规划使用的模型 |
| `LLM_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容模型接口地址 |
| `EMBEDDING_MODEL` | `text-embedding-v3` | 文档嵌入模型 |
| `CHROMA_DIRECTORY` | 本地 `./chroma_db2`；云端 `/data/chroma` | 向量存储目录 |
| `CHROMA_COLLECTION` | `factory_knowledge` | 向量集合 |
| `MYSQL_HOST` / `MYSQL_PORT` | 本地 `127.0.0.1:3306`；云端 `mysql:3306` | 应用数据库地址 |
| `MYSQL_DATABASE` | `factory_kb` | 数据库名称 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `factory_app` / 自行设置 | 应用数据库账号 |
| `MYSQL_ROOT_PASSWORD` | 自行设置 | Docker 新实例 root 密码；不作为普通网页登录密码 |
| `REDIS_URL` | 本地 `redis://127.0.0.1:6379/0`；云端 `redis://redis:6379/0` | 缓存地址，本地可留空 |
| `AUTH_SESSION_SECONDS` | `28800` | 登录有效期秒数 |
| `UPLOAD_MAX_MB` | `20` | 后端上传文件大小上限 |
| `RAG_SPLIT_STRATEGY` | `structured` | 默认切分策略 |
| `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` | `800` / `120` | 片段字符预算 / 重叠目标 |
| `RAG_CANDIDATE_K` | `20` | 每路候选数量 |
| `RAG_MAX_DISTANCE` | `1.2` | 向量距离上限 |
| `RAG_CONTEXT_CHARS` | `10000` | 总证据字符预算 |
| `RAG_CACHE_SECONDS` | `60` | 进程内检索缓存期限 |
| `RAG_MAX_HOPS` | `3` | 最大检索步数，代码限制在1至3之间 |
| `MEMORY_TTL_SECONDS` | `86400` | Redis 上下文缓存期限，不是 MySQL 会话保留期限 |
| `MEMORY_SUMMARY_CHARS` | `6000` | 历史摘要触发字符阈值 |
| `BACKEND_URL` | 本地 `http://127.0.0.1:8000`；云端 `http://backend:8000` | 前端进程环境中的后端地址 |
| `APP_TAG` | `local` | 云端 Compose 镜像标签，可选 |

云端 Compose 显式覆盖数据库主机、端口、Redis 地址和 Chroma 目录，不要用宿主机地址覆盖容器内服务名。前端的 `BACKEND_URL` 由进程环境或 Compose 提供，不会因为在本地 `.env` 加一行就自动被前端读取。

提高上传上限还要同步检查 Streamlit `--server.maxUploadSize` 和 Nginx `client_max_body_size`；当前云端分别为20MB和22MB。只有后端变量变大不能解除前端和代理限制。

## API 使用

本地 API 文档为 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。云端默认 Nginx 只代理前端，没有公开后端8000或 `/docs`；需要调试时使用服务器内部请求或另外受控的后端隧道。

### 认证和一次问答示例

除 `GET /health` 和 `POST /auth/login` 外，业务接口需要 `Authorization: Bearer <access_token>`。有 `user_ctx` 的接口必须原样使用登录返回的身份，不能自行填写其他账号 UUID、角色或访问范围。下面在本地 PowerShell 执行，密码通过隐藏输入，不写入命令历史：

```powershell
$credential = Get-Credential -UserName 'expert_a' -Message '输入网页专家账号密码'
$loginBody = @{
    username = $credential.UserName
    password = $credential.GetNetworkCredential().Password
} | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/auth/login' -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($loginBody))
$headers = @{ Authorization = "Bearer $($login.access_token)" }
$queryBody = @{
    question = 'F0012故障如何排查？'
    user_ctx = $login.user_ctx
    top_k = 5
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/query' -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($queryBody))
```

不要分享 Bearer 令牌或包含密码的请求内容。

### 接口列表

| 方法与路径 | 用途与关键字段 |
| --- | --- |
| `POST /auth/login` | `username`、`password`；返回令牌、过期时间、用户名和 `user_ctx` |
| `GET /auth/me` | 校验当前身份 |
| `POST /auth/logout` | 撤销当前登录 |
| `GET /auth/scopes` | 专家查看本租户可分配的工厂和产线 |
| `GET /auth/accounts` | 专家查看本租户账号 |
| `POST /auth/accounts` | 专家创建账号：用户名、密码、角色、工厂、产线；租户由服务端确定 |
| `POST /auth/accounts/{user_id}` | 专家启用/禁用或重置其他账号密码：`active`、`password` |
| `POST /query` | 技术问答或日常对话，支持会话和日期 |
| `POST /retrieve` | 仅按日期、权限和版本检索文档，不做回答生成；仍可能调用嵌入服务 |
| `POST /query-equipment` | 按账号范围查询设备，字段 `device_name`、`user_ctx` |
| `POST /query-faults` | 当前租户故障查询，支持 `device_type`、`fault_code` |
| `POST /business/equipment/list` | 设备列表、筛选、分页 |
| `POST /business/equipment/save` | 新增或修改设备参数 |
| `POST /business/faults/list` | 故障列表、筛选、分页 |
| `POST /business/faults/save` | 新增或修改故障知识 |
| `POST /test_file-upload` | 专家切分预览，multipart：`file`、可选 `split_strategy` |
| `POST /upload-document` | 专家上传及登记版本，multipart 字段见下文 |
| `POST /documents/list` | 当前范围内可见文档目录及当前生效 ID |
| `POST /documents/{document_id}/status` | 专家发布或撤回，`status=published/withdrawn` |
| `POST /sessions/{session_id}/history` | 读取自己的会话 |
| `POST /sessions/{session_id}/clear` | 清除自己的会话 |
| `POST /memories/list` | 列出自己的长期记忆 |
| `POST /memories` | 保存长期记忆 |
| `POST /memories/{memory_id}/delete` | 删除自己的长期记忆 |
| `GET /health` | 无需登录的进程健康检查 |
| `GET /db-status`、`GET /vector-status`、`GET /memory-status` | 专家查看运行状态；业务和向量计数按访问范围过滤 |
| `POST /execute-sql` | 已停用，登录后调用返回410；改用专用业务接口 |

### 请求和返回约定

- `/query`、`/retrieve`：`question` 必填、最多2000字符，`top_k` 默认5、范围1至10，`user_ctx` 为登录返回值。`/query` 可带会话 UUID；日期字段为 `as_of` 或恰好两个不同的 `comparison_dates`，二者互斥。
- `/query` 返回 `answer`、`session_id`、意图/诊断信息、`sources`、`citation_valid`、`confidence`、`retrieval_steps` 等；具体字段取决于日常对话或技术检索分支。没有检索的回复不展示技术证据置信度。
- 上传：`file`、JSON字符串 `user_ctx`、`category`、`access_level`；可选 `title`、`split_strategy`、`family_name`、`version`、`effective_from`、`effective_to`、`publish`。版本默认1、生效日期默认服务端当天；`publish=false` 保存草稿。响应包括文档 ID、片段数、新增片段数、是否重复和版本状态。
- 业务列表：`user_ctx`，可选 `keyword`、`device`、`severity`、`page`。响应有 `items`、`total`、`page`、`page_size`；设备列表不使用故障严重程度过滤。
- 业务保存：`user_ctx`、`record`。新增不传 `id`；修改时在请求体顶层传 `id` 和列表返回的 `revision`，防止覆盖他人修改。设备 `record` 包含 `device_name`、`parameter`、`value`、可选 `tolerance`；故障 `record` 包含 `device_type`、`code`、`description`、`solution`、`severity`（低/中/高）。
- 长期记忆：`user_ctx`、`content`（最多1000字符），可选 `kind=preference/background/reviewed_experience`、`source_id`、`valid_until`。保存不会自动审核内容正确性。
- 文档列表/状态、会话、记忆接口使用的请求体仍需 `user_ctx`；认证账号管理接口不需要客户端指定租户。

| 状态码 | 常见含义 |
| --- | --- |
| `401` | 未登录、凭证过期/撤销，或登录账号密码错误、禁用、锁定 |
| `403` | 当前角色无权限，或客户端身份与服务端身份不一致 |
| `404` | 记录不存在，或当前范围无法访问 |
| `409` | 重复记录、并发修改、会话忙或版本冲突等 |
| `410` | 自由 SQL 接口已停用 |
| `413` / `415` | 文件过大 / 格式不支持 |
| `422` | 参数、日期、范围或文件内容不符合要求 |
| `500` / `503` | 服务执行失败、配置无效或依赖服务不可用，结合日志定位 |

## 测试与检索评测

### 离线回归

在本地项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

测试位于 `tests/`，覆盖认证边界、租户/用户隔离、设备和故障表单、文件切分、真实临时 Chroma 过滤、版本选择、意图、多跳、记忆和会话并发。离线测试不依赖真实 MySQL，不调用付费模型；会使用临时存储。整理目录时验证通过90项测试，数量以后续实际输出为准。

只验证认证、API、业务模块时：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_auth tests.test_api tests.test_business -v
```

`tests.test_auth` 通过完整身份认证链路；其他部分 API/业务规则测试会隔离认证依赖，以测试具体功能。这不代表实际运行时可以跳过认证。

### 模型联通检查与检索评测

```powershell
# 显式调用外部嵌入服务，可能产生费用
.\.venv\Scripts\python.exe scripts/check_embedding.py

# 使用实际模型、向量库及版本目录评测文档检索
.\.venv\Scripts\python.exe scripts/evaluate_rag.py examples/evaluation.jsonl --output evaluation-report.json
```

联通检查脚本当前固定测试 `text-embedding-v3`；更换生产嵌入模型后，这个检查不等于完整验证新模型。评测脚本直接使用本地配置访问存储，不走 HTTP 登录接口，不应作为对外服务开放。

评测前在数据集对应范围上传 `examples/故障手册.txt` 等资料，并登记有效版本。输出文件级 Recall@K、MRR、无答案问题的空召回正确率和逐题耗时；示例集用于链路检查，真实质量需用实际业务问题、正确来源文件及不同权限范围评估。

## 演示数据

所有演示设备参数、故障案例及点检资料都是虚构示例，不可作为现场操作依据。`02_demo.sql` 提供基础组织和少量业务记录；补充脚本覆盖4条产线、24类设备、168条额外设备参数、48个虚构故障案例及16份版本资料。

| 租户 | 工厂 | 产线 | 场景 |
| --- | --- | --- | --- |
| `tenant_A` | `plant_A1` | `line_a1` | 模组装配 |
| `tenant_A` | `plant_A2` | `line_a2` | 电芯封装 |
| `tenant_B` | `plant_B1` | `line_b1` | 机械加工 |
| `tenant_C` | `plant_C1` | `line_c1` | SMT |

在已具备这些组织范围的本地开发库执行：

```powershell
# 只查看数量及组织范围
.\.venv\Scripts\python.exe scripts/enrich_demo.py --inspect

# 向MySQL补充演示设备参数和故障，不覆盖已有同业务键记录
.\.venv\Scripts\python.exe scripts/enrich_demo.py

# 还上传当前专家绑定产线的知识资料，密码隐藏输入
.\.venv\Scripts\python.exe scripts/enrich_demo.py --with-knowledge --username expert_a
```

脚本先检查4条演示产线归属，范围不匹配会停止。设备与故障数据在同一事务中提交；文档逐份上传，重复执行可跳过已有业务记录或重复向量。`--with-knowledge` 当前固定连接 `http://127.0.0.1:8000`，需本地后端已启动；每次只导入登录专家绑定产线的资料，其他产线分别使用相应专家账号。

可尝试“F0012故障如何排查？”、“RB1001是什么故障？”、“PLC控制柜有哪些演示参数？”或比较不同日期的演示点检规则。没有上传有效资料或账号范围不匹配时，不应期待返回相应文档答案。

## 备份与维护

1. 数据备份与代码提交分开管理。代码在 Git 中，不等于 MySQL、Chroma、密钥配置已有备份。
2. 更新或迁移前按[部署指南](deploy/README.md#4-每次更新前备份)暂停应用写入，备份 MySQL 和完整 Chroma 目录，再恢复应用，减少跨存储不一致。
3. 备份文件应复制到另一台设备或对象存储，验证可以恢复；`.env.deploy` 含密钥，另行安全保管。
4. 纯代码回退不能自动撤销已执行的数据库迁移，需验证兼容性或按备份恢复。
5. 不要删除 `data/`、知识库目录或对包含数据的开发栈执行 `docker compose down -v`；它们不是临时缓存。
6. 生产账号使用适当的数据库权限，配置 HTTPS、日志归档、监控及备份流程；默认部署不提供这些完整运维能力。

## 常见问题

### MySQL 连接被拒绝，出现10061或2003

这表示目标地址/端口尚未接受连接，通常还没进入密码校验。检查 MySQL 服务、容器状态、主机和端口。`127.0.0.1` 是当前机器/容器，不是默认指向云服务器；本地和云端配置应分别核对。不要为连不上原有库直接创建新的空实例覆盖路径。

### MySQL 提示 Access denied 或缺少表

`Access denied` 检查账户密码、允许连接的来源和权限；已有数据库密码不会随 `.env` 自动重置。表不存在时检查是否执行正确结构 SQL，是否连接了正确数据库。已有数据卷不会自动运行新增初始化文件，可用管理员执行 `scripts/init_mysql.py` 补齐表和权限；旧列变化仍需独立迁移。

MySQL 8认证若提示需要 `cryptography`，本地可安装与 Dockerfile 相同的额外依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install "PyMySQL[rsa]==1.1.1"
```

### Docker 提示 docker.sock permission denied

在云服务器使用本文带 `sudo` 的 Compose 命令。`[sudo] password` 是服务器用户密码；后续“MySQL 管理员密码”才是数据库 root 密码，二者不同。

### 容器内找不到 scripts/init_auth.py

检查云端 `git pull` 是否成功、服务器目录中是否已有脚本，再执行构建和重建应用容器。只更新服务器源码不会更新旧镜像。进入错误项目目录或仍运行旧容器也会出现同样现象。

### git pull 长时间无输出

先用 `Ctrl+C` 取消，检查远程地址和网络：

```bash
git remote -v
timeout 20s git ls-remote origin HEAD
curl -4 -vI --connect-timeout 10 --max-time 15 https://github.com
```

GitHub 域名能解析不代表TCP连接一定通。连接443超时发生在账号验证之前，重新输入密码通常不能解决。可以按[GitHub 官方说明](https://docs.github.com/zh/authentication/troubleshooting-ssh/using-ssh-over-the-https-port)测试 SSH 443 入口：

```bash
ssh -T -p 443 -o ConnectTimeout=10 git@ssh.github.com
```

首次连接须按[官方 SSH 指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)核对主机身份；提示 `Permission denied (publickey)` 时需配置当前服务器的公钥权限。看到 `Hi 用户名!` 表示认证成功，GitHub 不提供 shell 是正常提示。成功后可将当前仓库改成该入口，再拉取：

```bash
git remote set-url origin ssh://git@ssh.github.com:443/你的账号/Smart-Factory.git
git pull --ff-only
```

仓库路径以 `git remote -v` 的实际所属账号/组织为准，不要原样保留“你的账号”。不要分享含令牌的仓库地址或私钥。

### SSH 提示 Enter passphrase for key

这是创建该 SSH 私钥时设置的口令，不是 MySQL、网页或 GitHub 登录密码。输入不回显。云服务器私钥与 Windows 私钥也是分别保存的，不能假设两边自动共用密钥。

### 网页隧道没有输出，或不停出现 Connection refused

无输出通常表示隧道正在保持连接；`channel ... Connection refused` 表示 SSH 已连上，但转发目标未接受连接。检查服务器 Nginx 是否运行、是否监听8080，不能仅凭公网80能打开就认为8080也可用。网页隧道和数据库隧道使用不同端口。

### Navicat 无法连接或端口已被占用

先检查服务器的 `127.0.0.1:3307` 映射是否保留，再检查 Windows 隧道窗口和数据库工具的13306端口。使用手动 PowerShell 隧道时，Navicat 中创建普通 MySQL 连接，不重复配置第二条 SSH 隧道。隧道不会自动重连，断线后重开并重新连接数据库工具。

### 登录失败、没有默认账号或账号已锁定

先创建正确范围的专家；系统没有通用默认密码。检查账号是否禁用、密码是否正确、是否因5次失败锁定15分钟。专家可重置其他账号密码。重置、启用、禁用均使旧凭证失效，原登录需重新进入。

### 上传成功但问答没有资料

逐项检查：账号范围、文档最低角色、系列和版本登记、是否发布、查询日期、生效/失效时间、是否撤回，以及向量是否完整。草稿、未来版本和没有权威目录的旧片段不会参与检索；当前版本不可用时不会自动回退旧版。扫描 PDF 无可抽取文本时需先 OCR。

### 故障数据改了，为什么答案或页面仍没变化

确认连接的是云端实际数据库、事务已提交，并刷新页面或重新提问。新一次故障问答直接读取最新 SQL；之前显示的历史回答不会自动改写。修改文档片段或版本目录与修改故障 SQL 是不同操作，文档应走上传和版本管理流程。

### Redis 不可用或模型服务报错

Redis 不可用时应用可回退 MySQL，状态会显示 `unavailable_mysql_fallback`；缓存期限不代表 MySQL 数据过期。默认云端编排仍要求 Redis 健康后才启动相关服务。

模型错误检查密钥、账户额度、网络、模型名和接口地址。日常本地问候可以不调用模型，但技术问答、规划、摘要和文档嵌入需要相应服务；`/health` 成功不能证明它们都正常。查看专家运行状态和后端日志定位具体依赖。

## 当前边界与后续工作

- 文档加载不含扫描 OCR，不归档上传原文件，也不提供原文件下载功能。
- 没有现成的租户/工厂/产线网页管理、账号角色/范围修改页面或用户自助找回密码流程。
- 版本目录没有完整双时间审计，不保证重放任意历史时刻的发布/撤回状态；设备及故障 SQL 是当前快照。
- 已有混合召回、多跳及回答核验，尚未接入专门的外部 reranker 或语义长期记忆索引。
- 当前部署以单机为主，检索缓存按进程管理；多进程或更大规模需进一步设计索引更新、监控与容量方案。
- 默认云端只提供回环网页入口，没有内置 TLS、自动备份和无人值守发布。公开使用前应完成对应运维配置。

如需定位具体实现，先查看上方项目目录与接口表；部署、完整数据迁移和恢复步骤统一参考 [deploy/README.md](deploy/README.md)。
