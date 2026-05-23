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

2026-05-23（德州/炸金花闭环修复）
- 问题定位：
  1) `GameWorkbench` 的局面行过滤对席位行匹配不全（`#1 用户：积分...` 未被识别），导致：
     - 面板拿不到完整席位列表；
     - 炸金花“可比牌目标”会退化为房间用户列表（不含机器人）；
     - 按钮状态与真实局面不同步，表现为“按钮不生效/不可点”。
  2) `ZhaJinHuaGame` 缺少若干闭环保护：
     - 无积分玩家也可能成为行动方；
     - 比牌目标仅支持原样昵称，不支持 `#席位`；
     - 开局未检查积分耗尽状态。
  3) `HoldemGame` / `ZhaJinHuaGame` 机器人循环在“当前行动方不可行动”时可能提前中断，不利于稳定推进。

- 已修复：
  1) 前端工作台识别增强（机器人席位可见）：
     - `isLikelyGameLine` 新增匹配：
       - `#席位 用户：...` 行；
       - `- 用户 (alive/out)` 行。
  2) 炸金花面板可比牌目标增强：
     - 优先从席位中提取“存活”目标；
     - 若无存活标记则退化到席位全量，再退化到房间用户；
     - 无目标时给出明确提示文案。
  3) 炸金花后端闭环增强：
     - 新增 `_pick_next_actor_from_start()`，开局后指向首个可行动玩家；
     - 新增 `_resolve_target_name()`，支持 `compare #2` 与不区分大小写昵称；
     - `start` 前检查积分耗尽玩家并阻止开局；
     - 行动前新增“无积分不可操作”拦截；
     - 支持中文命令别名（开始/看牌/跟注/加注/弃牌/比牌/机器人）；
     - 机器人循环在当前行动方不可行动时自动 `_advance()`。
  4) 德州机器人推进增强：
     - 机器人循环遇到不可行动当前方时自动 `_advance()`，避免卡住。

- 新增后端回归测试：
  - `tests/test_poker_games_flow.py`
    - 用例1：炸金花可与机器人按昵称、按席位 `#2` 比牌；
    - 用例2：德州双人对局可从开局推进至摊牌结束，公共牌到 5 张，积分总量守恒。

- 验证结果：
  - `python -m pytest tests/test_poker_games_flow.py -q` 通过（2 passed）
  - `python -m pytest tests/test_multi_device_resume.py -q` 通过（2 passed）
  - `npx tsc -p tsconfig.json --noEmit` 通过
  - `npx tsc -p tsconfig.node.json --noEmit` 通过
  - `node electron/scripts/qa-game-panels-playwright.mjs` 验收通过：
    - `QA PASS: all game panels command interactions are functional.`
    - 重点确认炸金花 `compare R1` 指令可由按钮触发并发送。
