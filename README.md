# Cloudflare Tunnel Manager

一个轻量级的 Cloudflare Tunnel Web 管理面板。通过 Web UI 创建、启动、停止和删除 Cloudflare Tunnel，自动维护对应的 DNS CNAME 记录。

适合将内网服务（NAS、Home Assistant、Web 面板等）通过 Cloudflare Tunnel 暴露到公网。

> ⚠️ 本项目会保存 Cloudflare API Token、Tunnel Secret、SQLite 数据库和 cloudflared 配置文件。请只部署在可信机器上，并保护好 `data/` 目录。

---

## 快速开始

### Docker Compose（推荐）

```bash
git clone https://github.com/Sndeok/cf-tunnel-manager.git
cd cf-tunnel-manager
docker compose up -d --build
```

打开 `http://127.0.0.1:5000`（局域网部署则访问 `http://服务器IP:5000`）。

### 直接运行

```bash
git clone https://github.com/Sndeok/cf-tunnel-manager.git
cd cf-tunnel-manager

# 方式一：一键启动
./start.sh

# 方式二：手动
python3 -m pip install -r requirements.txt
python3 app.py
```

默认监听 `0.0.0.0:5000`。终端会打印访问地址。

---

## 使用步骤

1. **配置凭证**：打开 Web UI，进入「凭证配置」，填写 Cloudflare Account ID、API Token、域名列表（一行一个），点击保存。
2. **创建隧道**：进入「隧道管理」，选择域名，填写子域名和目标服务地址（如 `http://127.0.0.1:8080`），点击创建。
3. **启动隧道**：隧道创建后，点击「启动」运行 cloudflared。
4. **自动恢复**：Docker 容器或宿主机重启后，已创建的隧道会自动恢复运行（可通过 `AUTO_START_TUNNELS=false` 关闭）。

---

## 前置条件

### Cloudflare API Token

需要在 [Cloudflare Dashboard](https://dash.cloudflare.com/) 创建 API Token，最小权限：

| 资源类型 | 权限范围 | 权限 |
| --- | --- | --- |
| Account | Cloudflare Tunnel | Read、Edit |
| Zone | DNS | Read、Edit |

> 创建 Tunnel 时用到 `/user/tokens/verify` 验证 Token，该接口不需要额外权限。

同时需要 **Account ID**，可在 Cloudflare Dashboard 右侧栏或「账户」页面找到。

### 运行环境

| 方式 | 要求 |
| --- | --- |
| Docker Compose | Docker、Docker Compose v2 |
| 直接运行 | Linux / macOS / WSL、Python 3.10+、`curl` |

---

## 项目结构

```text
.
├── app.py                # Flask 后端（SQLite、Cloudflare API、cloudflared 控制）
├── templates/index.html  # 单页 Web UI
├── requirements.txt      # flask、pyyaml
├── Dockerfile            # Alpine 镜像
├── docker-compose.yml    # Compose 配置
├── start.sh              # 直接运行脚本
├── .gitignore
└── .dockerignore
```

运行时数据目录：

```text
~/.cf-tunnel-manager/          # Docker 映射为 ./data
├── credentials.json           # Cloudflare 凭证（权限 600）
├── tunnels.db                 # SQLite 数据库
├── configs/<tunnel_id>.yml    # cloudflared ingress 配置
├── configs/<tunnel_id>.json   # cloudflared tunnel credentials（权限 600）
├── logs/<tunnel_id>.log       # cloudflared 运行日志
├── pids/<tunnel_id>.pid       # cloudflared PID
└── bin/cloudflared            # cloudflared 二进制
```

---

## 功能概览

- **Web UI**：深色 / 浅色 / 跟随系统主题，仪表盘、隧道管理、凭证配置、操作日志
- **Tunnel 管理**：创建 / 删除 / 启动 / 停止，自动创建 DNS CNAME
- **自动恢复**：容器或进程重启后自动启动已持久化隧道
- **多服务 ingress**：同一隧道下可添加多个 hostname → service 规则
- **cloudflared 辅助**：检测版本、一键安装/更新，支持配置下载代理
- **操作日志**：记录创建、启停、删除等操作，带隧道名称和 hostname
- **数据持久化**：SQLite + JSON 文件，挂载 `./data` 即可备份迁移

---

## 配置参考

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AUTO_START_TUNNELS` | `true` | 设为 `false` 关闭容器启动时自动恢复隧道 |

### cloudflared 下载代理

在「凭证配置」页面可启用代理，仅用于下载和检查更新 cloudflared。**不会**用于 Cloudflare API 或隧道运行。

### Docker 构建代理

如果构建镜像时无法访问 GitHub / Alpine 软件源，修改 `Dockerfile` 顶部注释掉的代理：

```dockerfile
ENV HTTP_PROXY=http://your-proxy:port \
    HTTPS_PROXY=http://your-proxy:port
```

构建完成后 `Dockerfile` 末尾会清空代理，不影响运行时。

---

## API 参考

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/config` | 获取配置摘要 |
| `POST` | `/api/config` | 保存并验证凭证 |
| `GET` | `/api/zones` | 获取域名列表 |
| `GET` | `/api/tunnels` | 获取隧道列表 |
| `POST` | `/api/tunnels` | 创建隧道 |
| `DELETE` | `/api/tunnels/<id>` | 删除隧道 |
| `POST` | `/api/tunnels/<id>/start` | 启动隧道 |
| `POST` | `/api/tunnels/<id>/stop` | 停止隧道 |
| `GET` | `/api/tunnels/<id>/status` | 运行状态 + cloudflared 日志 |
| `GET` | `/api/tunnels/<id>/services` | 读取 ingress 规则 |
| `POST` | `/api/tunnels/<id>/services` | 追加 ingress 规则 |
| `GET` | `/api/logs` | 操作日志 |
| `POST` | `/api/check-update` | 检查 cloudflared 更新 |
| `GET/POST` | `/api/install-cloudflared` | 检测安装 / 下载更新 |

---

## 故障排查

### cloudflared 未安装

在「凭证配置」页面点击「安装 cloudflared」。若下载失败：

1. 检查是否能访问 GitHub Release
2. 在页面中启用 cloudflared 下载代理
3. Docker 构建阶段失败则参考上方「Docker 构建代理」

### 创建隧道失败

- Account ID 是否正确
- API Token 权限是否足够
- 域名是否属于当前账号
- 子域名是否有冲突的 DNS 记录

### 隧道启动失败

- cloudflared 是否已安装
- `configs/<tunnel_id>.yml` 是否存在
- 目标服务是否能从部署机器访问
- 查看日志面板中的 cloudflared 错误信息

### 外网无法访问

1. Tunnel 是否运行中
2. Cloudflare DNS CNAME 是否开启代理（橙云）
3. 目标服务是否能从运行 cloudflared 的机器访问

---

## 安全

- Web UI 无登录认证，建议仅监听内网或放在反向代理后面
- Cloudflare API Token 和 Tunnel Secret 保存在本地，请保护 `data/` 目录权限
- 敏感文件已设 `0600` 权限
- 删除隧道会同时删除 Cloudflare 上的 Tunnel 和 DNS 记录
- 使用最小权限 API Token，不要使用 Global API Key

---

## 数据备份

```bash
tar -czf cf-tunnel-manager-backup.tar.gz data/
```

> 不要将 `data/` 上传到公开仓库。

---

## 开发

```bash
# 语法检查
python3 -m py_compile app.py

# 运行
python3 app.py

# Docker 构建
docker compose build
```

## License

未声明。建议补充 MIT、Apache-2.0 等开源许可证。
