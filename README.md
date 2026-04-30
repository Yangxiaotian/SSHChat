# SSHChat

在服务器上跑一个基于 **TCP** 的多房间文字聊天服务；用户通过 **SSH 公钥 + 强制命令** 登录后自动进入客户端，无需给聊天账号交互式 shell。

## 架构

不要混淆 **两条链路**（地址、端口各管各的）：

| | 作用 | 典型配置 | 配置落在哪儿 |
|---|------|----------|--------------|
| **聊天数据面** | 用户 **已经 SSH 登录到同一台 Linux 服务器** 之后，在该机上跑的 **`client.py`** 用 TCP 连 **`server.py`**，多路会话在本机交换聊天内容 | **`SSHCHAT_SERVER=127.0.0.1`** + **`SSHCHAT_PORT`**（如 `12345`） | **`sshchat.env`**；`deploy.sh` 的 **`--server-ip` / `--port`** 写的是这一对（**不是**用户笔记本上要 `ssh` 的域名） |
| **用户接入面** | 用户 **从自己的电脑** 执行 **`ssh -p … 用户名@域名`**，打开 PTY，由 **`authorized_keys` 强制命令** 启动 **`chat.sh` → `client.py`** | 公网 **域名或 IP** + **sshd 端口**（常 `22`） | **`client-bundle.json`**（图形安装包内置）；`deploy.sh` 的 **`--client-ssh-host` / `--client-ssh-port`** |

- **`server.py`**：监听聊天 **TCP** 端口（`sshchat.env` 里 **`SSHCHAT_PORT`**），按房间广播。
- **`client.py`**：在 **服务器上**（SSH 会话里）运行，读 **`SSHCHAT_SERVER` / `SSHCHAT_PORT`** 连本机或内网 **`server.py`**；依赖 **prompt_toolkit**。
- **`chat.sh`**：启动 **`client.py`**；加载 **`sshchat.env`**。
- **`server.sh`**：启动 **`server.py`**。

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

**macOS：** 默认会走 **本地开发模式**（不创建 `sshchat` 系统用户、不调用 `groupadd`、不装 **systemd**），安装目录为 **root** 下宽松权限，便于直接执行 **`sudo /opt/sshchat/server.sh`** 启动服务。这里的 **`--server-ip`** 只影响 **`sshchat.env` → `SSHCHAT_SERVER`**（**本机 `client.py` 连 `server.py` 的聊天地址**），与「用户从外网 `ssh` 的域名」无关；本机一体部署时常用 **`127.0.0.1`**。若 **`server.py` 只在局域网别机** 上监听，才需要把 **`--server-ip`** 设成那台机在局域网内的可达地址。要坚持 Linux 那套逻辑可设 **`SSHCHAT_NO_MAC_ADAPT=1`**（在无 `groupadd` 的机器上会报错）。**`admin-add-user.sh`** 在 macOS 上 **不会** 自动建账号，需先在「系统设置 → 用户与群组」创建用户；并会跳过 **`sshchat-clients`** 组。

部署会：

- 将程序文件复制到 `--prefix`（默认 `/opt/sshchat`）
- 创建 venv 并安装 `prompt_toolkit`
- 写入 **`sshchat.env`**（`SSHCHAT_SERVER`、`SSHCHAT_PORT`、`SSHCHAT_ALERT_SOUND=auto`）
- 创建系统用户 **sshchat**（可用 `--run-user` 修改），并以该用户运行聊天服务（**Linux**；macOS 见上文）
- 创建系统组 **sshchat-clients**（可用环境变量 **`SSHCHAT_CLIENT_GROUP`** 改名，须与 `admin-add-user.sh` 一致；**Linux**）：聊天 SSH 用户经 `admin-add-user.sh` 加入该组后，可进入安装目录并运行 **`chat.sh`** 与 **`venv`**；**`server.py` / `server.sh`、以及 `admin-add-user.sh`** 仅 **sshchat** 或 **root** 可访问
- 安装并启用 **`sshchat.service`**（**Linux** + systemd；除非 `--no-systemd`）
- **（默认）** 扫描 Linux `/home/*`、`/root` 与 macOS `/Users/*`、`/var/root` 的 **`authorized_keys`**，把其中 **`command="…"`** 里、以安装目录 **`chat.sh`** 同名为结尾的路径**一律改成**当前 **`--prefix`** 下的 **`chat.sh`**（依赖 **perl**）；不需要再单独跑迁移脚本。若不想改密钥，加 **`--no-migrate-keys`**。匹配的文件名不同则设置环境变量 **`SSHCHAT_COMMAND_BASENAME`**（与 **`admin-add-user.sh`** 里强制命令脚本名一致）。

若 **`SSHCHAT_SERVER` 指向本机回环**（`127.0.0.1`）：**强制命令模式下**（用户先 `ssh` 上机再跑 `client.py`）是正常、推荐用法，聊天仍在本机完成。只有在 **把 `client.py` 挪到另一台机器上跑**、却仍指向 `127.0.0.1` 时才会连错——那时应把 **`SSHCHAT_SERVER`** 改成 **`server.py` 所在机** 可达的地址。需要改 `sshchat.env` 时，**Linux** 可执行：

```bash
sudo systemctl restart sshchat
```

**macOS** 无该服务时请重启 **`server.sh`** 进程或整进程重跑。

并在防火墙中放行 **聊天 TCP 端口**（默认 `12345`，即 **`SSHCHAT_PORT`**；与 **sshd** 端口无关）。

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

## 本机简易启动器（可选）

管理员完成部署与 `admin-add-user.sh` 后，用户也可以**直接**用系统自带的 **`ssh -tt -p … 用户@主机`**，与平时登录服务器相同；认证只用本机 **`~/.ssh`** 里标准私钥与 **ssh-agent**，**不**在配置里指定私钥路径（与「不乱放公钥、密钥只认默认习惯」一致）。

若希望少记参数，可用 **`easy_connect.py`**：在本机放一个 **JSON**（**仅**连接目标，**不含**密钥路径），例如路径：

- **macOS / Linux：** `~/.config/sshchat/client.json`
- **Windows：** `%APPDATA%\SSHChat\client.json`
- 或环境变量 **`SSHCHAT_CLIENT_CONFIG`** 指向任意路径

文件内容示例：

```json
{
  "host": "chat.example.com",
  "user": "alice",
  "ssh_port": 22
}
```

然后执行 **`python3 easy_connect.py`**（内部等价于对上述主机调用 **`ssh -tt`**，**不传 `-i`**）。可选字段 **`extra_ssh_options`**（字符串数组）会转成多条 **`ssh -o`**，例如首次信任主机等；**不要**用它来绕过正常的密钥与 `authorized_keys` 流程。

命令行可临时覆盖，例如：`python3 easy_connect.py --host 10.0.0.5 --user alice`。

## 图形界面客户端（推荐给小白）

仓库提供 **`sshchat_gui.py`**：底层为 **Paramiko + SSH 交互式会话**（与 `ssh user@host -p port` 等价），服务端仍是 **`command="…/chat.sh"`**，不是绕过 SSH 直连聊天端口。用户若**输错 Linux 用户名**，或本机私钥与 **`~/.ssh/authorized_keys`** 中公钥不匹配，SSH 认证会失败。

### 分发安装包（只内置「用户 SSH」域名 / 端口）

**与 `sshchat.env` 里的 `127.0.0.1` 聊天地址无关**：安装包里嵌的是 **用户从自己电脑 `ssh` 用的主机名和 sshd 端口**。

1. **部署时**由 `deploy.sh` 生成 **`$PREFIX/client-bundle.json`**，内含 **`host`**（用户 SSH 的域名或公网 IP）与 **`ssh_port`**（**sshd** 端口，默认 **22**）。**不会**写入 **`SSHCHAT_PORT`**（聊天服务端口）；聊天仍在服务器本机由 **`client.py` → `SSHCHAT_SERVER:SSHCHAT_PORT`** 完成。
2. 若部署时 **`SCRIPT_DIR`**（本仓库路径）可写，脚本会同步 **`dist/client-bundle.json`**，供维护者在各操作系统上打包。
3. 维护者在 **Windows / macOS / Linux 各自环境**执行 **`scripts/build-gui-packages.sh`**（依赖 **tkinter** 与 **PyInstaller**，见 `requirements-packaging.txt`），得到 **`dist-packages/SSHChat.exe`**、**`SSHChat.app`** 或 **`SSHChat`** 单文件可执行程序；**`client-bundle.json` 会打进包内**，最终用户**只需输入自己的 Linux 用户名**；认证与 **`ssh`** 相同，使用本机 **`~/.ssh`** 与 **agent**。

**部署示例（指定外网域名与 sshd 端口）：**

```bash
sudo ./deploy.sh --client-ssh-host chat.example.com --client-ssh-port 22
# 可选：在同一台机器上尝试自动打包（需已装 tkinter + PyInstaller，无头服务器常缺 tkinter）
# sudo ./deploy.sh --client-ssh-host chat.example.com --build-gui-packages
```

也可用环境变量（便于 CI）：**`SSHCHAT_CLIENT_SSH_HOST`**、**`SSHCHAT_CLIENT_SSH_PORT`**。

若未指定 **`--client-ssh-host`**：当 **`--server-ip`** 不是 **`127.0.0.1`** 时，**误用**该值作为 SSH 目标（仅当你把 **`--server-ip` 设成对外地址** 时还算合理）；否则用 **`detect_ip` 探测 IP**。**注意**：**`--server-ip` 默认常写入 `sshchat.env` 的聊天地址**，多数场景是 **`127.0.0.1`**，和「用户要 SSH 的域名」不是一回事；对外分发图形包时请尽量 **显式写 `--client-ssh-host`**。若 bundle 里仍是 **`127.0.0.1`**，脚本会警告外网用户无法 SSH 进来。

**打包示例（维护者工作站）：**

```bash
cd /path/to/SSHChat
# 将服务器上的 /opt/sshchat/client-bundle.json 拷到本仓库 dist/，或：
export SSHCHAT_BUNDLE_FILE=/path/to/client-bundle.json
./scripts/build-gui-packages.sh
```

Windows 请在 **Git Bash** 或 **WSL** 下运行该脚本（需 **bash** 与 **Python venv**）。未单独提供 **JAR**；若需 Java 客户端可自行用 **JSch** 等实现同等 SSH 会话。

### 源码直接运行（不打包）

1. **Python 3** 与 **tkinter**（Ubuntu/Debian：`sudo apt install python3-tk`）。
2. **`pip install -r requirements-gui.txt`**
3. **`python3 sshchat_gui.py`**

将 **`client-bundle.example.json`** 复制为仓库根目录的 **`client-bundle.json`** 可本地模拟「内置服务器」界面。开发与排障可用 **`--full-ui`** 忽略内置 bundle，编辑完整主机 / 端口表单。

**`--config`**：可选保存**用户名**（完整 UI 模式下还可保存主机 / SSH 端口）；**不**保存私钥路径。勾选 **严格校验主机密钥** 时仅信任 **`~/.ssh/known_hosts`**；不勾选时首次连接自动接受新主机密钥（内网省事，公网请权衡）。

聊天区为远端 **`client.py`** 输出（简单 ANSI 过滤）；底部 **发送** 向 SSH 会话发送一行。

## 使用方法（聊天内命令）

连接后进入 **`#default`**，可使用：

| 命令 | 说明 |
|------|------|
| `/users` 或 `/who` | 当前房间在线用户列表 |
| `/join <room>` | 切换到新房间（`1–32` 字符：`[a-zA-Z0-9_-]`） |
| `/help` | 简短帮助 |

普通行即为聊天内容，仅当前房间内用户可见。

## 配置说明

安装目录中的 **`sshchat.env`** 示例（**仅聊天 TCP**，与 **sshd / 用户 SSH 域名** 无关）：

```bash
SSHCHAT_SERVER=127.0.0.1
SSHCHAT_PORT=12345
SSHCHAT_ALERT_SOUND=auto
```

- **`server.py`** 读取 **`SSHCHAT_PORT`**（默认 `12345`）。
- **在服务器上、SSH 会话里跑的 `client.py`** 读取 **`SSHCHAT_SERVER`**（默认 `127.0.0.1`）与 **`SSHCHAT_PORT`**，连到 **本机或内网** 的 `server.py`。

用户 **`ssh user@域名 -p …`** 的目标主机与端口写在 **`client-bundle.json`**（图形包内置）或用户本机 **`client.json`**（**仅** `host` / `user` / `ssh_port` 等，**无**私钥路径），**不要**写进 `sshchat.env`。

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

**常见、较省心的用法：** `server.py` 与通过 SSH 强制命令启动的 `client.py` 跑在**同一台**类 Unix 主机上，且 **`sshchat.env` 里 `SSHCHAT_SERVER=127.0.0.1`**（默认即本机回环）。此时客户端到聊天服务走的是**本机 TCP**，内容不会像「第二条跨公网的明文连接」那样暴露给路径上的嗅探；能 SSH 进来说明已通过你配置的**公钥与强制命令**。在信任 **root**、只给可信用户发公钥的前提下，这与在个人电脑或自建小服务器上跑本地服务类似，**远程窃听聊天正文的概率很低**。

**需要多想一想的情况：** 若把 `SSHCHAT_SERVER` 指到**另一台机器**或经**不可信网段**访问聊天端口，则客户端到 `server.py` 这一段是**独立于 SSH 会话的 TCP**，默认无 TLS，应自行评估网络与防火墙。本机部署时仍建议：仅 **sshchat-clients** 组成员可进入默认安装目录并执行 `chat.sh`；**root** 与 **sshchat** 维护服务端；安装目录与 `chat.sh` **勿对不可信用户可写**（避免篡改他人登录时执行的脚本）；仅向可信用户分发公钥并**定期审计 `authorized_keys`**。

## 仓库文件一览

| 文件 | 作用 |
|------|------|
| `server.py` | 聊天服务端 |
| `client.py` | 聊天客户端 |
| `server.sh` / `chat.sh` | 启动包装脚本 |
| `deploy.sh` | 一键部署；默认同步重写各用户 **`authorized_keys`** 中的 **`command=`** |
| `admin-add-user.sh` | 创建系统用户并写入受限公钥 |
| `easy_connect.py` | 可选：按 JSON 调用 `ssh -tt`（仅 host/user/ssh_port，认证用默认 `~/.ssh`） |
| `sshchat_gui.py` / `requirements-gui.txt` | 图形界面客户端（tkinter + paramiko） |
| `requirements-packaging.txt` / `scripts/build-gui-packages.sh` | PyInstaller 打包（各 OS 分别构建） |
| `client-bundle.example.json` | 内置站点配置样例（`host` + `ssh_port`） |
| `sshchat_client_util.py` | 客户端配置路径、读写、bundle 查找（CLI / GUI 共用） |
