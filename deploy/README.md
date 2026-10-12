# 腾讯云单机部署与 Git 更新

这套配置用于先将现有项目迁到服务器验证。登录已接入真实账号与服务端租户、身份校验。入口默认只绑定服务器 `127.0.0.1:8080`，通过 SSH 隧道访问；开放公网前配置 HTTPS。首次登录需按[账号初始化说明](../README.md#账号与身份隔离)创建各租户首个专家，再由专家分配账号。已有数据库升级需导入 `database/04_auth.sql` 并配置账户表读写权限。

## 1. 准备服务器和代码仓库

建议使用 Ubuntu 24.04 LTS、x86_64。小规模演示可从 4 核 8 GB 内存起步，这是容量估计，实际取决于文档量、构建依赖和并发；调用远程 DashScope，不需要 GPU。

腾讯云安全组允许你的管理 IP 访问 SSH 22 端口；本配置无需开放 3306、6379、8000、8501 或 8080。服务器需要能访问代码仓库、Docker 镜像源、Python 包源和 DashScope。

在服务器安装 Git、Docker Engine 和 Compose 插件。按 [Docker 官方 Ubuntu 安装步骤](https://docs.docker.com/engine/install/ubuntu/) 使用 apt 仓库安装，不要复制本地 Windows `.venv`。验证：

```bash
git --version
sudo docker version
sudo docker compose version
```

将本地代码提交到你自己的私有 Git 仓库；本指南不会自动创建仓库或推送代码。提交前检查暂存区，不能包含 `.env`、`.env.deploy`、数据库文件或密钥。在服务器执行（替换仓库地址）：

```bash
sudo mkdir -p /opt/smart-factory
sudo chown "$USER":"$(id -gn)" /opt/smart-factory
git clone <你的仓库地址> /opt/smart-factory
cd /opt/smart-factory
cp .env.deploy.example .env.deploy
chmod 600 .env.deploy
nano .env.deploy
```

填写 DashScope 密钥以及两个不同的强数据库密码；保留 `MYSQL_USER=factory_app`，不要使用 root 作为应用账号。密码包含 `$` 等 Compose 特殊字符时，用单引号包住值。部署环境覆盖了容器内部数据库地址，不需要填写本地 IP。

## 2. 迁移现有数据（首次部署）

先停止本地前后端，避免导出期间继续写入。使用 Navicat 导出整个 `factory_kb` 数据库的结构和数据，选择 UTF-8，不导出 `mysql` 系统库、用户或授权。保留导出文件作为备份。

使用 SFTP / WinSCP 将文件上传到服务器：

- SQL 导出文件 → `/opt/smart-factory/backups/factory_kb.sql`
- 本地整个 `chroma_db2` 目录的内容 → `/opt/smart-factory/data/chroma/`

不要只复制 Chroma 的 SQLite 文件；向量索引文件也必须一起复制。目录层级应为 `data/chroma/chroma.sqlite3`，不能多套一层 `chroma_db2`。嵌入模型、集合名称和本地保持一致。上传文件的原始 PDF/DOCX 目前未归档，本次迁移恢复的是已存储切片和向量。

服务器执行：

```bash
cd /opt/smart-factory
mkdir -p backups data/chroma
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait mysql redis
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root --default-character-set=utf8mb4 "$MYSQL_DATABASE"' < backups/factory_kb.sql
```

此导入只用于首次迁移到新建的云端数据库，不是日常更新命令。云端已经有新增数据时，不要再次导入本地旧备份。云端配置不会自动导入演示数据。

Redis 旧缓存可以不迁移；长期会话、摘要及记忆来自 MySQL，缓存会按需重建。

## 3. 构建并启动

```bash
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml build backend
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait --wait-timeout 300
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml ps
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml logs --tail=100 backend frontend
```

首次构建要下载 Python 依赖，速度取决于网络。镜像或包下载失败时先检查网络，不要跳过依赖安装。

本地 Windows PowerShell 打开 SSH 隧道（替换登录用户和 IP，必要时添加 `-i 私钥路径`）：

```powershell
ssh -N -L 8080:127.0.0.1:8080 ubuntu@服务器公网IP
```

保持这个终端运行，浏览器访问 `http://localhost:8080`。检查系统状态、设备参数、故障查询、知识库检索、引用、旧会话恢复，并上传一份测试文档。重启容器后确认文档仍可检索。HTTP 进程健康检查不代表整个 RAG 业务可用，仍需完成这些操作检查。

### 通过 SSH 隧道连接云端 MySQL

可以在 Windows 上使用 Navicat、DBeaver 等数据库工具，通过 SSH 隧道查看和维护云端 MySQL。连接的是云端实际数据库：新增、修改和删除并提交后，会直接改变云端数据。SSH 隧道只提供连接通道，不会创建本地副本或同步两份数据库。

**服务器首次配置：** 在云服务器项目目录执行。本文示例目录为 `/opt/smart-factory`；如果你的项目在 `~/Smart-Factory`，请改用该目录。

```bash
cd /opt/smart-factory
cat > compose.mysql-access.yaml <<'EOF'
services:
  mysql:
    ports:
      - "127.0.0.1:3307:3306"
EOF

sudo docker compose --env-file .env.deploy \
  -f compose.deploy.yaml \
  -f compose.mysql-access.yaml \
  up -d --no-deps --wait mysql
```

此操作会重建 MySQL 容器，连接会短暂中断；沿用 `./data/mysql` 中的已有数据。映射仅监听服务器本机 `127.0.0.1:3307`，无需在安全组开放 3306 或 3307。后端仍通过 Docker 内网的 `mysql:3306` 连接，不需要修改 `.env.deploy` 中的数据库连接配置。

以后执行可能创建或重建 MySQL 的 Compose 命令（包括备份后恢复全部服务）时，都要同时指定 `-f compose.deploy.yaml -f compose.mysql-access.yaml`，否则可能移除端口映射。例如：

```bash
sudo docker compose --env-file .env.deploy \
  -f compose.deploy.yaml \
  -f compose.mysql-access.yaml \
  up -d --wait --wait-timeout 300
```

**Windows 建立隧道：** 在本地 PowerShell 执行，替换 SSH 用户和服务器公网 IP；如需指定私钥，在 `ssh` 后添加 `-i "C:\路径\私钥文件"`。

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:13306:127.0.0.1:3307 ubuntu@服务器公网IP
```

连接路径为：Windows `127.0.0.1:13306` → SSH 隧道 → 云服务器 `127.0.0.1:3307` → MySQL 容器 `3306`。使用平时登录云服务器的 SSH 密码或私钥认证；私钥口令与 MySQL 密码不同。连接建立后终端没有持续输出是正常的，保持该窗口打开即可。

在 Navicat 或 DBeaver 中创建普通 MySQL 连接，按下面填写；数据库工具内不需要再次开启 SSH 隧道：

| 配置项 | 内容 |
| --- | --- |
| 主机 | `127.0.0.1` |
| 端口 | `13306` |
| 用户名 | 云端 `.env.deploy` 中的 `MYSQL_USER`，通常为 `factory_app` |
| 密码 | 云端 `.env.deploy` 中的 `MYSQL_PASSWORD` |
| 数据库 | 云端 `.env.deploy` 中的 `MYSQL_DATABASE`，默认 `factory_kb` |

**长期保持与断线处理：** 隧道可以一直保持，但 PowerShell 窗口、电脑和网络连接需要保持运行。上述命令每30秒发送 SSH 保活请求，连续3次未响应会退出；保活不会自动重连。关闭窗口、电脑休眠、网络中断或服务器重启后，如果连接已断开，重新执行隧道命令，并让数据库工具重新连接。隧道断开只影响本地数据库工具访问，云端项目继续使用 Docker 内网连接数据库。

用完后在隧道窗口按 `Ctrl+C` 关闭连接。如果提示本地端口已被占用，先检查是否已有隧道运行；也可以将命令中的本地端口 `13306` 改为 `13307`，并同步修改数据库工具的端口。只有隧道命令中的第一个端口是本地端口，服务器目标端口仍为 `3307`。

账号创建、禁用和密码重置使用系统的“账户与权限”页面，确保执行密码哈希和登录凭证撤销逻辑；不要直接在账户表中填写明文密码。

## 4. 每次更新前备份

先暂停应用写入；备份 MySQL 和 Chroma 时保持应用停止，以获得一致的数据快照：

```bash
cd /opt/smart-factory
mkdir -p backups
backup_stamp=$(date +%Y%m%d-%H%M%S)
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml stop nginx frontend backend
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -u root --single-transaction --routines --triggers --events --no-tablespaces --set-gtid-purged=OFF "$MYSQL_DATABASE"' > "backups/mysql-$backup_stamp.sql"
sudo tar -czf "backups/chroma-$backup_stamp.tar.gz" -C data chroma
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait --wait-timeout 300
```

检查 SQL 非空、导出命令成功、压缩包可列出内容，并将备份复制到另一台设备或对象存储。仅放在同一块云盘上不能防止云盘丢失。备份不包含密钥文件，`.env.deploy` 需要另外安全保管。

如果已按上文启用 MySQL 隧道端口映射，恢复全部服务的 `up` 命令也需加入 `-f compose.mysql-access.yaml`，以保留映射。

## 5. 使用 Git 迭代

本地测试通过后提交、推送；服务器目录保持干净，不要直接修改应用代码。服务器执行：

```bash
cd /opt/smart-factory
git status --short
git rev-parse HEAD
git pull --ff-only
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml build backend
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml up -d --wait --wait-timeout 300 --force-recreate backend frontend nginx
sudo docker compose --env-file .env.deploy -f compose.deploy.yaml ps
```

保存更新前的提交号。构建完成后才重建应用，数据库和 Redis 保持运行；单机应用重建会短暂中断会话连接。重建 Nginx 可以刷新前端容器地址。

普通代码更新不导入数据库，也不删除 data 目录。新增字段或修改表结构时，按该版本的数据库迁移说明操作；初始化 SQL 只在空数据库目录运行。当前没有通用的数据库版本迁移系统，`create_all` 也不会自动迁移已有字段。

代码回退：`git switch --detach <更新前提交号>`，随后重复 build 和应用重建命令；下一次更新前切回原部署分支。数据库结构或数据已经变化时，不能把代码回退当成完整回滚，须验证兼容性或使用备份恢复。

部署配置用固定项目名和 `data/` 挂载目录，更新镜像不会删除数据。不要删除 `data/`。不要在旧的开发配置里执行 `docker compose down -v`，那会删除其命名数据卷。

## 6. 公网开放

后端认证、服务端可信租户权限和 HTTPS 完成后，再调整 Nginx 的公网端口、证书和腾讯云安全组规则。不要直接开放数据库、Redis 或 FastAPI。当前部署没有 TLS、自动备份和无人值守发布，适合先通过 SSH 隧道验证，不应直接作为公开生产站点。
