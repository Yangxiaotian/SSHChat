# SSHChat

在服务器上跑一个基于 **TCP** 的多房间文字聊天服务；用户通过 **SSH 公钥 + 强制命令** 登录后自动进入客户端，无需给聊天账号交互式 shell。

## 架构

- **`server.py`**：监听 `0.0.0.0`（端口见配置），按 **聊天室** 广播消息；支持 `/join`、`/users` 等命令。
- **`client.py`**：交互式输入；依赖 **prompt_toolkit**。
- **`chat.sh`**：启动客户端；会加载同目录下的 **`sshchat.env`**（若存在），并优先使用 **`venv/bin/python`**。
- **`server.sh`**：启动服务端（同样读取 `sshchat.env` 与 venv）。

默认聊天室为 **`#default`**；普通消息只发给当前房间内的连接。

## 依赖

- **Python 3**（含 `venv` 模块），部署目标建议为带 **systemd** 的 Linux。
- 客户端额外依赖：**prompt_toolkit**（由 `deploy.sh` 装入安装目录下的 venv）。

## 一键部署

在仓库目录以 **root** 执行：

```bash
sudo ./deploy.sh
```

常用参数：

```bash
sudo ./deploy.sh --prefix /opt/sshchat --server-ip 10.0.0.5 --port 12345
sudo ./deploy.sh --prefix /Shared --server-ip 192.168.1.10
sudo ./deploy.sh --no-systemd
```

部署会：

- 将程序文件复制到 `--prefix`（默认 `/opt/sshchat`）
- 创建 venv 并安装 `prompt_toolkit`
- 写入 **`sshchat.env`**（`SSHCHAT_SERVER`、`SSHCHAT_PORT`）
- 创建系统用户 **`sshchat`**（可用 `--run-user` 修改）并以该用户运行服务
- 安装并启用 **`sshchat.service`**（除非 `--no-systemd`）

若自动探测到的 `SSHCHAT_SERVER` 为 `127.0.0.1`，远程用户无法直连，请改为本机对外的 IP 或 DNS，编辑安装目录下的 `sshchat.env` 后执行：

```bash
sudo systemctl restart sshchat
```

并在防火墙中放行对应 **TCP 端口**（默认 `12345`）。

## 添加聊天用户与公钥

在**安装目录**下以 root 执行（脚本与 `chat.sh` 同目录时可自动解析强制命令路径）：

```bash
sudo /opt/sshchat/admin-add-user.sh alice /path/to/alice_ed25519.pub
cat bob.pub | sudo /opt/sshchat/admin-add-user.sh bob -
```

环境变量（可选）：

| 变量 | 含义 |
|------|------|
| `SSHCHAT_CHAT_SCRIPT` | `chat.sh` 的绝对路径（默认：脚本同目录的 `chat.sh`） |
| `SSHCHAT_SHELL` | 新建系统用户的登录 shell（默认 `/usr/sbin/nologin`） |

写入 `authorized_keys` 时会加上 `command="…/chat.sh"` 以及 `no-port-forwarding` 等限制；**未使用 `no-pty`**，以便交互式客户端正常工作。

## 使用方法（聊天内命令）

连接后进入 **`#default`**，可使用：

| 命令 | 说明 |
|------|------|
| `/users` 或 `/who` | 当前房间在线用户列表 |
| `/join <room>` | 切换到新房间（`1–32` 字符：`[a-zA-Z0-9_-]`） |
| `/help` | 简短帮助 |

普通行即为聊天内容，仅当前房间内用户可见。

## 配置说明

安装目录中的 **`sshchat.env`** 示例：

```bash
SSHCHAT_SERVER=10.0.0.5
SSHCHAT_PORT=12345
```

- **`server.py`** 读取 **`SSHCHAT_PORT`**（默认 `12345`）。
- **`client.py`** 读取 **`SSHCHAT_SERVER`**（默认 `127.0.0.1`）与 **`SSHCHAT_PORT`**。

`systemd` 单元通过 **`EnvironmentFile=-…/sshchat.env`** 注入上述变量。

## 手动运行（未用 systemd）

在安装目录：

```bash
./server.sh
```

另一终端本地测试客户端：

```bash
./chat.sh
```

开发时若无 venv，需保证当前用于运行 `client.py` 的 Python 已安装 **prompt_toolkit**，或自行 `python3 -m venv venv && ./venv/bin/pip install prompt_toolkit`。

## 安全提示

- 聊天内容未加密（仅依赖 SSH 隧道与 TCP 内网隔离时请自行评估风险）。
- 若安装目录或 `chat.sh` 对不可信用户可写，可能篡改他人登录时执行的脚本。
- 仅向可信用户分发公钥；定期审计 `authorized_keys`。

## 仓库文件一览

| 文件 | 作用 |
|------|------|
| `server.py` | 聊天服务端 |
| `client.py` | 聊天客户端 |
| `server.sh` / `chat.sh` | 启动包装脚本 |
| `deploy.sh` | 一键部署 |
| `admin-add-user.sh` | 创建系统用户并写入受限公钥 |
