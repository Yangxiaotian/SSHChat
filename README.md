# SSHChat

## Update (2026-08 iSH 服务器部署)

- **可在 iPhone/iPad 的 iSH（Alpine）上部署聊天服务器**：`deploy.sh` 自动识别 `/ish`，默认关 Cloudflare、精简依赖、OpenRC 保活。说明见 **`DEPLOY-iSH.md`**。
- 手机端只当 SSH **客户端**登录时，仍用 **`小白使用说明书-iSH.md`**。

## Update (2026-08 安全文件收发)

- **新增 `/sendfile`**：不再走 SSH 通道传文件，而是由服务端起一个 HTTP/HTTPS 页面收发。
  `/sendfile` 发到当前房间，`/sendfile 昵称` 发给某人，`/sendfile #房间` 发到指定房间。
- **文件名不用写**：以你在网页上选的文件为准。
- **密钥从不出现在网址里**：聊天里网址和密钥分两行给出，打开网页后手动输入；
  密钥通过表单提交（上传走请求头、下载走请求体），不会进浏览器历史和代理日志。
  房间内每个成员拿到的网址和密钥都不同。
- **预览和下载是两条独立的一次性链接**：输入密钥后服务器当场签发两条临时链接，
  各自只能用一次，取完文件立即失效。即使走 HTTP 明文、链接被半路截获，重放也一律失败。
- **上传和下载都只能用一次**：下载完成后该接收者的页面即作废。
  下载中途断线不算用掉，回到页面重新输密钥即可换一组新链接。
- **在线预览**：图片、视频、音频、PDF、文本等 20+ 种格式直接在网页里看，确认后再保存。
- **自动过期**：上传网址 60 分钟、下载网址 24 小时、一次性链接 10 分钟，过期文件自动删除。
- 部署参数见 **[管理员怎么做 → 1. 部署](#1-部署)**，详细说明见 `USER_GUIDE_FILE_SHARING.md`。

## Update (2026-06 联邦聊天室)

- **多服务器互联（联邦）**：多台独立部署的 SSHChat 可通过 `admin-add-peer.sh` 交换联邦公钥，组成更大的聊天网络；拓扑为图，中继洪泛后不必全互连。
- **同名用户 = 同一账号**：不同服务器上 Linux 用户名相同，视为同一人在多端登录（房间同步、不误报离线）。
- **同名房间 = 同一房间**：`#default`、`#dev` 等房间名跨节点合并；文字聊天、`/names`、`/msg` 私聊互通。
- **跨服对局**：`/game` 由开局节点同步局面，远端玩家可 `/game join`、`/game move`、`/game show` 等；节点刚连上或本地缺局面时会自动补同步。若两节点在同名房间各开一局，会用随机票选保留其中一局，并向房间广播冲突通知（落选节点的对局作废）。
- 详见下方 **[联邦网络（多服务器互联）](#联邦网络多服务器互联)**；管理员可选步骤见 **[管理员怎么做 → 4. 联邦扩网](#4-联邦扩网可选)**。

## Update (2026-05-21 UI/UX)

- 游戏工作台新增“智能行动提示卡”：
  - 自动识别：可加入、等待房主、轮到你、对方回合、上一步失败。
  - 每种状态都有下一步按钮（如“加入对局 / 开始对局 / 刷新局面 / 查看玩法”）。
- 交互降噪：
  - 默认收起“高级命令”输入区，优先按钮操作，减少误操作。
  - “局面原文”支持展开/收起，避免大段文本干扰聊天阅读。
  - 局面文本增加去噪（过滤命令提示噪声、连续重复行）。
- 深色主题可读性优化：
  - 提示卡、提示文字、局面文本提高对比度。
  - info/warn/error 三类提示增加明显边框与颜色分层。
- 统一中文文案：
  - 游戏快捷按钮、工作台动作按钮、输入区文案全部中文化。
- 房间对局逻辑说明：
  - 同一房间同一时刻仅允许一场进行中的对局。
  - 检测到房间已有对局时，工作台优先给出“加入对局”入口。

## Update (2026-05-21)

- Added new room game: `zjh` (炸金花).
- Quick start:
  - `/game new zjh`
  - `/game join`
  - host: `/game move start`
- Main actions:
  - `/game move look`
  - `/game move follow`
  - `/game move raise <amount>`
  - `/game move compare <name>`
  - `/game move fold`
  - `compare` when points are insufficient: auto all-in with remaining points
- Added aliases:
  - `zjh`
  - `zhajinhua`
- Game Workbench now includes a dedicated ZJH panel with buttons for Start/Look/Follow/Raise/Fold/Compare.
- Portable build command (Windows):
  - `cd electron && npm run build:portable`
  - output: `electron/release/VsCodeEn-portable.zip`

## Update (2026-05)

- **Monitor 功能（新增）**：左侧栏新增「M」图标，通过前置摄像头检测画面中的人数。当检测到 2 人及以上时，自动执行预设动作（最小化窗口 / 关闭应用 / 杀指定进程并最小化）。详见下方「Monitor 功能」章节。
- Electron portable startup stability improved (runtime cache isolation, single-instance focus, preload diagnostics).
- Room UX improved: join/remove in sidebar, keep room list unless manually removed, preserve per-room history when switching.
- Game Workbench quick actions now fill command into the chat composer first, then send after manual confirm/edit.
- Added `werewolf` game:
  - `/game new werewolf`
  - `/game join`
  - host starts with `/game move start`
  - night/day flow with `kill/check/save/poison/pass/vote`
- macOS build support:
  - `cd electron && npm run build:mac`
  - outputs DMG/ZIP in `electron/release/`
- GitHub safety:
  - login/server addresses remain outside repo (user profile/app data)
  - local sensitive/runtime artifacts ignored: `electron/release/`, `.runtime/`, `client-bundle.json`, build/runtime caches.

**在自己控制的 Linux 服务器上开文字聊天室。**  
用户这边**只用 SSH 登录**——和平时 `ssh 用户@服务器` 一样，不装微信、不装 Slack，**网络上看就是一条普通的 SSH 连接**。

适合想**少留一条「聊天软件」痕迹**、又愿意自己或朋友有一台服务器的人。聊天走你熟悉的密钥登录，没有单独的聊天账号密码。

多台服务器之间还可通过 **联邦** 合并成更大的聊天网络（同名用户、同名房间跨服互通），见 [联邦网络](#联邦网络多服务器互联)。

---

## 用户怎么用（最简单）

1. 向管理员要：**服务器地址**、**SSH 端口**（常见是 `22`）、以及你在服务器上的 **Linux 用户名**。
2. 确保本机 `~/.ssh` 里已有对应私钥（或管理员已把你的公钥登记好）。
3. 连接：

   ```bash
   ssh -p 端口 你的用户名@服务器地址
   ```

   连上后会进入聊天界面，不是平时那种可随便执行命令的 shell（由服务器配置决定）。

4. 发普通文字就是聊天。常用命令：`/help`、`/names` 或 `/users`、`/rooms`、`/join`、`/switch`、`/msg`、`/sendfile`、`/leave`、`/game`、`/news`、`/library`（简写 `/lib`）、`/dict`、`/clear`。终端客户端输入 `/` 后按 **Tab** 可补全命令（类似 Linux shell）。

5. 要发文件就输 `/sendfile`（发到当前房间）、`/sendfile 昵称`（发给某人）或 `/sendfile #房间`。文件不走 SSH 通道，服务器会给你一个网页地址和一个 6 位密钥（分两行给出）：在网页上输入密钥、选文件上传即可，文件名以你选的文件为准。接收方各自收到一个专属网址和密钥，图片、视频、PDF 等能直接在网页里预览。**注意文件只能下载一次**，接收方确认保存成功之前别关页面。

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

**文件收发（`/sendfile`）相关参数**，默认就已启用，并用 **Cloudflare Quick Tunnel** 给出公网 `https://*.trycloudflare.com` 链接（无需开放 8443 端口）：

| 参数 | 作用 |
|------|------|
| `--cloudflare` | 强制启用 Cloudflare 隧道（**默认已开**） |
| `--no-cloudflare` | 不用隧道：自签名 HTTPS，或配合 `--file-domain` |
| `--file-domain 域名` | 文件网页用这个域名申请 **Let's Encrypt**（会自动关掉 Cloudflare 隧道；需要 `certbot` 且 80 可达） |
| `--file-port N` | 本机文件网页端口，默认 `8443`（走 Cloudflare 时只监听本机，不必对公网开放） |
| `--no-file-https` | 本机只用 HTTP（Cloudflare 模式下本来就是 HTTP） |
| `--no-file-transfer` | 完全关闭文件收发功能 |

推荐（默认即可）：

```bash
sudo ./deploy.sh --client-ssh-host chat.example.com --client-ssh-port 22
```

有自己的域名、想用 Let's Encrypt 而不是临时 `trycloudflare.com`：

```bash
sudo ./deploy.sh --client-ssh-host chat.example.com --client-ssh-port 22 \
  --file-domain chat.example.com
```

内网自测、不要公网隧道：

```bash
sudo ./deploy.sh --client-ssh-host 10.0.0.5 --no-cloudflare
```

**在 iPhone/iPad 的 iSH（Alpine）上当服务器部署：** 详见 **[DEPLOY-iSH.md](DEPLOY-iSH.md)**。摘要：

```bash
cd ~/SSHChat
git fetch github develop && git checkout -B develop github/develop
./deploy.sh --client-ssh-host 手机局域网IP --client-ssh-port 22 \
  --no-cloudflare --no-file-https
```

`deploy.sh` 会自动识别 `/ish`：跳过 Cloudflare（无 i686 二进制）、用精简依赖（无 pymupdf，PDF 走 pypdf）、venv 带 `--system-site-packages`（复用 apk 的 `py3-lxml`）、并用 **OpenRC** 拉起 `/etc/init.d/sshchat`。首次建 venv 可能要十几分钟。手机端用户只连聊天室时看 `小白使用说明书-iSH.md`。

装完后这些值写在 `sshchat.env` 里，可直接改（改完重启服务；Cloudflare 模式下公网域名一般由 `sshchat-cloudflared` 自动改写）：

| 变量 | 说明 |
|------|------|
| `SSHCHAT_FILE_PUBLIC_HOST` | **发给用户的网址用哪个地址**。开 Cloudflare 时会自动写成 `*.trycloudflare.com`；关隧道时部署会填成 `--client-ssh-host` |
| `SSHCHAT_FILE_PUBLIC_PORT` | 外链端口；Cloudflare 下为 `443` |
| `SSHCHAT_FILE_STORAGE_DIR` | 文件存储目录；Cloudflare 默认 `/var/lib/sshchat/files`（比 `/tmp` 更耐重启） |
| `SSHCHAT_MAX_FILE_SIZE` | 单文件大小上限，默认 100MB |
| `SSHCHAT_ONE_TIME_DOWNLOAD` | 下载链接是否只能用一次，默认 `1`。设 `0` 才允许重复下载，会削弱安全性，不推荐 |
| `SSHCHAT_TICKET_TTL_SECONDS` | 预览/下载一次性链接的有效期，默认 `600`（10 分钟） |
| `SSHCHAT_MAX_PREVIEW_SIZE` | 超过这个大小就不预览、直接走下载，默认 25MB |

Quick Tunnel 无账号、**每次成功部署都会停掉旧隧道并换新的 `*.trycloudflare.com`**，同时写回 `sshchat.env` 并重启聊天服务。进程由 `sshchat-cloudflared`（Linux systemd）或 `com.sshchat.cloudflared`（macOS LaunchDaemon）保活。若被 Cloudflare 限流，可稍后执行仓库里的 `scripts/start-cloudflared-once.sh`。

### 2. 给聊天的人开账号并登记公钥

```bash
sudo /opt/sshchat/admin-add-user.sh 用户名 ssh-ed25519 AAAA... 备注可选
```

也可把公钥放在文件里，把路径交给脚本（详见脚本 `--help` 或仓库内说明）。

### 3. 防火墙

除 SSH 端口外，若聊天服务监听非本机回环，可能还需放行 **`sshchat.env` 里配置的聊天端口**（默认常见为 `12345`，与 SSH 端口不是同一个）。云厂商安全组里一并放行。

**默认走 Cloudflare 隧道时，不必放行文件端口 `8443`。** 仅在 `--no-cloudflare` 或用户要直连本机文件口时才需要：

```bash
sudo ufw allow 8443/tcp
```

若用 `--file-domain` 走 Let's Encrypt，申请证书时还要临时放行 **80** 端口：

```bash
sudo ufw allow 80/tcp   # 仅 Let's Encrypt 签发/续期需要
```

### 4. 联邦扩网（可选）

两台及以上服务器合并聊天网时，用 `admin-add-peer.sh` **按边互加**联邦公钥即可（A↔B、B↔C 即可三者互通，不必全互连）；拆除用 `admin-remove-peer.sh`。加/拆都会 **SIGHUP 热加载**并通知在线用户（加入/退出提示），**一般不必重启**。完整步骤见 **[联邦网络（多服务器互联）](#联邦网络多服务器互联)**。

**升级：** 在新版本仓库里再执行一次，**`--prefix` 与当初相同**，一般要加 **`--keep-env`** 保留你已改过的端口等配置：

```bash
sudo ./deploy.sh --prefix /opt/sshchat --keep-env
```

**重置棋类积分（管理员部署参数）：**

- 重置全部棋类持久化积分：

  ```bash
  sudo ./deploy.sh --prefix /opt/sshchat --keep-env --reset-all-ratings
  ```

- 只重置单个游戏（如国际象棋）：

  ```bash
  sudo ./deploy.sh --prefix /opt/sshchat --keep-env --reset-game-ratings chess
  ```

- 只重置单个用户的单个游戏积分：

  ```bash
  sudo ./deploy.sh --prefix /opt/sshchat --keep-env --reset-user-game-rating alice gomoku
  ```

---

## 联邦网络（多服务器互联）

多台已部署的 SSHChat 可以组成一张聊天网：同名房间合并、跨服私聊/`/names`、跨服对局，以及跨服 `/sendfile` 通知（文件仍存在发起方节点，需公网可达的文件地址）。

### 拓扑：图，不必全互连

联邦按**图**组织：每条边仍需两端互加公钥，但消息会沿邻居洪泛（带去重），私聊/对局指令按 next-hop 中继。

因此 **A↔B、B↔C 即可让 A/B/C 三者互通**，不必再让 A 与 C 直接互加。全互连只是可选的低延迟加速，不是必须。

```
  A ——— B ——— C     ✓ 连通分量内互通
  A ——— B            ✓
  A ——— C
```

### 先分清两把钥匙

| 钥匙 | 路径（默认） | 干什么 |
|------|--------------|--------|
| **联邦公钥** | `/opt/sshchat/federation/id_ed25519.pub` | **只给节点互连用**。`admin-add-peer.sh` 交换的是这个 |
| **用户登录公钥** | 各用户自己的 `~/.ssh/*.pub` | 人 SSH 进聊天用，和联邦无关 |

联邦密钥在首次部署（或首次跑 `admin-add-peer.sh`）时自动生成，**不是** root / 管理员家目录下的 `.ssh` 公钥。

### 联邦网络行为说明

- **同名用户 = 同一账号**：不同服务器上 Linux 用户名相同（如都叫 `alice`），视为同一人在多端登录；会同步房间列表，离开/加入时不会误报「离线」（只要另一端还在线）。
- **同名房间 = 同一房间**：`#default`、`#dev` 等房间名在全网共享；任一节点发出的房间消息会沿联邦图广播到连通分量内所有节点上的同名房间。
- **私聊与 /names**：`/msg alice …` 可经中继投递到联邦网络中的在线 `alice`；`/names` 会列出当前房间内包括多跳远端节点在内的昵称。
- **文件收发 `/sendfile`**：会话建在**发起方节点**，下载/上传网址用该节点的 `SSHCHAT_FILE_PUBLIC_HOST`（建议各节点都配公网可达地址，例如 Cloudflare）。对端房间成员与对端在线用户会收到下载通知；对方完全离线时，文件留言会写入各联邦节点留言箱，在任意节点登录都能收到；文件字节不经联邦链路拷贝。
  - **无 Cloudflare 的节点**（如 iSH `--no-cloudflare`）：若联邦里最近有带公网隧道的节点（`*.trycloudflare.com` 等），`/sendfile` 会自动把上传会话托管到该节点，浏览器走对方的公网 URL；托管失败则回退本机局域网地址。可用 `SSHCHAT_FILE_USE_FED_PROXY=0` 关闭。
- **离线文字留言 `/msg`**：对方全网离线时，留言同样会播种到各联邦节点，登录任一节点即可收到。
- **节点上线/下线**：某节点与本机连通或断开时，本机在线用户会收到系统提示；本机还会向邻居洪泛通报（`nodeup` / `nodedown`），连通分量内其它节点的用户同样能看到。

### 互加联邦（每条边两边都要做）

假设两台机先连成一条边（三台时再对 B–C 做同样的事即可）：

| | 服务器 A | 服务器 B |
|--|----------|----------|
| 公网地址 | `a.example.com` | `b.example.com` |
| 节点名 `node_id`（自定，两边别撞车） | `server-a` | `server-b` |

**原则：每条信任边两端各登记对方，自动热加载无需重启。** 只加一边连不上。扩展到第三台时，只需让新节点与已有连通分量中的任一节点互加，即可并入整网。

#### 0. 两边都先装好 SSHChat

```bash
# 每台各执行一次（已装过可加 --keep-env）
sudo ./deploy.sh --prefix /opt/sshchat --keep-env
```

#### 1. 各自取出「本机联邦公钥」发给对方管理员

在 **A** 上：

```bash
cat /opt/sshchat/federation/id_ed25519.pub
# 把整行发给 B 的管理员（保存成文件也可以，如 a-federation.pub）
```

在 **B** 上：

```bash
cat /opt/sshchat/federation/id_ed25519.pub
# 把整行发给 A 的管理员（如 b-federation.pub）
```

#### 2. 在 A 上登记 B

把 B 的联邦公钥放到 A 能读到的地方后执行：

```bash
sudo /opt/sshchat/admin-add-peer.sh server-b b.example.com "$(cat /path/to/b-federation.pub)"
```

含义：`server-b` = B 的 `SSHCHAT_NODE_ID`；`b.example.com` = A 能 SSH 到的 B 地址；第三个参数 = **B 的联邦公钥**。

脚本会：

- 把对方联邦公钥写入本机 `sshchat-federation` 用户的 `authorized_keys`（允许对方隧道连进来；sshd 即时生效）
- 更新 `federation/peers.json`，并触发本机热加载出站连接

#### 3. 在 B 上登记 A（对称）

```bash
sudo /opt/sshchat/admin-add-peer.sh server-a a.example.com "$(cat /path/to/a-federation.pub)"
```

#### 4. 不必重启聊天服务

`admin-add-peer.sh` 会更新 `peers.json` / `authorized_keys`，并向运行中的 `sshchat` 发送 **SIGHUP** 热加载新对端；连上后本机用户会收到「联邦节点已加入」提示（并洪泛通报邻居）。若信号未送达，服务也会在数秒内监视到 `peers.json` 变更并自动加载。

#### 拆除一条联邦边

两端各执行（`peer_node_id` 必须是对方真实 `SSHCHAT_NODE_ID`）：

```bash
# 在 A 上拆掉 B
sudo /opt/sshchat/admin-remove-peer.sh server-b

# 在 B 上拆掉 A
sudo /opt/sshchat/admin-remove-peer.sh server-a
```

脚本会从 `peers.json` 删除该节点、从联邦 `authorized_keys` 去掉对方公钥（若登记时已写入 `peer_pubkey`），并 **SIGHUP** 热加载：已建立的链路会断开，本机用户收到「联邦节点已退出」提示。不必整机重启。旧条目若没有存公钥，可把公钥作为第二参数传入，或手动编辑 `authorized_keys`。

#### 5. 防火墙（若节点间无法直连）

默认走 SSH `ssh -W` 隧道到对端本机联邦端口（`authorized_keys` 使用 `permitopen`，通常只需对方 **22** 可达）。若改用直连 TCP，再放行 **`SSHCHAT_FEDERATION_PORT`**（默认聊天端口 +1）。

#### 6. 怎么确认连上了

- 服务日志里出现 `federation: outbound connected` 或 `peer … connected`
- 两边用户进入同一房间名，一方说话另一方能看到
- `/names` 能列出对端节点上的人（含经中继可达的多跳节点）

### 脚本实际改了什么

联邦公钥在首次部署时生成，之后重复跑 `deploy.sh` **不会更换**（除非有人删掉密钥文件）。

**环境变量（`sshchat.env`）：**

| 变量 | 含义 |
|------|------|
| `SSHCHAT_NODE_ID` | 本节点唯一名（默认主机名） |
| `SSHCHAT_FEDERATION_PORT` | 联邦 TCP 端口（默认聊天端口 +1） |
| `SSHCHAT_FEDERATION_DISABLE=1` | 关闭联邦 |
| `SSHCHAT_FEDERATION_PEERS` | 可选，覆盖 `federation/peers.json` 路径 |
| `SSHCHAT_FED_PEERS_WATCH_SECONDS` | 监视 `peers.json` 的间隔（默认 `5`；`0` 关闭监视，仍可用 SIGHUP） |
| `SSHCHAT_FED_SEEN_MAX` | 洪泛去重缓存条数（默认 `4096`） |

普通用户若要在多台服务器用同一身份：在各机用 `admin-add-user.sh` 建**相同用户名**并登记各自（或同一把）登录公钥，再按上面完成联邦互信即可。

---

## 可选：图形客户端（维护者打包）

部署成功后，服务器上会有 **`client-bundle.json`**（记录用户该连哪个主机、哪个 SSH 端口）。

- 把该文件拷到本机仓库的 **`dist/client-bundle.json`**，或在打包时设置环境变量 **`SSHCHAT_BUNDLE_FILE`** 指向它。
- **Windows：** `.\scripts\build-gui-packages.ps1`  
- **Linux / macOS：** `./scripts/build-gui-packages.sh`  

生成物在 **`dist-packages/`**。最终用户仍用本机 SSH 密钥登录，程序只是少记参数。

不打包也可以：用户直接用上面的 **`ssh`** 命令，或在本机用 **`easy_connect.py`** + 简单 JSON 配置（见下文「进阶」）。

### 本次更新：Electron 客户端（VSCode 布局）

- 新增目录：`SSHChat/electron/`（与原 `server.py` 协议兼容，仍走 SSH + 端口转发）。
- 进程与外观：`name/appId/VsCodeEn.exe`、任务栏分组、深浅色主题、ActivityBar/Sidebar/Tab/StatusBar。
- 默认开启“保密模式”（Focus）：界面文案偏开发工具风格，避免显眼的“聊天/游戏”提示。
- 新增游戏工作台：在 VSCode 布局内直接执行 `/game show|seats|move`，并显示当前棋盘/对局文本。

**开发启动（Electron）**

```bash
cd electron
npm ci
npm run dev
```

若 `npm ci` 在安装 `electron` 时报 `TypeError: Invalid URL`，多半是本地的 `electron_mirror` / `ELECTRON_MIRROR` 或项目 `electron/.npmrc` 配成了无效地址；见 **`electron/NPM-ELECTRON-INSTALL.md`** 与 **`electron/.npmrc.example`**。

**Windows 免安装打包（推荐）**

```bash
cd electron
npm run build:portable
```

产物：`electron/release/VsCodeEn-portable.zip`
解压后双击：`Start-VsCodeEn.cmd`

### Monitor 功能

左侧栏点击「M」图标打开监控面板。

**使用方式：**

1. 点击「Start Monitor」启动摄像头，首次启动会加载本地 AI 检测模型（约 5 秒）。
2. 摄像头画面实时预览，系统自动检测画面中的人数并显示。
3. 设置触发动作：
   - **Minimize Window**：检测到 2 人及以上时，最小化当前窗口。
   - **Close App**：检测到 2 人及以上时，直接退出应用。
   - **Kill Processes + Minimize**：检测到 2 人及以上时，杀掉指定进程并最小化窗口。
4. 如需杀指定进程，点击「↻」加载系统进程列表，点击选择目标进程。

**安全与隐私：**

- AI 模型（COCO-SSD）打包在本地，**不发起任何网络请求**，离线可用。
- 摄像头画面仅在本地 `<video>` 元素中预览，**不写入磁盘、不上传、不缓存**。
- 人数检测在内存中完成，仅保留数字结果，**不保存任何图像数据**。
- 停止监控时，摄像头流、GPU 张量、模型资源全部释放。
- 进程名经过正则校验（仅允许字母、数字、点、下划线、连字符），**防止命令注入**。

**跨平台支持：**

- Windows / macOS 均支持摄像头访问和进程管理。
- macOS 首次使用时系统会弹出摄像头权限请求，需用户授权。

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
| `/msg 昵称 内容` | 私聊该昵称（在线即时送达；不在线则留言，对方下次上线时收到；大小写不敏感） |
| `/sendfile` | 发文件到**当前房间**，房内其他人各自收到专属下载网址和密钥 |
| `/sendfile 昵称` | 发文件给某个人（对方离线也能发，上线后收到） |
| `/sendfile #房间名` | 发文件到指定房间（你必须在该房间内） |
| `/leave` | 查看你发出、对方尚未阅读的留言 |
| `/leave 昵称` | 只看发给该昵称的未读留言（带编号） |
| `/leave 昵称 编号` | 撤回发给该昵称的第 N 条未读留言（别名：`/留言`、`/unmsg`） |
| `/announce` | 查看当前房间公告；房主可 `/announce 文字` 设置，`/announce clear` 清除 |
| `/game help` | 查看房间小游戏用法 |
| `/library` | 联邦并集书目（本机 + 对端 epub / txt / md / pdf）；书签以**藏书节点**为准，翻页自动保存 |
| `/lib` | `/library` 的简写（子命令相同，如 `/lib open 1`） |
| `/library open <序号\|文件名[@节点]>` | 打开图书（对端书按页拉取，并从藏书节点恢复书签）；`next` / `prev` / `page` 翻页 |
| `/dict en\|cn\|hh <词>` | 词典查询（见下方「词典查询」）；`/dict <词>` 自动识别 |
| `/dict help` | 词典详细用法（含 `hh` = 汉语词典） |
| `/news` | 按「中文 / 国际 / 科技」三类各显示若干条 RSS：**标题 + 提要正文**（无链接） |
| `/news 中文`、`/news 国际`、`/news 科技` | 只看某一类；可加条数，如 `/news 科技 10` |
| `/news detail 中文 2` 或 `/news 详情 中文 2` | 该分类列表 **第 2 条** 的更长 RSS 提要（非网页全文） |
| `/news fetch 中文 2` 或 `/news 全文 中文 2` | 按该条 RSS **链接**抓取网页 HTML，抽取正文（较慢；付费墙/反爬/纯 JS 页会失败） |
| `/dnd on` / `/dnd off` | 终端勿扰模式：过滤游戏刷屏，轮到你时只显示一行提示；状态会持久化 |
| `/clear` 或 `/cls` | 清屏（终端清空画面；图形客户端清空当前房间记录） |
| `/part 房间名` | 退出某个房间（至少保留一个） |

普通一行文字 = 发给当前房间里的人。

### 房间小游戏

游戏按**房间**隔离：每个房间同一时间最多一局。开局、走子、局面显示都会广播到当前房间；旁观者可以用 `/game show` 查看。

**可玩游戏一览**（`/game list`；房主可用 `/game on|off <名称>` 在本房上下线）：

| 名称 | 说明 | 常用别名 |
|------|------|----------|
| `chess` | 国际象棋 | — |
| `gomoku` | 15×15 五子棋（连珠规则） | — |
| `go` | 围棋（19×19） | `weiqi`、`baduk`、`围棋` |
| `xiangqi` | 中国象棋 | `cchess` |
| `sanguo` | 三国杀（军争版，2～6 人） | `sgs`、`三国杀` |
| `werewolf` | 狼人杀（5～12 人） | `langrensha` |
| `holdem` | 德州扑克 | `poker`、`texas`、`dezhou`、`德州` |
| `zjh` | 炸金花 | `zhajinhua`、`炸金花` |
| `niutou` | 牛头王（卡牌计分型） | `niutouwang`、`ntw`、`牛头王` |
| `mahjong` | 麻将（4 人，可补 AI） | `mj`、`majiang`、`麻将` |

**通用命令：**

- `/game list`：列出本房已上线、可玩的游戏。
- `/game new <名称>`：在当前房间开一局；发起人坐第一席（棋类：chess 白 / gomoku·go·xiangqi 黑或红先手；sanguo 为房主）。
- `/game new <名称> ai [easy\|normal\|hard]`：棋类 AI 练习局（仅 `chess` / `gomoku` / `go` / `xiangqi`）；**不计入持久化积分**。
- `/game join`：加入对局（棋类为第二席；sanguo 可 2～6 人 join 后房主 `/game move 开始`；狼人杀等多人局按提示 join）。
- `/game seats`：查看席位与对局状态。
- `/game show`：重新显示棋盘/局面（棋类第二席视角自动翻转，己方在下）。
- `/game move …`：走子或游戏内操作（各游戏语法不同，见下）。
- `/game rating [游戏] [昵称]`：查看棋类持久化积分/等级（**跨房间共享**）。
- `/game undo`：悔棋（`chess` / `gomoku` / `go` / `xiangqi`）；上一步走子方发起，对方 `/game undo accept` 同意。
- `/game pgn`：导出国际象棋 PGN（仅 `chess`）。
- `/game resign`：认负。
- `/game abort`：终止尚未开始的对局。
- `/game end`：房主强制结束当前房间对局。
- `/game on <名称>` / `/game off <名称>`：房主在本房上线/下线某游戏（进行中的该局不受影响）。

**棋类走法摘要：**

- **`chess`**：SAN（如 `e4`、`Nf3`、`O-O`）或 UCI（如 `e2e4`）。棋盘用 Unicode 棋子（♔♕♖♗♘♙ / ♚♛♜♝♞♟），空位为 `·`。
- **`gomoku`**：`/game move 行 列`，如 `/game move 8 8`。黑方禁手（长连、四四、三三）；黑方仅「恰好五连」取胜。
- **`go`**：`/game move 行 列` 落子；`/game move pass` 停一手。19×19 标准围棋规则。
- **`xiangqi`**：推荐棋谱记法 `/game move 炮二平五`、`/game move 马2进3`；也可用坐标 `/game move 8 二 8 五`。棋盘 **`+` 红子**、**`-` 黑子**、**`!` 上一步**。

**牌类 / 多人局摘要：**

- **`sanguo`（三国杀）**：房主 `/game move 开始` 开局；`/game move 武将` 查武将池；观星/蛊惑/断粮等技能见 `/game show`。
- **`werewolf`（狼人杀）**：至少 5 人；房主 `/game move start` 开始。夜晚/白天流程：`kill` / `check` / `save` / `poison` / `pass` / `vote` 等（详见 `/game show`）。
- **`holdem`（德州扑克）**：`start` 开始 \| `look` 看牌 \| `check` 过牌 \| `call` 跟注 \| `raise <额>` 加注 \| `fold` 弃牌 \| `allin` 全下；可 `bot <easy\|hard\|pro>` 加机器人。
- **`zjh`（炸金花）**：`start` \| `look` \| `follow` 跟注 \| `raise <额>` \| `compare <昵称>` 比牌 \| `fold`；比牌费用为当前单注两倍（看牌后再翻倍）；每局结束自动下一局；需机器人用 `start bot` 或 `bot add`。
- **`niutou`（牛头王）**：每回合 `pick` 选牌；若小于所有行尾须 `row 1~4` 选行吃牌；牛头越少排名越高。
- **`mahjong`（麻将）**：4 人局，人数不足时 `start` 自动补 AI；支持吃/碰/杠/胡。轮到你时 `discard <牌>`；编码 `m`=万、`p`=筒、`s`=条、`z`=字牌；也支持中文如「二万」「红中」。

**棋类积分说明：**

- `chess` 使用 **FIDE Elo** 风格积分与等级线（GM/IM/FM/CM 线等）。
- `gomoku`、`go`、`xiangqi` 使用统一 Elo 积分并映射到业余级位。
- 只有**真人对真人**对局会写入持久化积分；AI 练习局、观战不会写入。
- terminal 端可用 `/dnd on` 开启勿扰模式：读新闻/看书时过滤游戏棋盘与状态刷屏，轮到你时只显示一行 `轮到你操作`；设置写入本地客户端配置，重连后保留。需要完整局面时请手动 `/game show`（仅这类查看命令会短暂放行完整输出）；`/game move` 等走子命令不会把棋盘刷回来。再次 `/dnd on` 会立刻恢复过滤。

`gomoku`、`go`、`xiangqi` 只用 Python 标准库；`chess` 需要服务端安装 `requirements-server.txt` 里的 `chess>=1.10`。Electron 客户端左侧栏有**游戏工作台**，可按钮化执行常用 `/game` 操作。

### 词典查询

通过服务端调用有道 JSON API，在当前用户私屏显示结果（不广播到房间）。

| 命令 | 说明 |
|------|------|
| `/dict en <英文>` | **英→中**（中英文词典） |
| `/dict cn <中文>` | **中→英**（中英文词典） |
| `/dict hh <词语>` | **汉语词典**（汉→汉释义，来源《现代汉语规范词典》） |
| `/dict <词语>` | 自动识别：英文查英→中；中文同时返回中→英 + 汉语释义 |
| `/dict help` | 完整用法与别名 |

**模式别名：** `en`/`英`、`cn`/`中`/`中英`、`hh`/`汉`/`汉语`。

示例：

```text
/dict en hello
/dict cn 你好
/dict hh 学习
/dict 学习          # 自动：中→英 + 汉语释义
```

环境变量（可选）：`SSHCHAT_DICT_TIMEOUT`（超时秒数）、`SSHCHAT_DICT_PROXY`（或复用 `SSHCHAT_NEWS_PROXY` 等代理变量）、`SSHCHAT_DICT_TLS_FALLBACK=0` 关闭 TLS 校验回退。

### 图书馆

服务端目录（默认 `/opt/sshchat/library`，可用 `SSHCHAT_LIBRARY_DIR` 覆盖）放置 **epub / txt / md / pdf** 图书文件。Markdown（`.md`）按纯文本分页阅读，不渲染语法。PDF 优先用 PyMuPDF 提取正文，并过滤「逐字空格」类乱序排版，避免章节标题（如 1 → 1.1 → 1.2）顺序颠倒。**首次打开大型 PDF** 服务端需解析并缓存，约需十几秒，期间会提示「正在加载…」；再次打开同一本书会即时显示。

联邦开启时，`/library` 显示**各节点书目并集**（对端书带 `@节点`）；打开对端书时只按页拉取正文，不在联邦链路上复制整本文件。同名书可用 `/library open 书名@节点` 消歧。阅读进度（书签）保存在**该书所在节点**：在联邦 B 打开联邦 A 的书时，会从 A 恢复书签，翻页也会写回 A。

| 命令 | 说明 |
|------|------|
| `/library` 或 `/lib` | 联邦并集书目（含你的书签进度） |
| `/library open <序号\|文件名[@节点]>` | 打开图书（有书签则从藏书节点继续） |
| `/library next` / `prev` | 翻页（自动存书签到藏书节点） |
| `/library page <页码>` | 跳到指定页 |
| `/library bookmarks` | 列出我的全部书签 |
| `/library reset <序号\|文件名[@节点]>` | 清除某本书的书签（对端书同时清藏书节点） |
| `/library close` | 结束阅读（保留书签） |

Electron 客户端左侧栏「L」图标可打开**图书馆面板**，图形化浏览书目与翻页。

### 命令补全

- **SSH 终端客户端**（`client.py`）：输入以 `/` 开头的命令时按 **Tab** 补全（支持 `/library`、`/lib`、`/dnd on|off` 及常用子命令），行为类似 Linux shell。
- **Electron 图形客户端**：输入框输入 `/` 后出现命令提示，按 **Tab** 补全当前高亮项，**↑/↓** 切换候选项。

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

**`/news fetch` 报 `SSLEOFError` / 握手失败：** 多为 **代理对个别站点 HTTPS 不稳定**（RSS 能读、网页全文失败较常见）。服务端默认会 **自动重试**：放宽 TLS 校验、再 **不经代理直连**（`SSHCHAT_NEWS_PROXY_FALLBACK_DIRECT=1`，可设 `0` 关闭）。仍失败时可 **`SSHCHAT_NEWS_NO_PROXY=1`** 全程直连，或换节点/端口；并加大 **`SSHCHAT_NEWS_PAGE_TIMEOUT`**（默认 15 秒）。

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
- **文件收发是另一条通道。** `/sendfile` 走的是独立的 HTTPS 网页端口（默认 `8443`），不是 SSH。文件在服务器上落盘保存到过期为止，**管理员能看到**。别用它传你不希望管理员看到的东西。不需要就用 `--no-file-transfer` 关掉。
- **文件链路本身是按抗截获设计的。** 密钥从不出现在 URL 里；真正传输文件字节的链接是一次性的，用过即废。所以即使你为了省事用了 `--no-file-https`，中途抓包拿到的链接也无法重放。但明文下**文件内容本身**仍是可见的，介意就别关 HTTPS。

---

## 仓库里主要文件

| 文件 | 用途 |
|------|------|
| `deploy.sh` | 一键部署 |
| `DEPLOY-iSH.md` | iPhone/iPad iSH 上当**服务器**部署说明 |
| `小白使用说明书-iSH.md` | iSH 上当 **SSH 客户端**连聊天室（给小白） |
| `admin-add-user.sh` | 添加用户与公钥 |
| `admin-add-peer.sh` | 登记联邦互信节点（多服扩网） |
| `admin-remove-peer.sh` | 拆除联邦互信节点（热加载并通知用户） |
| `federation.py` / `federation-bridge.sh` | 服务器间联邦协议与 SSH 桥接 |
| `server.py` / `client.py` | 服务端与终端客户端 |
| `games.py` | 房间小游戏逻辑 |
| `file_sharing.py` / `file_http_server.py` | `/sendfile` 的传输会话管理与收发网页 |
| `library.py` / `dict_lookup.py` | 图书馆与词典查询 |
| `sshchat_gui.py` | 图形客户端源码 |
| `easy_connect.py` | 按 JSON 调用 `ssh` |
| `scripts/build-gui-packages.*` | 打 Windows / Unix 图形包 |

更细的参数（`--prefix`、`--no-systemd`、macOS 说明、密钥迁移等）见 **`deploy.sh` 头部注释**与脚本 **`--help`**。
