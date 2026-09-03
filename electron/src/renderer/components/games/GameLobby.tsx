import React, { useMemo, useState } from 'react';
import { useTranslation } from '../../i18n';
import type { Locale } from '../../i18n/types';

type GameEntry = { id: string; zh: string; en: string; category: string; command: string };
type Props = { disabled: boolean; onCommand: (command: string) => void };

const GAMES: GameEntry[] = [
  { id: 'chess', zh: '国际象棋', en: 'Chess', category: 'strategy', command: '/game new chess' },
  { id: 'xiangqi', zh: '中国象棋', en: 'Chinese Chess', category: 'strategy', command: '/game new xiangqi' },
  { id: 'go', zh: '围棋', en: 'Go', category: 'strategy', command: '/game new go' },
  { id: 'gomoku', zh: '五子棋', en: 'Gomoku', category: 'strategy', command: '/game new gomoku' },
  { id: 'reversi', zh: '黑白棋', en: 'Reversi', category: 'strategy', command: '/game new reversi' },
  { id: 'darkchess', zh: '暗棋 / 翻翻棋', en: 'Dark Chess', category: 'strategy', command: '/game new darkchess' },
  { id: 'junqi', zh: '军棋', en: 'Junqi', category: 'strategy', command: '/game new junqi' },
  { id: 'doushou', zh: '斗兽棋', en: 'Jungle', category: 'strategy', command: '/game new doushou' },
  { id: 'battleship', zh: '海战棋', en: 'Battleship', category: 'strategy', command: '/game new battleship' },
  { id: 'sanguo', zh: '三国杀', en: 'Sanguosha', category: 'social', command: '/game new sanguo' },
  { id: 'werewolf', zh: '狼人杀', en: 'Werewolf', category: 'social', command: '/game new werewolf' },
  { id: 'drawguess', zh: '你画我猜', en: 'Draw & Guess', category: 'social', command: '/game new drawguess' },
  { id: 'holdem', zh: '德州扑克', en: "Texas Hold'em", category: 'cards', command: '/game new holdem' },
  { id: 'zjh', zh: '炸金花', en: 'Three Card Poker', category: 'cards', command: '/game new zjh' },
  { id: 'niutou', zh: '牛头王', en: '6 nimmt!', category: 'cards', command: '/game new niutou' },
];

function categoryLabel(category: string, locale: Locale): string {
  if (category === 'social') return locale === 'zh' ? '社交游戏' : 'Social';
  if (category === 'cards') return locale === 'zh' ? '牌类游戏' : 'Cards';
  return locale === 'zh' ? '棋类与策略' : 'Board & Strategy';
}

export default function GameLobby({ disabled, onCommand }: Props) {
  const { locale } = useTranslation();
  const [query, setQuery] = useState('');
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return GAMES.filter((game) => !needle || `${game.id} ${game.zh} ${game.en}`.toLowerCase().includes(needle));
  }, [query]);
  const categories = [...new Set(filtered.map((game) => game.category))];

  return (
    <section className="game-lobby" aria-label={locale === 'zh' ? '游戏大厅' : 'Game lobby'}>
      <div className="game-lobby-header">
        <div>
          <div className="game-lobby-title">{locale === 'zh' ? '选择一款游戏' : 'Choose a game'}</div>
          <div className="game-workbench-hint">{locale === 'zh' ? '开局后主界面只保留当前对局操作。' : 'After a game starts, only its controls stay visible.'}</div>
        </div>
        <input
          className="game-lobby-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={locale === 'zh' ? '搜索游戏' : 'Search games'}
          aria-label={locale === 'zh' ? '搜索游戏' : 'Search games'}
        />
      </div>
      {categories.map((category) => (
        <div className="game-lobby-category" key={category}>
          <div className="game-lobby-category-title">{categoryLabel(category, locale)}</div>
          <div className="game-lobby-grid">
            {filtered.filter((game) => game.category === category).map((game) => (
              <button type="button" className="game-lobby-card" key={game.id} disabled={disabled} onClick={() => onCommand(game.command)}>
                <strong>{locale === 'zh' ? game.zh : game.en}</strong>
                <span>{game.command}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      {filtered.length === 0 && <div className="game-workbench-hint">{locale === 'zh' ? '没有匹配的游戏。' : 'No matching games.'}</div>}
    </section>
  );
}
