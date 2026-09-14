import React, { useMemo, useState } from 'react';
import { useTranslation } from '../../i18n';

type Props = {
  disabled: boolean;
  nickname: string;
  boardText: string;
  hintText?: string;
  onCmd: (cmd: string) => void;
  onOpenCanvas?: () => void;
};

function parseMeta(boardText: string): {
  state: string;
  host: string;
  drawer: string;
  round: string;
  scores: string;
} {
  let state = '';
  let host = '';
  let drawer = '';
  let round = '';
  let scores = '';

  for (const raw of boardText.split('\n')) {
    const line = raw.trim();
    if (!state) {
      const m = line.match(/drawguess state[:：]\s*(\w+)/i) || line.match(/状态[:：]\s*(\w+)/i);
      if (m) state = m[1].trim();
    }
    if (!host) {
      const m =
        line.match(/^host[:：]\s*(.+)$/i) ||
        line.match(/^\-\s+([^\s]+)\s+\(\d+\s*pts\)\s*host/i);
      if (m) host = m[1].trim();
    }
    if (!drawer) {
      const m = line.match(/^drawer[:：]\s*(.+)$/i) || line.match(/^画家[:：]\s*(.+)$/i);
      if (m) drawer = m[1].trim();
    }
    if (!round) {
      const m = line.match(/^round[:：]\s*(.+)$/i) || line.match(/^回合[:：]\s*(.+)$/i);
      if (m) round = m[1].trim();
    }
    if (!scores && (/^积分[:：]/.test(line) || /^scores?[:：]/i.test(line))) {
      scores = line;
    }
  }
  return { state, host, drawer, round, scores };
}

function parseSecret(text: string): string {
  const matches = [...text.matchAll(/本回合词[:：]\s*【([^】]+)】/g)];
  if (!matches.length) return '';
  return matches[matches.length - 1][1].trim();
}

export default function DrawGuessPanel({
  disabled,
  nickname,
  boardText,
  hintText = '',
  onCmd,
  onOpenCanvas,
}: Props) {
  const [guess, setGuess] = useState('');
  const [canvasRequested, setCanvasRequested] = useState(false);
  const { t, locale } = useTranslation();
  const meta = useMemo(() => parseMeta(boardText), [boardText]);
  const secret = useMemo(
    () => parseSecret(hintText) || parseSecret(boardText),
    [hintText, boardText],
  );
  const state = meta.state.toLowerCase();
  const waiting = state === 'waiting' || !state;
  const drawing = state === 'drawing';
  const isHost = !!meta.host && meta.host === nickname;
  const isDrawer = !!meta.drawer && meta.drawer === nickname;
  const canStart = isHost && waiting;
  const canGuess = drawing && !isDrawer;
  const canSkip = drawing && (isDrawer || isHost);
  const canWord = drawing && isDrawer;

  return (
    <div className="game-interaction-panel">
      <div className="game-interaction-title">{t('game.drawguess.title')}</div>
      <div className="game-workbench-hint">
        {t('game.drawguess.phase', {
          state: meta.state || '?',
          round: meta.round || '?',
          drawer: meta.drawer || (locale === 'zh' ? '未定' : 'n/a'),
        })}
      </div>
      {meta.scores ? <div className="game-workbench-hint">{meta.scores}</div> : null}
      {secret && isDrawer ? (
        <div className="game-workbench-hint">
          {t('game.drawguess.yourWord', { word: secret })}
        </div>
      ) : null}

      <div className="game-chip-row">
        <button className="mini-btn" disabled={disabled || !canStart} onClick={() => onCmd('start')}>
          {t('game.drawguess.start')}
        </button>
        <button className="mini-btn" disabled={disabled || !canSkip} onClick={() => onCmd('skip')}>
          {t('game.drawguess.skip')}
        </button>
        <button className="mini-btn" disabled={disabled || !canWord} onClick={() => onCmd('word')}>
          {t('game.drawguess.word')}
        </button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('scores')}>
          {t('game.drawguess.scores')}
        </button>
        <button
          className="mini-btn"
          disabled={disabled || canvasRequested}
          onClick={() => {
            setCanvasRequested(true);
            if (onOpenCanvas) onOpenCanvas();
            else onCmd('/canvas');
            window.setTimeout(() => setCanvasRequested(false), 8000);
          }}
        >
          {canvasRequested ? (locale === 'zh' ? '正在打开画板...' : 'Opening canvas...') : t('game.drawguess.openCanvas')}
        </button>
        <button className="mini-btn" disabled={disabled} onClick={() => onCmd('/game end')}>
          {t('game.end')}
        </button>
      </div>

      <div className="game-chip-row" style={{ alignItems: 'center', gap: 8 }}>
        <input
          className="game-lobby-search"
          value={guess}
          disabled={disabled || !canGuess}
          onChange={(e) => setGuess(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && canGuess && guess.trim()) {
              onCmd(`guess ${guess.trim()}`);
              setGuess('');
            }
          }}
          placeholder={t('game.drawguess.guessPlaceholder')}
          aria-label={t('game.drawguess.guessPlaceholder')}
        />
        <button
          className="mini-btn"
          disabled={disabled || !canGuess || !guess.trim()}
          onClick={() => {
            onCmd(`guess ${guess.trim()}`);
            setGuess('');
          }}
        >
          {t('game.drawguess.guess')}
        </button>
      </div>
    </div>
  );
}
