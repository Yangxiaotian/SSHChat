import React from 'react';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  onCmd: (cmd: string) => void;
};

function extractHand(boardText: string): number[] {
  const line = boardText.split('\n').find((l) => l.toLowerCase().includes('your hand') || l.includes('你的手牌'));
  if (!line) return [];
  const nums = line.match(/\d+/g);
  return nums ? nums.map((x) => Number(x)) : [];
}

function parseRows(boardText: string): string[] {
  return boardText.split('\n').filter((l) => l.toLowerCase().includes('row') || l.includes('行：'));
}

function parseMeta(text: string): { state: string; host: string; awaitPlayer: string } {
  let state = '';
  let host = '';
  let awaitPlayer = '';
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!state) {
      const m = t.match(/(状态|state)[:：]\s*(.+)$/i);
      if (m) state = m[2].trim();
    }
    if (!host) {
      const m = t.match(/^#1\s+([^:：]+)[:：]/);
      if (m) host = m[1].trim();
      const m2 = t.match(/^房主[:：]\s*(.+)$/);
      if (!host && m2) host = m2[1].trim();
    }
    if (!awaitPlayer) {
      const m1 = t.match(/^请等待\s+(\S+)\s+选择吃哪一行/);
      const m2 = t.match(/^(\S+)\s+需要选行/);
      if (m1) awaitPlayer = m1[1];
      else if (m2) awaitPlayer = m2[1];
    }
  }
  return { state, host, awaitPlayer };
}

function includesAny(source: string, needles: string[]): boolean {
  const s = source.toLowerCase();
  return needles.some((n) => s.includes(n.toLowerCase()));
}

export default function NiuTouPanel({ disabled, nickname, boardText, onCmd }: Props) {
  const hand = extractHand(boardText);
  const rows = parseRows(boardText);
  const meta = parseMeta(boardText);
  const isHost = !!meta.host && meta.host === nickname;
  const canStart = isHost && (includesAny(meta.state, ['等待开始', 'waiting']) || includesAny(meta.state, ['已结束', 'ended']));
  const canPick = includesAny(meta.state, ['进行中', 'playing']) && hand.length > 0;
  const canChooseRow = boardText.includes('你必须选择一行') || (includesAny(meta.state, ['等待选行', 'await_row']) && meta.awaitPlayer === nickname);
  const canTuneBot = isHost;

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">谁是牛头王互动面板（新手引导）</div>
      <div className="game-workbench-hint">每回合点一张手牌；若提示必须吃行，再点“吃第1~4行”。牛头越少越好。</div>
      {rows.length > 0 && (
        <div className="game-chip-row">
          {rows.map((r, i) => (
            <div key={i} className="poker-card">{r}</div>
          ))}
        </div>
      )}
      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !canStart} onClick={() => onCmd('start')}>发牌开始</button>
        <button className="mini-btn" disabled={disabled || !canChooseRow} onClick={() => onCmd('row 1')}>吃第1行</button>
        <button className="mini-btn" disabled={disabled || !canChooseRow} onClick={() => onCmd('row 2')}>吃第2行</button>
        <button className="mini-btn" disabled={disabled || !canChooseRow} onClick={() => onCmd('row 3')}>吃第3行</button>
        <button className="mini-btn" disabled={disabled || !canChooseRow} onClick={() => onCmd('row 4')}>吃第4行</button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>结束对局</button>
      </div>
      <div className="game-chip-row">
        {hand.map((n) => (
          <button key={n} className="mini-btn" disabled={disabled || !canPick} onClick={() => onCmd(`pick ${n}`)}>{n}</button>
        ))}
      </div>
      <div className="game-chip-row">
        <span className="game-workbench-hint">机器人难度</span>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot easy')}>Easy</button>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot hard')}>Hard</button>
        <button className="mini-btn" disabled={disabled || !canTuneBot} onClick={() => onCmd('bot pro')}>Pro</button>
      </div>
    </div>
  );
}
