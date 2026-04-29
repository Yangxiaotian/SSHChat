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
- Linux 新消息提示音：若终端 BEL 被静音，可额外安装 `libcanberra-gtk3-module`（`canberra-gtk-play`）或使用 `pulseaudio-utils`（`paplay`）/`alsa-utils`（`aplay`）作为回退。

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
sudo ./deploy.sh --keep-env --no-migrate-keys   # 升级时保留 env 且不改 authorized_keys
```

**macOS：** 默认会走 **本地开发模式**（不创建 `sshchat` 系统用户、不调用 `groupadd`、不装 **systemd**），安装目录为 **root** 下宽松权限，便于直接执行 **`sudo /opt/sshchat/server.sh`** 启动服务。请用 **`--server-ip`** 或 **`ipconfig getifaddr en0`** 填好局域网地址，避免客户端只看到 `127.0.0.1`。要坚持 Linux 那套逻辑可设 **`SSHCHAT_NO_MAC_ADAPT=1`**（在无 `groupadd` 的机器上会报错）。**`admin-add-user.sh`** 在 macOS 上 **不会** 自动建账号，需先在「系统设置 → 用户与群组」创建用户；并会跳过 **`sshchat-clients`** 组。

部署会：

- 将程序文件复制到 `--prefix`（默认 `/opt/sshchat`）
- 创建 venv 并安装 `prompt_toolkit`
- 写入 **`sshchat.env`**（`SSHCHAT_SERVER`、`SSHCHAT_PORT`、`SSHCHAT_ALERT_SOUND=auto`）
- 创建系统用户 **sshchat**（可用 `--run-user` 修改），并以该用户运行聊天服务（**Linux**；macOS 见上文）
- 创建系统组 **sshchat-clients**（可用环境变量 **`SSHCHAT_CLIENT_GROUP`** 改名，须与 `admin-add-user.sh` 一致；**Linux**）：聊天 SSH 用户经 `admin-add-user.sh` 加入该组后，可进入安装目录并运行 **`chat.sh`** 与 **`venv`**；**`server.py` / `server.sh`、以及 `admin-add-user.sh`** 仅 **sshchat** 或 **root** 可访问
- 安装并启用 **`sshchat.service`**（**Linux** + systemd；除非 `--no-systemd`）
- **（默认）** 扫描 Linux `/home/*`、`/root` 与 macOS `/Users/*`、`/var/root` 的 **`authorized_keys`**，把其中 **`command="…"`** 里、以安装目录 **`chat.sh`** 同名为结尾的路径**一律改成**当前 **`--prefix`** 下的 **`chat.sh`**（依赖 **perl**）；不需要再单独跑迁移脚本。若不想改密钥，加 **`--no-migrate-keys`**。匹配的文件名不同则设置环境变量 **`SSHCHAT_COMMAND_BASENAME`**（与 **`admin-add-user.sh`** 里强制命令脚本名一致）。

若自动探测到的 `SSHCHAT_SERVER` 为 `127.0.0.1`，远程用户无法直连，请改为本机对外的 IP 或 DNS，编辑安装目录下的 `sshchat.env` 后：**Linux** 上可执行：

```bash
sudo systemctl restart sshchat
```

**macOS** 无该服务时请重启 **`server.sh`** 进程或整进程重跑。

并在防火墙中放行对应 **TCP 端口**（默认 `12345`）。

部署后安装目录为 **`750`**、属主一般为 **`sshchat:sshchat-clients`**（**Linux** 严格模式）：普通系统用户**不在** **sshchat-clients** 组则**无法**进入该目录，自然也无法读管理脚本。从旧版本升级后若聊天用户连不上，请对其再执行一次 **`admin-add-user.sh`**（公钥已存在时会跳过写入，但仍会确保加入 **sshchat-clients**），或手动 `sudo usermod -aG sshchat-clients <user>` 后让用户**重新建立 SSH 连接**。

## 升级已有安装（用户已存在时）

聊天用户的 `authorized_keys` 里是 **`command="…/chat.sh"`** 等选项；升级 **不必重做公钥**。每次 **`deploy.sh`**（默认）会把各 key 里 **`command=`** 中符合 basename 规则的路径**改到**当前 **`--prefix/chat.sh`**；不需要再单独跑迁移脚本。若从旧版（无组权限限制）迁来，请确认用户已在 **sshchat-clients** 组（再执行 `admin-add-user` 或 `usermod`，见上文「一键部署」末尾）。

在**新版本仓库目录**以 root 再执行部署，**使用与当初相同的 `--prefix`**（默认 `/opt/sshchat`）：

```bash
cd /path/to/SSHChat
sudo ./deploy.sh --prefix /opt/sshchat --keep-env
```

- **`deploy.sh` 会覆盖** 安装目录下的 `server.py`、`client.py`、`chat.sh`、`server.sh`、`admin-add-user.sh`，并**重建** **`venv`**。
- **默认**按当前 **`--prefix`** **重写**各用户 **`authorized_keys`** 里的 **`command="…/<basename>"`**（**perl**；basename 默认为 **`chat.sh`**，可用环境变量 **`SSHCHAT_COMMAND_BASENAME`**）；若不想动公钥，加 **`--no-migrate-keys`**。
- **`--keep-env`**：若 **`sshchat.env` 已存在**则**不覆盖**（保留你改过的 IP/端口）；不加则会用本次的 `--server-ip` / `--port` 或自动探测值**重写**该文件。
- 使用 **systemd** 时，部署结束会 **restart** `sshchat.service`，服务端立即用上新 `server.py`。
- 已连上的 SSH 会话里仍是旧客户端进程；用户 **断开再连** 后才会加载新 `client.py`。

若更换 **`--prefix`**，部署会把 **`command=`** 指到**新目录**下的 **`chat.sh`**（仍受 basename 规则约束）。若某行密钥格式特殊、**未被自动匹配**，请手工编辑 **`authorized_keys`** 或删掉旧行后用 **`admin-add-user.sh`** 重新登记。

## 添加聊天用户与公钥

在**安装目录**下以 root 执行（脚本与 `chat.sh` 同目录时可自动解析强制命令路径）：

```bash
# 直接粘贴整行公钥（多个词可不加引号，脚本会拼成一行）
sudo /opt/sshchat/admin-add-user.sh alice ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA… alice@laptop

# 仍可使用公钥文件路径（路径存在则读文件首行）或从 stdin 读一行
sudo /opt/sshchat/admin-add-user.sh bob /path/to/bob_ed25519.pub
cat bob.pub | sudo /opt/sshchat/admin-add-user.sh carol -
```

环境变量（可选）：

| 变量 | 含义 |
|------|------|
| `SSHCHAT_CHAT_SCRIPT` | `chat.sh` 的绝对路径（默认：脚本同目录的 `chat.sh`） |
| `SSHCHAT_SHELL` | 新建系统用户的登录 shell（默认 `/bin/sh`；需可执行 forced command） |
| `SSHCHAT_CLIENT_GROUP` | 赋予「可进安装目录、跑客户端」的补充组（默认 `sshchat-clients`，须与部署时一致） |
| `SSHCHAT_COMMAND_BASENAME` | `deploy.sh` 重写 **`command=`** 时匹配的文件名（默认与 **`chat.sh`**  basename 相同） |
| `SSHCHAT_ALERT_SOUND` | 新消息提示音后端（`auto`/`canberra`/`paplay`/`aplay`/`none`，默认 `auto`） |

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
SSHCHAT_ALERT_SOUND=auto
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
- 仅 **sshchat-clients** 组成员可进入默认安装目录并执行 `chat.sh`；**root** 与 **sshchat** 维护服务端；避免把安装目录设成全局可写。
- 若安装目录或 `chat.sh` 对不可信用户可写，可能篡改他人登录时执行的脚本。
- 仅向可信用户分发公钥；定期审计 `authorized_keys`。

## 仓库文件一览

| 文件 | 作用 |
|------|------|
| `server.py` | 聊天服务端 |
| `client.py` | 聊天客户端 |
| `server.sh` / `chat.sh` | 启动包装脚本 |
| `deploy.sh` | 一键部署；默认同步重写各用户 **`authorized_keys`** 中的 **`command=`** |
| `admin-add-user.sh` | 创建系统用户并写入受限公钥 |
