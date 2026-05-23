Original prompt: [$develop-web-game](C:\Users\dell\.codex\skills\develop-web-game\SKILL.md) Web 游戏迭代（实现 + 测试循环）。当前所有游戏的面板交互开发。[$playwright](C:\Users\dell\.codex\skills\playwright\SKILL.md) 验收

2026-05-23
- 初始化 develop-web-game 工作流。
- 当前目标：全游戏面板交互补齐 + Playwright 验收。
- 已完成：读取 skill 文档；完成面板审查，发现三国杀/狼人杀状态门控不足，棋类回合门控可增强，德州/炸金花中英状态解析可增强。
- 待办：
  1) 修复并增强各面板状态解析和按钮启停逻辑。
  2) 构建并运行自动化 Playwright 验收脚本。
  3) 回归检查并更新结果。

2026-05-23（更新）
- 已完成：
  1) 全游戏面板交互补齐：
     - `GomokuPanel` 重写：修复白屏风险、坐标解析、落子直显、回合门控、隐藏助手（仅 `zouyu` 可见）与策略建议。
     - `ChessPanel` / `XiangqiPanel`：回合门控、禁用提示、取消选中。
     - `SanguoPanel` / `WerewolfPanel`：状态驱动按钮启停（waiting/playing/night/day/ended）、目标选择与可执行命令约束。
     - `HoldemPanel` / `ZjhPanel` / `NiuTouPanel`：中英状态解析增强、房主/回合门控、交互输入样式统一。
  2) 工作台解析增强：
     - `GameWorkbench.isLikelyGameLine` 增加对 `#席位行`、`轮到`、`alive/players/votes` 行识别，避免关键状态被过滤导致按钮误禁用。
  3) 样式与稳定性：
     - 新增 `.game-mini-input` 样式。
     - `App.tsx` 异常边界中文文案与编码修复。
  4) Playwright 验收：
     - 新增 `electron/scripts/qa-game-panels-playwright.mjs`。
     - 覆盖 8 个游戏面板交互点击 -> 指令发送验证（五子棋/国际象棋/中国象棋/三国杀/狼人杀/德州/炸金花/牛头王）。
     - 验收结果：`QA PASS: all game panels command interactions are functional.`
- 编译检查：
  - `npx tsc -p tsconfig.json --noEmit` 通过
  - `npx tsc -p tsconfig.node.json --noEmit` 通过
