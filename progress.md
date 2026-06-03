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

2026-06-02（Rapfi 耗时调度优化）
- 问题定位：31545ms 来自前端在 12 线程机器中后盘给 Rapfi 的 31500ms 高强度预算，日志确认 queueMs=0、runMs=31545，不是排队或卡死。
- 已优化：
  1) 正式 Rapfi move 请求只在确认为本方回合时发；没有明确 turnInfo 时按棋盘黑白子数量兜底判断。
  2) 非本方回合会作废未完成的正式请求，避免旧局面结果覆盖新局面。
  3) 命中后台 ponder 缓存时直接复用建议并标记完成，不再立刻重复发高时限正式请求。
  4) 后台 ponder 不再与正式分析并发，且任意 ponder 在飞时不继续排新 ponder，降低 CPU 抢占和排队。
  5) 主进程增加保险：非本方回合的正式分析结果不写入 Rapfi 连续局面状态，避免增量 TURN 状态被污染。
- 验证：
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
- 预期效果：减少 not-my-turn-or-unknown 正式请求、提升 ponder 命中率、降低 31 秒整盘重算出现频率；保留 Rapfi 高强度预算本身。

2026-06-02（围棋 KataGo 调度优化）
- 问题定位：KataGo 强度本身足够，但首次 OpenCL autotuning 可能超过 75s；旧调度会在非本方回合也分析、失败后同局面永久不重试，容易表现为卡/回退。
- 已优化：
  1) 围棋助手只在隐藏用户 zouyu 入座且轮到自己时触发正式 KataGo 分析；无 turnInfo 时按黑白子数量兜底判断回合。
  2) 非本方回合作废旧请求并清空旧 KataGo 结果，避免过期建议继续高亮。
  3) 增加局面缓存，同一棋盘/执子命中后直接复用建议，减少重复调用。
  4) 失败策略从“永久失败”改为 15s 冷却，首次调优超时后允许后续重试。
  5) 首次 KataGo 请求允许 180s 保护窗口，后续恢复 60s；中后盘 visits 从 96 动态提升到 128/160。
  6) 主进程 KataGo timeout clamp 从 60000ms 放宽到 180000ms，确保首次预热请求不会被主进程提前截断。
- 验证：
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - python -m unittest discover -s tests -p test_go_game.py 通过（5 passed）

2026-06-02（五子棋 Rapfi 防守兜底修复）
- 用户反馈：对手已连三/有明显威胁时，Rapfi 模式仍首选己方进攻点，表现为不防守。
- 诊断：rapfi-move-trace.log 显示 req#10 走 incremental-turn，mySide=-1，runMs=59，返回 UI 坐标 13,8；说明主进程确实拿到了 Rapfi 快速建议，但展示层没有强制防守兜底。
- 已修复：
  1) 新增 urgentDefenseSeverity / urgentDefenseLabel / findUrgentDefenseMove。
  2) 如果我方无一步胜，而对手存在下一手连五、活四/冲四、跳三转四、双三等强制威胁，强制把防守点提升为当前首选。
  3) Rapfi 模式下若 Rapfi 原始建议没覆盖强制防点，展示层直接用防守兜底建议覆盖，避免“引擎短算忽略威胁”。
  4) 若 Rapfi 建议正好覆盖防点，则继续显示为紧急防守。
- 验证：electron tsc renderer/main 检查通过；清理了本轮诊断产生的 analysis_logs 临时目录。

2026-06-02（五子棋终局棋盘禁止继续 Rapfi 分析）
- 用户指出：黑方标红区域已连五，Rapfi 仍返回 13,8；该局面应是终局，不应继续给下一手。
- 已修复：
  1) 前端 GomokuPanel 新增 winnerOnBoard：黑方按连珠规则“恰好五连”胜，白方五连及以上胜。
  2) 若当前棋盘已有胜方，Rapfi effect 会取消请求并提示“已连五，当前局面应已结束，不再请求 Rapfi”。
  3) shownSuggestions 在终局时显示“终局检测”，不再渲染可点击建议按钮，避免 0,0 或错误下一手。
  4) 主进程 Rapfi 服务新增 gomokuWinnerOnBoard 兜底；任何终局棋盘都会直接返回错误并清理增量状态，不会发送给 Rapfi。
- 验证：
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - python -m pytest tests/test_gomoku_renju.py -q 通过（13 passed）

2026-06-02（围棋助手建议点合法性过滤）
- 用户反馈：KataGo 建议1 的点已经被吃/已占用，但 UI 仍提示落子。
- 根因：GoPanel 的 shownMoves 直接使用 katagoMoves 或缓存结果，没有按当前 matrix 再过滤；棋盘变化/提子/刷新后旧建议可能已不可落子。
- 已修复：
  1) 新增 isLegalEmptyPoint / filterPlayableMoves。
  2) shownMoves 只使用当前棋盘仍为空的 KataGo 建议，否则回退内置建议。
  3) 缓存命中时二次校验；如果缓存建议全部不可落子，则删除缓存并允许重新分析。
  4) KataGo 返回建议后先取前6再过滤空点，最终只展示可落子的前3个。
  5) 状态文案改为以 playableKataGoMoves 为准，避免“已接入 KataGo”但实际展示的是回退建议。
- 验证：
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - python -m unittest discover -s tests -p test_go_game.py 通过（5 passed）

2026-06-02 15:06（棋盘助手扫尾）
- 追加发现：五子棋盘面已经终局时，前端仍可能保持空点可点击，容易造成终局后误点或“按钮没反应”的体验问题。
- 已修复：
  1) GomokuPanel 根据当前可视棋盘计算 boardWinner。
  2) boardWinner 存在时直接禁用棋盘落子和建议按钮。
  3) Rapfi 状态改为显示“终局：黑方/白方已连五”，不再表现成失败回退。
- 验证：
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - python -m pytest tests/test_gomoku_renju.py -q 通过（13 passed）
  - python -m unittest discover -s tests -p test_go_game.py 通过（5 passed）

2026-06-02 15:28（全游戏面板 QA 扫描）
- 按 develop-web-game 流程跑 electron/scripts/qa-game-panels-playwright.mjs，发现并修复：
  1) 中国象棋面板标题没有连续“中国象棋棋盘”，导致自动化和产品识别不一致；已改为“中国象棋棋盘（真实棋盘，先点起点再点终点）”。
  2) 中国象棋格点缺少统一 .xiangqi-cell class，自动化无法稳定选择格点；已补 class，不改变视觉。
  3) QA 脚本原先给中国象棋喂空局面，却要求走子；已改成带红黑席位和 10 行真实棋盘的样本。
  4) 中国象棋解析器原先纯按顺序落行，遇到重复空行/行号局面容易坐标漂移；已支持“有行号按行号，无行号按顺序”。
  5) 工作台没有把“红：zouyu 黑：R1”识别为游戏行，导致席位判断偏差；已纳入游戏行识别。
  6) 中国象棋面板改用 cleanBoard 输入，避免噪声和重复行影响可视棋盘解析。
- 同轮发现并修复：
  1) 每房间消息上限仍是 500，和此前要求 1200 不一致；已改为 1200。
  2) 游戏工作台仍解析全量消息；已收紧为最近 360 行，降低旧局面干扰和卡顿风险。
  3) 炸金花加注只判断非空；已改为必须是大于 0 的数字。
  4) 炸金花全局禁用时仍可切换比牌目标；已禁用目标按钮。
- 验证：
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - node electron/scripts/qa-game-panels-playwright.mjs 通过（覆盖五子棋/国际象棋/中国象棋/三国杀/狼人杀/德州/炸金花/牛头王）
  - python -m pytest tests/test_gomoku_renju.py -q 通过（13 passed）
  - python -m unittest discover -s tests -p test_go_game.py 通过（5 passed）
  - python -m unittest discover -s tests -p test_poker_games_flow.py 通过（6 passed）

2026-06-02（KataGo 围棋助手可用性修复）
- 用户反馈：围棋助手一直显示分析中，且建议不看对手布局。
- 真实验证：直接启动 electron/engines/katago 的 KataGo，首次 OpenCL 调优耗时约 5 分钟；调优缓存生成后，修正请求格式后 34.6 秒返回建议。
- 根因：
  1) 主进程向 KataGo analysis 传了错误的 analyzeTurns: ['B'/'W']，KataGo 要求整数数组，因此返回 error JSON。
  2) 主进程没有立即处理 KataGo error JSON，导致前端一直等待直到超时，看起来像“分析中”。
  3) 内置围棋建议只做邻近/中心评分，不会看对手气口、提子、救棋、切断等围棋基本战术。
- 已修复：
  1) KataGo 请求改为 initialPlayer: 'B'/'W'，不再传错误的字符串 analyzeTurns。
  2) KataGo error JSON 会立即返回前端，不再假卡到超时。
  3) 本地开发态增加 electron/engines/katago 查找路径。
  4) KataGo 超时后会清理进程，避免卡死会话污染下一次分析。
  5) GoPanel 状态显示改为可观测：分析中显示耗时，并说明已先显示内置全局建议。
  6) 内置建议升级为全局启发式：模拟落子、提子、自杀、己方被打吃救援、对方气口攻击、连接、切断、防止对方提子、自紧气降权。
  7) QA 脚本 Windows 下用 taskkill /t /f 清理 Vite 子进程，避免端口残留导致验收假超时。
- 验证：
  - 真实 KataGo smoke：ok=true，约 34595ms 返回 R17/R3/O17 等候选点。
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - python -m unittest discover -s tests -p test_go_game.py 通过（5 passed）
  - node electron/scripts/qa-game-panels-playwright.mjs 通过

2026-06-03（围棋内置建议增加对手布局评估）
- 用户反馈：KataGo 可用时会看对手，但内置全局建议也必须根据对手落子布局。
- 已修复：
  1) GoPanel 内置建议新增黑白影响力图 buildGoInfluence，候选点会评估己方势力、对手势力、双方争夺区。
  2) 新增 opponentMoveValue，模拟“如果对手下一手下在这里”的价值，用于抢占/反制对手关键点。
  3) 评分新增：抢对手价值点、打入/削减对手模样、扩张己方模样、深入敌阵自紧气降权。
  4) 建议理由新增：打断对方布局、削减对方势力、扩张己方势力。
- 验证：
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - python -m unittest discover -s tests -p test_go_game.py 通过（5 passed）
  - node electron/scripts/qa-game-panels-playwright.mjs 通过
  - Playwright 强制 KataGo 回退内置对照：普通白棋布局建议 9,10 / 10,9 / 10,11；白棋压迫中腹后变为 10,9 / 9,11 / 11,11，说明内置建议会随对手落子变化。

2026-06-03（客户端关闭后重连续玩）
- 用户要求：客户端关闭后重新连接也能继续当前游戏，不只支持同账号多端同时在线。
- 根因：此前服务端只在同昵称旧连接和新连接同时在线时迁移席位；如果客户端先关闭，remove_client 会触发 on_player_leave，房间空时还会丢弃 room_games，导致重启后无法续玩。
- 已修复：
  1) server.py 新增 disconnected_sessions，按昵称保存最近加入房间和活跃房间，默认 24 小时可恢复。
  2) 断线时如果玩家正在未结束游戏中，不再立即离席；保留旧连接席位，重连后按昵称换绑到新连接。
  3) 重连握手会继承上次房间，自动接管旧席位，并主动发送 Rooms 和当前游戏 show，让客户端游戏面板立即恢复。
  4) Electron 启动时如果有保存的连接配置，会自动重连；失败才回到登录框。
  5) 补充 tests/test_multi_device_resume.py，覆盖断线无第二端在线、稍后重连接管，以及五子棋重连后继续落子。
  6) 修正旧测试：五子棋现在按产品要求落子后实时推送棋盘，send_view_on_move 断言改为 True；python-chess 未安装时跳过国际象棋专项。
- 验证：
  - python -m unittest discover -s tests -v 通过（49 tests，47 passed，2 skipped: python-chess optional dependency）
  - electron: npx tsc -p tsconfig.json --noEmit 通过
  - electron: npx tsc -p tsconfig.node.json --noEmit 通过
  - node electron/scripts/qa-game-panels-playwright.mjs 通过
