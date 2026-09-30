---
name: sshchat-add-command
description: >-
  Hard checklist when adding or renaming SSHChat chat slash commands: bilingual
  /help, server locale strings, and Tab/autocomplete on every client (terminal,
  Tk, Electron, Android, iOS). Use whenever adding /foo, aliases, or subcommands;
  never ship a command without help + completion.
---

# SSHChat：加命令必做 help + 补全

**触发**：新增/改名顶层命令（`/fed`、`/pad`…）、别名、或子命令。

**硬规则**：没有双语 `/help`、没有全端自动补全，就不算做完。

完整文档与小白说明书见同级 skill `sshchat-feature-docs`；本 skill 只钉住最容易漏的两项。

## 必做（缺一不可）

### 1. `/help` + 运行时文案（双语）

| 项 | 位置 |
|----|------|
| `/help` 一行 | `locales/zh.py` + `locales/en.py` → `MESSAGES["help_lines"]` |
| `_ts` 提示 | 两边 `MESSAGES["server"]` **同一 key** |
| 复杂命令 | 另做 `/cmd help`，文案仍进 locales |

禁止只在 `server.py` 写死中文。

### 2. 自动补全（每一端）

| 客户端 | 顶层 | 子命令 |
|--------|------|--------|
| `client.py` | `_TOP_COMMANDS` | `_SUBCOMMANDS_BY_CMD`（及 `_NESTED_SUBCOMMANDS`） |
| `sshchat_gui.py` | `_TOP_COMMANDS` | `_SUBCOMMANDS_BY_CMD` |
| Electron `InputBar.tsx` | `COMMAND_KEYS` | + `i18n/messages/{zh,en}.ts` 的 `input.commands.*` |
| Android `CommandCompletions.kt` | `TOP` | `SUBS` / `NESTED` |
| iOS `CommandCompletions.swift` | `top` | `subs` / `nested` |

别名（如 `/fed`=`/peers`）顶层表要**全部列出**。

### 3. 顺手同步（有则改）

- 进房欢迎命令串：`server.py` 里 `Active room…` 那行
- 终端启动提示：`client.py` 的 `Commands:` 行
- `README.zh.md` / `README.md` 命令表（新顶层命令时）

改了 `android/` 补全或功能后，按 `android-update-export` 导出升级包。

## 最短自检

```
- [ ] zh+en /help 都能看到新命令
- [ ] 终端 `/` + Tab 能补出；有子命令时第二段也能补
- [ ] Tk / Electron / Android / iOS 同样能补
- [ ] 别名也在补全表里
```

## 反例

- 只实现了 `handle_command`，忘了 locales
- 只改了 `client.py`，忘了 Android/iOS/Electron
- 加了 `/foo` 别名 `/bar`，补全里只有一个
