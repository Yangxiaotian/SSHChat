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

4. 发普通文字就是聊天。常用命令：`/help`、`/names` 或 `/users`、`/rooms`、`/join`、`/switch`、`/msg`、`/game`、`/news`、`/clear`。本项目**不提供文件传输**（避免强制命令 SSH 下路径不可用、服务端明文中继等问题）。

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
| `/help` | 完整命令说明（多行） |
| `/names` 或 `/users` | 当前房间昵称列表 |
| `/rooms` | 查看你已加入的房间（`*` 表示当前活动房间） |
| `/join 房间名` | 加入房间并切换到该房间 |
| `/switch 房间名` | 在已加入房间之间切换 |
| `/msg #房间名 内容` | 不切换当前房间，向指定房间发一行话（`#` 开头表示房间） |
| `/msg 昵称 内容` | 私聊该昵称（大小写不敏感；同昵称多人则都会收到） |
| `/game help` | 查看房间小游戏用法 |
| `/game new chess` 或 `/game new gomoku` | 在当前房间开一局国际象棋或五子棋 |
| `/news` | 按「中文 / 国际 / 科技」三类各显示若干条 RSS：**标题 + 提要正文**（无链接） |
| `/news 中文`、`/news 国际`、`/news 科技` | 只看某一类；可加条数，如 `/news 科技 10` |
| `/news detail 中文 2` 或 `/news 详情 中文 2` | 该分类列表 **第 2 条** 的更长 RSS 提要（非网页全文） |
| `/news fetch 中文 2` 或 `/news 全文 中文 2` | 按该条 RSS **链接**抓取网页 HTML，抽取正文（较慢；付费墙/反爬/纯 JS 页会失败） |
| `/clear` 或 `/cls` | 清屏（终端清空画面；图形客户端清空当前房间记录） |
| `/part 房间名` | 退出某个房间（至少保留一个） |

普通一行文字 = 发给当前房间里的人。

### 房间小游戏

游戏按**房间**隔离：每个房间同一时间最多一局。开局、走子、棋盘显示都会广播到当前房间；旁观者可以用 `/game show` 看棋盘。

常用命令：

- `/game list`：列出可玩游戏，目前有 `chess` 和 `gomoku`。
- `/game new chess`：开国际象棋；发起人执白，另一人用 `/game join` 加入后执黑。走法支持 SAN（如 `e4`、`Nf3`、`O-O`）或 UCI（如 `e2e4`）。
- `/game new gomoku`：开 15×15 五子棋；发起人执黑先手。走法是 `/game move 行 列`，例如 `/game move 8 8` 或 `/game move 8,8`。
- `/game join`：加入空席位。
- `/game seats`：查看双方与对局状态。
- `/game show`：重新显示棋盘。
- `/game move ...`：走子。
- `/game pgn`：导出国际象棋 PGN（仅 `chess`）。
- `/game resign`：认负。
- `/game abort`：终止尚未开始的对局。
- `/game end`：房主强制结束当前房间对局。

`gomoku` 只用 Python 标准库；`chess` 需要服务端安装 `requirements-server.txt` 里的 `chess>=1.10`。如果没装，服务端仍能启动，但 `/game new chess` 会提示缺依赖。

### RSS 新闻

`/news` 从服务端抓取 RSS，把 **标题** 和 **提要/正文片段**（来自 `description`、`summary`、`content` 等字段，去掉 HTML 标签）发给当前用户，**不展示链接**，也不会广播到房间。

可用分类：

- 中文新闻：`/news 中文`，别名 `cn`、`zh`
- 国际新闻：`/news 国际`，别名 `world`、`intl`
- 科技新闻：`/news 科技`，别名 `tech`
- 全部分类：`/news all`

**看某一条的「详情」**（更长提要，仍来自 RSS，不是网页全文）：先用 `/news 中文`（或 `国际` / `科技`）看带 **1、2、3…** 的列表，再发 **`/news detail 中文 2`** 或 **`/news 详情 中文 2`** 表示看中文类第 2 条。默认单条详情最多约 4000 字，可用环境变量 `SSHCHAT_NEWS_DETAIL_CHARS` 调整。

**看网页里抽出来的正文（粗抓取）**：同一列表序号下，发 **`/news fetch 中文 2`** 或 **`/news 全文 中文 2`**。服务端用 RSS 里的 `http(s)` 链接下载 HTML，去掉脚本/标签后尽量取 `<article>` / `<main>` / `<body>` 里的文字；**不是**官方接口，**不能保证**与浏览器里全文一致（付费墙、登录、强反爬、仅前端渲染的站点常会失败）。抓取超时、体积与展示长度可通过 `SSHCHAT_NEWS_PAGE_TIMEOUT`、`SSHCHAT_NEWS_PAGE_MAX_BYTES`、`SSHCHAT_NEWS_PAGE_TEXT_CHARS` 调整；同一 URL 有短期缓存（`SSHCHAT_NEWS_PAGE_CACHE_SECONDS`）。

默认 `/news` 每类显示 3 条；查看单类默认 8 条；可以加条数，例如 `/news 国际 10`。服务端最多显示 15 条，并默认缓存 10 分钟，避免频繁请求外部 RSS。

若 **「中文」类几乎空白**（标题都很少），多半是 **跑 `server.py` 的那台机器出不了网**（防火墙、无默认路由）、**访问境外 RSS 被拦**（常见于境内机房直连 BBC/NYT 等），或 **个别源超时**。

**代理说明：** 新闻在 **运行聊天服务的那台机器** 上抓取。代码里 **默认使用 `http://127.0.0.1:7897`** 作为 HTTP(S) 代理（适合本机已开 Clash / sing-box 且 mixed 端口为 7897）。若你的代理端口不同，可设 **`SSHCHAT_NEWS_PROXY_DEFAULT`**（例如 `http://127.0.0.1:7890`）或 **`SSHCHAT_NEWS_PROXY`** 覆盖。不需要代理时，在 `sshchat.env` 里设 **`SSHCHAT_NEWS_NO_PROXY=1`** 后重启服务。若服务在 **远程 Linux** 上，`127.0.0.1` 是 **服务器自己**，不是你笔记本；要让远端走你本机出口，需 **SSH 反向转发**（例如 `ssh -R 7897:127.0.0.1:7897 ...`）再把 `SSHCHAT_NEWS_PROXY` 指到转发端口，或直接在远端填可达的代理地址。也可用 **`SSHCHAT_NEWS_PROXY`**、`HTTPS_PROXY` 等覆盖默认端口（见 `server.py` 中 `_news_proxy_url`）。改环境后需 **重启聊天服务**；新闻有缓存，最多约 10 分钟才刷新。

内置来源包括 BBC 中文、纽约时报中文网、美国之音中文、RFI 华语、德国之声中文、BBC World、Al Jazeera、NPR、The Guardian、Ars Technica、The Verge、Wired、Hacker News。可通过环境变量调整：`SSHCHAT_NEWS_TIMEOUT`、`SSHCHAT_NEWS_CACHE_SECONDS`、`SSHCHAT_NEWS_BODY_CHARS`（单条提要最大字符数）、`SSHCHAT_NEWS_WRAP`（自动换行宽度），以及网页抓取相关变量见上一段。

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
