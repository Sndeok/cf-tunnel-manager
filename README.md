# Cloudflare Tunnel Manager

一个轻量级的 Cloudflare Tunnel Web 管理面板，用于在本机或局域网服务器上创建、启动、停止和删除 Cloudflare Tunnel，并自动维护对应的 Cloudflare DNS CNAME 记录。

项目适合用来把家里或内网里的服务通过 Cloudflare Tunnel 暴露到公网，例如 NAS、Home Assistant、内部 Web 面板、开发测试服务等。

> ⚠️ 本项目会保存 Cloudflare API Token、Tunnel Secret、SQLite 数据库和 cloudflared 配置文件。请只部署在可信机器上，并保护好 `data/` 目录。

## 功能特性

- **Web UI 管理面板**
  - 深色 / 浅色 / 跟随系统主题
  - 仪表盘展示隧道总数、运行中数量、停止数量
  - 左侧导航：仪表盘、隧道管理、凭证配置、操作日志

- **Cloudflare 凭证管理**
  - 保存 Account ID、API Token、域名列表
  - 保存后自动调用 Cloudflare API 验证 Token
  - 支持按域名列表限制可选 Zone，避免每次拉取全部 Zone

- **Tunnel 创建与删除**
  - 调用 Cloudflare API 创建 Tunnel
  - 获取 Tunnel Token 并生成本地 cloudflared credentials JSON
  - 自动生成 cloudflared YAML 配置
  - 自动创建 proxied CNAME：`subdomain.example.com -> <tunnel_id>.cfargotunnel.com`
  - 删除隧道时同步删除 Cloudflare Tunnel、DNS 记录、本地配置、日志和数据库记录

- **Tunnel 运行控制**
  - 启动 / 停止本地 cloudflared 进程
  - 使用 PID 文件追踪运行状态
  - 应用或 Docker 容器启动时自动恢复已有隧道
  - cloudflared stdout/stderr 写入独立日志文件
  - 页面内可查看单个隧道的 cloudflared 日志，并支持分页
  - 全局操作日志会显示隧道名称和 hostname，方便区分自动恢复/启动记录

- **多服务 ingress 支持**
  - 后端提供 `/api/tunnels/<id>/services`，可读取和追加同一个 Tunnel 下的 ingress 规则
  - 添加服务时会自动创建新的 DNS CNAME，并在隧道运行时重启 cloudflared 使配置生效

- **cloudflared 安装 / 更新辅助**
  - 检测本地 cloudflared 是否已安装
  - 从 GitHub Release 下载对应架构的 cloudflared
  - 检查最新版本并提示更新
  - 支持在 Web UI 中配置下载代理，仅用于 cloudflared 下载和检查更新

- **Docker / Compose 部署**
  - 提供 `Dockerfile` 和 `docker-compose.yml`
  - 使用 `network_mode: host`，便于容器访问宿主机或局域网里的目标服务
  - `./data` 挂载到容器内 `/root/.cf-tunnel-manager`，用于持久化凭证、数据库、配置和日志
  - 默认 `AUTO_START_TUNNELS=true`，容器重启后自动启动已创建的隧道

## 项目结构

```text
.
├── app.py                # Flask 后端、Cloudflare API、SQLite、cloudflared 进程控制
├── templates/
│   └── index.html        # 单页 Web UI，包含样式和前端逻辑
├── requirements.txt      # Python 依赖：Flask、PyYAML
├── Dockerfile            # Alpine 镜像构建文件，内置 cloudflared 下载
├── docker-compose.yml    # Compose 部署示例
├── start.sh              # 本地开发/直接运行脚本
├── .gitignore            # 排除运行时数据和敏感文件
└── .dockerignore         # Docker 构建上下文排除规则
```

运行时数据默认保存在：

```text
~/.cf-tunnel-manager/
├── credentials.json      # Cloudflare Account ID、API Token、域名、下载代理配置
├── tunnels.db            # SQLite 数据库
├── configs/              # 每个 tunnel 的 YAML 和 credentials JSON
├── logs/                 # cloudflared 运行日志
├── pids/                 # cloudflared PID 文件
└── bin/cloudflared       # Web UI 下载的 cloudflared 二进制
```

Docker 部署时，容器内的 `/root/.cf-tunnel-manager` 会映射到仓库目录下的 `./data`。

## 环境要求

### 直接运行

- Linux / macOS / WSL
- Python 3.10+
- `curl`
- 可访问 Cloudflare API 和 GitHub Release
- 可安装并运行 `cloudflared`

Python 依赖：

```text
flask
pyyaml
```

### Docker 运行

- Docker
- Docker Compose v2
- 构建阶段能访问 Alpine 软件源和 GitHub Release

## Cloudflare API Token 权限

需要在 Cloudflare Dashboard 创建 API Token。建议使用自定义 Token，最小权限如下：

| 权限范围 | 权限 |
| --- | --- |
| Account / Cloudflare Tunnel | Edit |
| Zone / DNS | Edit |
| Zone / Zone | Read |
| User / API Tokens | Read（用于 `/user/tokens/verify` 验证） |

同时需要填写 Cloudflare **Account ID**。

Account ID 可以在 Cloudflare Dashboard 右侧栏或账户页面中找到。

## 快速开始：Docker Compose

1. 克隆仓库：

```bash
git clone https://github.com/Sndeok/cf-tunnel-manager.git
cd cf-tunnel-manager
```

2. 构建并启动：

```bash
docker compose up -d --build
```

3. 打开 Web UI：

```text
http://127.0.0.1:5000
```

如果部署在局域网服务器上，请访问：

```text
http://服务器IP:5000
```

4. 在「凭证配置」页面填写：

- Account ID
- API Token
- 域名列表（一行一个）

保存后会自动验证 Cloudflare 凭证。

5. 在「隧道管理」页面创建隧道：

- 隧道名称：可选，不填会自动生成
- 域名：从已配置的域名列表中选择
- 子域名：例如 `nas`
- 目标服务：例如 `http://127.0.0.1:8080` 或 `http://192.168.1.10:8123`
- 协议：`HTTP/2` 或 `QUIC`

创建成功后，系统会自动：

1. 创建 Cloudflare Tunnel
2. 获取 Tunnel 凭证
3. 写入本地 cloudflared 配置
4. 创建 Cloudflare DNS CNAME
5. 保存到 SQLite 数据库

点击「启动」即可运行 cloudflared。

后续如果 Docker 容器、宿主机或应用进程重启，启动时会自动扫描已持久化的隧道并恢复运行，不需要逐个手动点击「启动」。

## 快速开始：直接运行

```bash
git clone https://github.com/Sndeok/cf-tunnel-manager.git
cd cf-tunnel-manager
./start.sh
```

或者手动安装依赖后运行：

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

默认监听：

```text
0.0.0.0:5000
```

终端会显示：

```text
Cloudflare Tunnel Manager
http://127.0.0.1:5000
```

## Docker 构建代理说明

`Dockerfile` 中默认不启用代理，也不会包含任何本地代理地址。

如果你所在网络在构建镜像时无法访问 GitHub、Cloudflare 或 Alpine 软件源，可以自行修改 `Dockerfile` 顶部的代理示例：

```dockerfile
# Optional build proxy. If your network cannot reach GitHub/Cloudflare during
# docker build, uncomment the following lines and replace the proxy address.
# ENV HTTP_PROXY=http://your-proxy-host:port \
#     HTTPS_PROXY=http://your-proxy-host:port
```

把 `your-proxy-host:port` 改成自己的代理地址，并取消注释后再构建：

```bash
docker compose build --no-cache
docker compose up -d
```

构建完成后，Dockerfile 末尾会清空运行时代理环境变量：

```dockerfile
ENV HTTP_PROXY= \
    HTTPS_PROXY=
```

这意味着代理只用于构建阶段，不会默认影响容器运行时访问局域网服务。

如果只是 Web UI 里下载或检查 cloudflared 版本需要代理，不必改 Dockerfile；可以在「凭证配置」页面启用「cloudflared 下载代理」，它只作用于后端下载/检查更新 cloudflared 的请求。

## 配置说明

### Cloudflare 凭证

在 Web UI 的「凭证配置」页面填写：

- **Account ID**：Cloudflare 账号 ID
- **API Token**：有 Tunnel 和 DNS 权限的 Token
- **域名列表**：一行一个，例如：

```text
example.com
example.net
```

保存后会写入：

```text
~/.cf-tunnel-manager/credentials.json
```

Docker 部署时对应：

```text
./data/credentials.json
```

### cloudflared 下载代理

该代理配置只用于：

- 获取 cloudflared 最新版本
- 下载/更新 cloudflared 二进制

不会用于：

- Cloudflare API 凭证验证
- Tunnel 创建/删除 API
- cloudflared 隧道运行过程

### 自动启动已有隧道

默认开启。应用启动时会：

1. 初始化 SQLite 数据库。
2. 确认 `~/.cf-tunnel-manager/bin/cloudflared` 可用。
3. 扫描数据库中已有的 tunnel 记录。
4. 跳过已经在运行的隧道。
5. 对存在 `configs/<tunnel_id>.yml` 的隧道执行 `cloudflared tunnel --config ... run <tunnel_id>`。
6. 写入新的 PID 文件，并在操作日志中记录「容器启动自动恢复隧道」。

Docker Compose 中默认配置：

```yaml
environment:
  - AUTO_START_TUNNELS=true
```

如果你想容器启动后只打开管理面板、不自动恢复隧道，可以改成：

```yaml
environment:
  - AUTO_START_TUNNELS=false
```

然后重新创建容器：

```bash
docker compose up -d --build --force-recreate
```

> 说明：Docker 会把 `./data` 挂载到 `/root/.cf-tunnel-manager`，这会覆盖镜像构建阶段放在该目录里的文件。程序启动时会自动把镜像里的 `/usr/local/bin/cloudflared` 链接到 `data/bin/cloudflared`，避免因为挂载空数据目录导致 Web UI 误判 cloudflared 未安装。

## 隧道目标服务填写示例

| 场景 | 目标服务 |
| --- | --- |
| 宿主机本地 Web 服务 | `http://127.0.0.1:8080` |
| 局域网 NAS | `http://192.168.1.20:5000` |
| Home Assistant | `http://192.168.1.30:8123` |
| HTTPS 内网服务 | `https://192.168.1.40:8443` |

Docker Compose 默认使用 `network_mode: host`，所以容器里的 cloudflared 可以直接访问宿主机和局域网地址。

> 如果你的 Docker 环境不支持 host network（例如 Docker Desktop for macOS/Windows），需要根据实际网络情况调整 `docker-compose.yml` 和目标服务地址。

## 数据持久化与备份

重要数据都在运行数据目录中：

- `credentials.json`：Cloudflare 凭证
- `tunnels.db`：隧道数据库
- `configs/*.json`：cloudflared tunnel credentials，包含 Tunnel Secret
- `configs/*.yml`：cloudflared ingress 配置
- `logs/*.log`：cloudflared 运行日志

建议定期备份：

```bash
tar -czf cf-tunnel-manager-data-backup.tar.gz data/
```

请不要把 `data/` 上传到公开仓库。

## API 概览

后端提供以下 REST API：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/config` | 获取当前配置摘要，不返回 API Token 明文 |
| `POST` | `/api/config` | 保存并验证 Cloudflare 凭证 |
| `GET` | `/api/zones` | 获取可用 Cloudflare Zone 列表 |
| `GET` | `/api/tunnels` | 获取隧道列表，并合并本地运行状态 |
| `POST` | `/api/tunnels` | 创建 Cloudflare Tunnel、DNS 记录和本地配置 |
| `DELETE` | `/api/tunnels/<id>` | 删除 Tunnel、DNS 记录和本地文件 |
| `POST` | `/api/tunnels/<id>/start` | 启动 cloudflared 进程 |
| `POST` | `/api/tunnels/<id>/stop` | 停止 cloudflared 进程 |
| `GET` | `/api/tunnels/<id>/status` | 获取运行状态和最近 cloudflared 日志 |
| `GET` | `/api/tunnels/<id>/services` | 读取 Tunnel ingress 服务列表 |
| `POST` | `/api/tunnels/<id>/services` | 追加 ingress 服务并创建对应 DNS 记录 |
| `GET` | `/api/logs` | 获取操作日志 |
| `POST` | `/api/check-update` | 检查 cloudflared 最新版本 |
| `GET` | `/api/install-cloudflared` | 检查 cloudflared 是否已安装 |
| `POST` | `/api/install-cloudflared` | 下载或更新 cloudflared |

## 安全注意事项

- Web UI 当前没有登录认证，建议只监听在可信网络内，或放在受保护的反向代理后面。
- Cloudflare API Token 和 Tunnel Secret 会保存在本地文件中，请保护 `data/` 或 `~/.cf-tunnel-manager/` 目录权限。
- 删除隧道会同时删除 Cloudflare 上的 Tunnel 和 DNS 记录，请谨慎操作。
- 不建议直接暴露该管理面板到公网。
- 建议使用最小权限 Cloudflare API Token，不要使用 Global API Key。

## 故障排查

### cloudflared 未安装

在「凭证配置」页面点击「安装 cloudflared」。如果下载失败：

1. 检查机器是否能访问 GitHub Release。
2. 在页面中启用「cloudflared 下载代理」。
3. Docker 构建阶段下载失败时，按上文修改 Dockerfile 中的构建代理示例。

### 创建隧道失败

常见原因：

- Account ID 填错
- API Token 权限不足
- API Token 已过期
- 域名不属于当前 Cloudflare 账号
- 子域名已有冲突 DNS 记录且删除失败

可以查看页面 Toast 提示，或检查后端日志。

### 隧道启动失败

检查：

- `cloudflared` 是否已安装
- `configs/<tunnel_id>.yml` 是否存在
- 目标服务地址是否能从部署机器访问
- cloudflared 日志面板里的错误信息

### 外网无法访问

检查：

1. Tunnel 是否处于运行中。
2. Cloudflare DNS CNAME 是否存在并开启代理（橙云）。
3. 目标服务是否能从运行 cloudflared 的机器访问。
4. 协议选择是否适合当前网络：HTTP/2 通常更稳定，QUIC 需要 UDP 网络正常。

## 开发说明

本项目是一个简单 Flask 应用，没有复杂构建步骤。

本地语法检查：

```bash
python3 -m py_compile app.py
bash -n start.sh
```

运行：

```bash
python3 app.py
```

Docker 构建：

```bash
docker compose build
```

## License

未声明许可证。发布或对外分发前建议补充明确的开源许可证，例如 MIT、Apache-2.0 或私有项目说明。
