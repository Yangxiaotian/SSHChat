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

### 5. 手机休眠后服务停了

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
