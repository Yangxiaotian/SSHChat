# 在 iSH 上部署 SSHChat 服务器

English version: [docs/en/deploy-ish.md](docs/en/deploy-ish.md)

把 iPhone / iPad 上的 **iSH**（Alpine Linux 用户态）当作聊天服务器主机。  
手机端**只当 SSH 客户端**连别的服务器时，请看 `小白使用说明书-iSH.md`，不必看本文。

---

## 环境特点（必读）

| 项 | 说明 |
|----|------|
| 系统 | Alpine（iSH 自带），架构多为 **i686** |
| Python | 常见为 **3.9**（已用 `from __future__ import annotations` 兼容） |
| 进程管理 | **OpenRC**（无 systemd） |
| Cloudflare | **默认关闭**：官方 `cloudflared` 无 i686 包 |
| PDF 库 | **不装 pymupdf**；图书馆 PDF 走 `pypdf` |
| 依赖 | venv 使用 `--system-site-packages`，复用 apk 的 `py3-lxml`（避免源码编译） |
| 耗时 | 首次建 venv / pip 可能要 **十几分钟**，属正常；再次部署若依赖已齐会复用 venv |

局域网文件页默认形如：`http://手机IP:8443`（需 `--no-cloudflare`，一般已自动关闭）。

---

## 准备

1. App Store 安装 **iSH**，打开并完成 Alpine 初始化。  
2. 在 iSH 里安装基础工具（只需一次）：

```bash
apk update
apk add openssh openssh-server git bash python3 py3-pip py3-lxml
```

3. 启动并配置 `sshd`（若要用别的电脑 SSH 进这台 iSH 做维护）：

```bash
rc-update add sshd default
rc-service sshd start
# 按需设置 root 密码或写入 authorized_keys
```

4. 记下手机局域网 IP（示例：`10.147.17.226`），下文用它作为 `--client-ssh-host`。

---

## 获取代码并部署

```bash
cd ~
git clone git@github.com:Yangxiaotian/SSHChat.git   # 或已有仓库则跳过
cd SSHChat
git fetch github develop
git checkout -B develop github/develop             # 或 master / 指定 tag

# 推荐：关 Cloudflare + 文件用 HTTP（局域网）
./deploy.sh --client-ssh-host 10.147.17.226 --client-ssh-port 22 \
  --no-cloudflare --no-file-https
```

把 `10.147.17.226` / `22` 换成你的实际 IP 与 sshd 端口。

脚本检测到 `/ish`（或 apk 源含 `apk.ish.app`）时会自动：

- 关闭 Cloudflare Quick Tunnel  
- 使用 `requirements-server-ish.txt`（无 pymupdf）  
- 默认清华 PyPI 镜像、跳过 pip 自升级  
- 用 apk 装 `py3-lxml`（venv `--system-site-packages`）；`apk.ish.app` 失败时自动改官方 CDN / 清华镜像并重试，仍失败则**立刻退出**（避免空跑 pip）  
- 安装 / 启动 OpenRC 单元 `/etc/init.d/sshchat`  
- 服务用户一般为 `sshchat`（Alpine 上主组常为 `nogroup`，脚本已按主组 chown）

升级（代码已更新后）：

```bash
cd ~/SSHChat
git fetch github develop && git reset --hard github/develop
./deploy.sh --client-ssh-host 10.147.17.226 --client-ssh-port 22 \
  --no-cloudflare --no-file-https --keep-env
```

---

## 部署成功怎么确认

```bash
rc-service sshchat status          # 应为 started
# 或
pgrep -af '/opt/sshchat/server.py'

# 本机探测聊天端口（默认 12345）
python3 -c "import socket; s=socket.create_connection(('127.0.0.1',12345),3); print('tcp-ok'); s.close()"

tail -20 /opt/sshchat/server.log
# 期望看到类似：chat server started on port 12345
```

常用路径：

| 路径 | 用途 |
|------|------|
| `/opt/sshchat` | 安装前缀 |
| `/opt/sshchat/sshchat.env` | 运行配置 |
| `/opt/sshchat/server.log` | 服务日志 |
| `/etc/init.d/sshchat` | OpenRC 脚本 |

启停：

```bash
rc-service sshchat start
rc-service sshchat stop
rc-service sshchat restart
```

若 OpenRC 因依赖起不来，`deploy.sh` 会 **nohup 回退** 启动；修好依赖后可再 `rc-service sshchat restart`。

---

## 加聊天用户

与普通 Linux 相同，在 iSH（root）上：

```bash
/opt/sshchat/admin-add-user.sh 用户名 'ssh-ed25519 AAAA... 备注'
```

用户从另一台设备 / 另一台 iSH 登录：

```bash
ssh -p 22 用户名@10.147.17.226
```

（端口以你 sshd 为准。）给完全不懂终端的人，可再发 `小白使用说明书-iSH.md`，把地址/端口/用户名填好。

---

## 文件收发（`/sendfile`）

- iSH 上默认 **无** Cloudflare；文件页用局域网 HTTP。  
- `SSHCHAT_FILE_PUBLIC_HOST` 一般为 `--client-ssh-host`（手机 IP）。  
- 接收方需能访问该 IP 的 `8443`（或你改过的 `--file-port`）。  
- 仅同一 Wi‑Fi / VPN / Tailscale 等可达网络内使用较稳妥。  
- **联邦公网代理：** 若本机已与一台开了 Cloudflare 的联邦节点连通，`/sendfile` 会优先把上传/下载网址托管到该节点的 `*.trycloudflare.com`，手机浏览器可从外网打开；失败时仍回退局域网地址。

---

## 常见问题

### 1. `chown: unknown user/group sshchat:sshchat`

旧脚本在 Alpine 上会踩坑（`adduser -S` 常进 `nogroup`）。请使用含 iSH 适配的版本（`v1.1.4` 及以后）：按用户**主组** chown。

### 2. `TypeError: unsupported operand type(s) for |`

Python 3.9 不支持运行时求值 `str | None`。请更新到已加 `from __future__ import annotations` 的版本。

### 3. OpenRC：`cannot start sshchat as networking would not start`

iSH 上 `networking` 服务常坏。新版 OpenRC 单元用软依赖 `use net`，不应再硬依赖。仍失败时看 `/opt/sshchat/server.log`，或手动：

```bash
nohup /opt/sshchat/server.sh >>/opt/sshchat/server.log 2>&1 &
```

### 4. pip / venv 极慢或卡在编译 lxml

不要手装会编译的二进制依赖。确保：

```bash
apk add py3-lxml
```

并用仓库自带的 `deploy.sh`（会 `--system-site-packages` + ebooklib `--no-deps`）。

### 5. 卡在 `fetch http://apk.ish.app/.../APKINDEX.tar.gz`

这是 iSH 自带源 `apk.ish.app` 常见故障（源站慢/不可达），与 SSHChat 无关。新版 `deploy.sh` 会：已装好 `py3-lxml`/`pip` 时跳过 `apk add`；失败时自动改写 `/etc/apk/repositories`（备份为 `repositories.sshchat.bak`）并试官方 CDN / 清华镜像。若自动换源仍失败，再手工处理：

**立刻处理：**

1. `Ctrl+C` 中断当前 `deploy.sh`（若卡在旧版脚本里）  
2. 若以前装过依赖，直接再跑（会跳过 apk）：

```bash
./deploy.sh --keep-env
```

3. 若缺包且自动换源未成功，先换可用 Alpine 源再装（App Store 版建议留在 **v3.14**，勿盲目升到过新版本以免 Bad system call）：

```bash
# 备份
cp /etc/apk/repositories /etc/apk/repositories.bak

# 方案 A：仍用同版本，换官方 CDN（常比 apk.ish.app 通）
printf '%s\n' \
  'https://dl-cdn.alpinelinux.org/alpine/v3.14/main' \
  'https://dl-cdn.alpinelinux.org/alpine/v3.14/community' \
  >/etc/apk/repositories

apk update
apk add py3-lxml py3-pip
```

若官方 CDN 也慢，可试国内镜像（版本仍用 v3.14）：

```bash
printf '%s\n' \
  'https://mirrors.tuna.tsinghua.edu.cn/alpine/v3.14/main' \
  'https://mirrors.tuna.tsinghua.edu.cn/alpine/v3.14/community' \
  >/etc/apk/repositories
apk update && apk add py3-lxml py3-pip
```

说明：只要目录 `/ish` 还在，iSH 重启后可能改回 `apk.ish.app`。需要长期固定源时可按 [iSH wiki](https://github.com/ish-app/ish/wiki/Using-Alpine-Linux-repositories) 处理 `/ish`（有风险，先备份）。

装好后再：

```bash
./deploy.sh --keep-env
```

### 6. 部署后 `chat.sh: Permission denied` / 公钥能登但进不了聊天

iSH 对**附加组**权限经常不生效：即使用户已在 `sshchat-clients`，`750`/`640` 仍会拒绝。

当前 `deploy.sh` 在 iSH 上会自动把客户端入口改为：

- `/opt/sshchat`、`chat.sh`、`venv`：`o+rX`（如 `755`）
- `client.py` / `sshchat_client_util.py` / `sshchat.env`：`644`

若你跑的是旧脚本，可临时手动修：

```bash
chmod 755 /opt/sshchat /opt/sshchat/chat.sh
chmod 644 /opt/sshchat/client.py /opt/sshchat/sshchat_client_util.py /opt/sshchat/sshchat.env
chmod -R a+rX /opt/sshchat/venv
```

服务端文件（`server.py` 等）保持 `600` 即可。

### 7. 手机休眠后服务停了

iSH 在后台可能被系统挂起。保持 iSH 前台、接电，或按需重开 App 后再 `rc-service sshchat start`。这是 iOS 限制，不是 SSHChat 独有。

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `deploy.sh` | 一键部署（含 `is_ish` 自动适配） |
| `requirements-server-ish.txt` | iSH 精简 pip 依赖 |
| `scripts/sshchat.openrc` | OpenRC 单元模板 |
| `小白使用说明书-iSH.md` | **客户端**登录聊天室（给小白） |
| `README.md` | 总览与管理员三步 |
