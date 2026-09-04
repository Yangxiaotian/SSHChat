---
name: sshchat-feature-docs
description: >-
  Checklist for shipping user-facing SSHChat features: bilingual /help and
  server UI strings (zh+en), beginner 小白说明书, and Tab command completion in
  client.py / sshchat_gui.py. Use when adding or changing chat commands,
  /help text, locales, README command tables, or user-facing docs.
---

# SSHChat 新功能文档与补全

加任何**用户能感知**的命令或功能时，收尾必须过完本清单。只改内部逻辑、测试、部署脚本且无新命令 → 可跳过。

## 清单（按序）

```
- [ ] 1. locales 双语 /help + 运行时文案
- [ ] 2. 命令补全（终端 + GUI）
- [ ] 3. 小白说明书（若影响普通用户操作）
- [ ] 4. README 命令表（若有新顶层命令）
- [ ] 5. 欢迎行 / 入口提示（若有）
```

## 1. 双语 /help 与 UI 文案

| 内容 | 文件 |
|------|------|
| `/help` 主说明 | `locales/zh.py` → `MESSAGES["help_lines"]` |
| 同上英文 | `locales/en.py` → `MESSAGES["help_lines"]` |
| `/game help` 等子帮助 | 两边的 `game_help_lines`（仅游戏相关时） |
| 运行时提示（`_ts(conn, key)`） | 两边的 `MESSAGES["server"]`，**同一 key** |

规则：

- **zh / en 成对改**，禁止只改一侧。
- `/help` 里写清：用法一行 + 必要别名；细节可指向 `/cmd help`。
- 新 `_ts` / `i18n.t("server.xxx")` key 必须两边都有；缺 key 会露原文或空白。
- 文案风格对齐现有条目（`[*]` 前缀、简短、终端等宽友好）。

子命令很多时：主 `/help` 一行摘要 + 实现 `/cmd help`（文案仍进 locales，不要硬编码中文在 `server.py`）。

## 2. 命令补全

新**顶层**命令（如 `/poll`）必须加入：

- `client.py` → `_TOP_COMMANDS`
- `sshchat_gui.py` → `_TOP_COMMANDS`

有子命令时同时加入：

- `client.py` → `_SUBCOMMANDS_BY_CMD`（可先定义 `_POLL_SUBCOMMANDS` 一类集合）
- `sshchat_gui.py` → `_SUBCOMMANDS_BY_CMD`

嵌套子命令（如 `/game undo accept`）→ `_NESTED_SUBCOMMANDS`。

客户端底部/欢迎提示里若罗列常用命令，同步加上（如 `client.py` 的提示串、`server.py` 进房欢迎行）。

## 3. 小白说明书

面向「不会看 `/help` 的用户」时才更新；纯管理员/联邦/内部协议可跳过。

| 场景 | 中文 | 英文 |
|------|------|------|
| iSH 终端当客户端 | `小白使用说明书-iSH.md` | `docs/en/ish-beginner.md` |
| 手机原生 App | `小白使用说明书-手机App.md` | 若尚无英版，至少中文补全，并在中文里保持与 App 一致 |

只写用户要敲的最短步骤；不要把实现细节写进小白文档。新命令用「复制即可」的例子。

## 4. README 命令表

顶层命令出现在帮助里时，同步：

- `README.zh.md` 聊天命令表（若该表收录了同类命令）
- `README.md` 对应英文表（若存在）

一句话说明即可，细节仍以 `/help` 为准。

## 5. 自检

- 中英文 `/help` 都能看到新命令
- 终端输入 `/` + Tab 能补出新命令；有子命令时第二段也能补
- 需要面向小白时，说明书里有一句怎么用
- 未新增无英译的 `server.*` key

## 反例

- 只在 `server.py` 里写死中文提示
- 只改 `locales/zh.py`
- 实现了 `/foo` 却忘记 `_TOP_COMMANDS`
- 把联邦/部署细节塞进小白说明书
