# SSHChat

**在自己控制的 Linux 服务器上开文字聊天室。**  
用户这边**只用 SSH 登录**——和平时 `ssh 用户@服务器` 一样，不装微信、不装 Slack，**网络上看就是一条普通的 SSH 连接**。

适合想**少留一条「聊天软件」痕迹**、又愿意自己或朋友有一台服务器的人。聊天走你熟悉的密钥登录，没有单独的聊天账号密码。

---

## 用户怎么用（最简单）

1. 向管理员要：**服务器地址**、**SSH 端口**（常见是 `22`）、以及你在服务器上的 **Linux 用户名**。
2. 确保本机 `~/.ssh` 里已有对应私钥（或管理员已把你的公钥登记好）。
3. 连接：

   ```bash
   ssh -p 端口 你的用户名@服务器地址
   ```

   连上后会进入聊天界面，不是平时那种可随便执行命令的 shell（由服务器配置决定）。

4. 发普通文字就是聊天。常用命令：`/help`、`/users`、`/rooms`、`/join`、`/switch`、`/msg`。本项目**不提供文件传输**（避免强制命令 SSH 下路径不可用、服务端明文中继等问题）。

---

## 管理员怎么做（三步）

**环境：** 一台 **Linux**（建议有 systemd），能 **SSH 登录**，有 **sudo**。

### 1. 部署

在**本仓库目录**执行（默认装到 `/opt/sshchat`）：

```bash
sudo ./deploy.sh --client-ssh-host 你的域名或公网IP --client-ssh-port 22
```

- `--client-ssh-host`：**用户 SSH 要连的地址**（域名或 IP），会写进给小白用的配置里。  
- `--client-ssh-port`：sshd 端口，一般是 `22`。

没有域名、只有 IP，就把 IP 写在 `--client-ssh-host` 里。

### 2. 给聊天的人开账号并登记公钥

```bash
sudo /opt/sshchat/admin-add-user.sh 用户名 ssh-ed25519 AAAA... 备注可选
```

也可把公钥放在文件里，把路径交给脚本（详见脚本 `--help` 或仓库内说明）。

### 3. 防火墙

除 SSH 端口外，若聊天服务监听非本机回环，可能还需放行 **`sshchat.env` 里配置的聊天端口**（默认常见为 `12345`，与 SSH 端口不是同一个）。云厂商安全组里一并放行。

**升级：** 在新版本仓库里再执行一次，**`--prefix` 与当初相同**，一般要加 **`--keep-env`** 保留你已改过的端口等配置：

```bash
sudo ./deploy.sh --prefix /opt/sshchat --keep-env
```

---

## 可选：图形客户端（维护者打包）

部署成功后，服务器上会有 **`client-bundle.json`**（记录用户该连哪个主机、哪个 SSH 端口）。

- 把该文件拷到本机仓库的 **`dist/client-bundle.json`**，或在打包时设置环境变量 **`SSHCHAT_BUNDLE_FILE`** 指向它。
- **Windows：** `.\scripts\build-gui-packages.ps1`  
- **Linux / macOS：** `./scripts/build-gui-packages.sh`  

生成物在 **`dist-packages/`**。最终用户仍用本机 SSH 密钥登录，程序只是少记参数。

不打包也可以：用户直接用上面的 **`ssh`** 命令，或在本机用 **`easy_connect.py`** + 简单 JSON 配置（见下文「进阶」）。

---

## 聊天命令（连上以后）

| 输入 | 作用 |
|------|------|
| `/help` | 简短帮助 |
| `/users` 或 `/who` | 当前房间有谁 |
| `/rooms` | 查看你已加入的房间（`*` 表示当前活动房间） |
| `/join 房间名` | 加入房间并切换到该房间 |
| `/switch 房间名` | 在已加入房间之间切换 |
| `/msg 房间名 内容` | 不切换当前房间，直接向指定房间发消息 |
| `/part 房间名` | 退出某个房间（至少保留一个） |

普通一行文字 = 发给当前房间里的人。

---

## 进阶（可选阅读）

**本机快捷连接 JSON**（不写私钥路径，认证仍用默认 `~/.ssh`）：

- Linux / macOS：`~/.config/sshchat/client.json`
- Windows：`%APPDATA%\SSHChat\client.json`

示例：

```json
{
  "host": "chat.example.com",
  "user": "alice",
  "ssh_port": 22
}
```

然后执行：`python3 easy_connect.py`（在本机根据 JSON 调用 `ssh`，省去重复敲主机与用户名）。

**从源码跑图形界面：** 安装 Python 3、tkinter，再 `pip install -r requirements-gui.txt`，运行 `python3 sshchat_gui.py`。开发调试可用 `python3 sshchat_gui.py --full-ui` 手动填主机信息。

**配置分工（给排错用）：**

- **`sshchat.env`**（服务器安装目录）：只关**聊天服务**在本机怎么连（例如 `127.0.0.1` + 聊天端口），**不是**用户笔记本上要 ssh 的域名。
- **`client-bundle.json` / 用户侧 `client.json`**：用户 **`ssh 谁@哪台机器 -p 几端口`** 这一类信息。

---

## 安全与预期（请读这几句）

- **不是「完全隐身」。** 服务器管理员、机房或云厂商仍能看到 SSH 连接；请用**你信任的人**和**你控制或信任的机器**。
- **好处在形态：** 流量形态是常见 SSH，不依赖额外聊天协议；在典型单机部署下，聊天内容在服务器本机转发。
- 只把公钥给**信任的人**，定期检查服务器上的授权密钥。

---

## 仓库里主要文件

| 文件 | 用途 |
|------|------|
| `deploy.sh` | 一键部署 |
| `admin-add-user.sh` | 添加用户与公钥 |
| `server.py` / `client.py` | 服务端与终端客户端 |
| `sshchat_gui.py` | 图形客户端源码 |
| `easy_connect.py` | 按 JSON 调用 `ssh` |
| `scripts/build-gui-packages.*` | 打 Windows / Unix 图形包 |

更细的参数（`--prefix`、`--no-systemd`、macOS 说明、密钥迁移等）见 **`deploy.sh` 头部注释**与脚本 **`--help`**。
