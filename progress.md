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

2026-05-23（现场截图问题定向修复）
- 用户现场截图暴露：`#席位` 行格式为 `R1:积分=...`（冒号后无空格）时，前端解析失败，导致“可比牌目标为空”。
- 修复内容：
  1) `ZjhPanel` 与 `HoldemPanel` 对席位/积分行改为强容错正则：
     - 支持 `R1:积分=993` / `R1: 积分=993` / `R1：积分=993` 等变体。
  2) `GameWorkbench.extractPlayerStats` 同步放宽正则，防止统计区遗漏。
  3) Playwright 脚本样本升级为“无空格格式”席位行，防回归。
- 复验：
  - `npx tsc -p tsconfig.json --noEmit` 通过
  - `npx tsc -p tsconfig.node.json --noEmit` 通过
  - `python -m pytest tests/test_poker_games_flow.py -q` 通过
  - `qa-game-panels-playwright.mjs` 再次通过（日志见 `electron/release/qa/qa-zjh-fix2.log`）

2026-05-23（德州/炸金花体验闭环增强）
- 用户反馈：按钮“点了没反应”、公共牌“没数据”、不同阶段可点击性不清晰。
- 本轮增强：
  1) `HoldemPanel` 增加阶段显示与流程提示：
     - 明确“翻牌前公共牌未发”为正常流程；
     - 显示当前阶段（翻牌前/翻牌/转牌/河牌）并给出下一阶段提示；
     - 显示“当前轮到你操作/当前轮到他人”提示。
  2) `HoldemPanel` / `ZjhPanel` 按阶段驱动按钮可用性：
     - 可操作按钮高亮 `ready`；
     - 不可操作按钮置灰（disabled + not-allowed）；
     - `发牌开始` 在进行中时明确禁用原因（title）。
  3) 样式层补齐：
     - `.mini-btn:disabled` 明确灰态；
     - `.mini-btn.ready` 明确高亮，解决“像能点但实际不可点”的错觉。
- 复验：
  - `npx tsc -p tsconfig.json --noEmit` 通过
  - `npx tsc -p tsconfig.node.json --noEmit` 通过
  - `python -m pytest tests/test_poker_games_flow.py -q` 通过
  - Playwright 面板验收通过（日志：`electron/release/qa/qa-holdem-ui.log`）

2026-05-25（五子棋助手策略升级）
- 需求：五子棋助手不只看一步，需具备多步预判、攻防平衡、套路化决策。
- 已完成：`electron/src/renderer/components/games/GomokuPanel.tsx`
  1) 重写策略引擎：
     - 从单步打分升级为“威胁识别 + 多步博弈搜索（alpha-beta 剪枝）”。
     - 引入动态前瞻深度：开局 2 层、中盘 3 层、后盘可 4 层。
  2) 威胁模型增强：
     - 识别连五、活四、冲四、眠三/活三、双威胁（双三/双四）并量化评分。
     - 同时评估“本手进攻价值 + 封堵价值 + 对手后续反击价值”。
  3) 套路策略增强（仅 `zouyu` 可见）：
     - 智能博弈、均衡控局、先手压迫、铁壁反击、双三诱杀、四三做杀、连环冲四、中腹运营。
     - `auto` 会按局势自动切换实战策略。
  4) 交互说明增强：
     - 显示“本手采用策略”和“前瞻预测层数”。
- 验证：
  - `npx tsc -p tsconfig.json --noEmit` 通过
  - `npx tsc -p tsconfig.node.json --noEmit` 通过
  - `node electron/scripts/qa-game-panels-playwright.mjs` 通过（见 `electron/release/qa/qa-latest.log`）

2026-06-02（新增围棋）
- 新增 `GoGame` 服务端规则：19路、黑先、白贴目6.5、提子、禁自杀、简单劫、连续两次 pass 终局数子、认输、悔棋、席位与动态棋盘广播。
- 新增围棋积分接入：`ratings.GAME_CONFIGS['go']`，支持 `/game rating go` 和终局 Elo 结算。
- 新增 Electron 围棋面板：`GoPanel.tsx`，支持可视19路棋盘、点击落子、停一手、加入/席位/认输按钮。
- 工作台接入：识别 `go/weiqi/baduk/围棋`，快捷按钮、帮助提示、面板渲染、命令工厂均已补齐。
- 验证：`python -m unittest discover -s tests -p test_go_game.py` 通过；牌类/多端/悔棋关键子集通过；`npm run build:win` 通过。
- 已知环境/历史测试说明：全量 unittest 仍受本机未安装 `python-chess` 和旧的“五子棋不广播棋盘”断言影响，非本次围棋回归。

2026-06-02（围棋 KataGo 助手接入层）
- 新增 KataGo IPC 协议与 preload/window API：`GO_KATAGO_ANALYZE` / `analyzeGoKataGo`。
- 主进程新增 `KataGoAnalysisService`：自动发现 `katago.exe`、模型文件、analysis/gtp cfg；支持 `KATAGO_PATH`、`KATAGO_MODEL`、`KATAGO_CONFIG` 环境变量；退出时清理进程。
- 围棋面板新增隐藏助手（仅 `zouyu`）：KataGo 可用时展示前3个推荐点、胜率、目差、访问数；不可用时回退内置轻量建议并明确提示原因。
- 新增 `electron/engines/katago/README.txt` 与打包资源规则，后续把 KataGo 文件放入该目录会随免安装包带出。
- 验证：`npm run build:win` 通过；`python -m unittest discover -s tests -p test_go_game.py` 通过。
