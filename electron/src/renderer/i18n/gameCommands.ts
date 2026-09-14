import type { Locale } from './types';
import type { GameKind } from '../components/games/types';

const MOVE_VERBS: Record<string, Record<Locale, string>> = {
  start: { zh: '开始', en: 'start' },
  look: { zh: '看牌', en: 'look' },
  check: { zh: '过牌', en: 'check' },
  call: { zh: '跟注', en: 'call' },
  follow: { zh: '跟注', en: 'follow' },
  fold: { zh: '弃牌', en: 'fold' },
  allin: { zh: '全下', en: 'allin' },
  raise: { zh: '加注', en: 'raise' },
  compare: { zh: '比牌', en: 'compare' },
  bot: { zh: '机器人', en: 'bot' },
  pass: { zh: '过', en: 'pass' },
  passgo: { zh: '停一手', en: 'pass' },
  generals: { zh: '武将', en: 'generals' },
  flip: { zh: '翻', en: 'flip' },
  darkMove: { zh: '走', en: 'move' },
};

function resolvePassVerb(locale: Locale, game?: GameKind): string {
  if (game === 'go') return MOVE_VERBS.passgo[locale];
  return MOVE_VERBS.pass[locale];
}

/** Build `/game move …` with locale-appropriate verb tokens. */
export function buildGameMove(locale: Locale, payload: string, game?: GameKind): string {
  const trimmed = payload.trim();
  if (!trimmed) return '/game move';
  if (trimmed.startsWith('/')) return trimmed;

  const parts = trimmed.split(/\s+/);
  const headLower = parts[0].toLowerCase();

  // Werewolf server verbs are English-only.
  if (game === 'werewolf') {
    return `/game move ${trimmed}`;
  }

  if (/[\u4e00-\u9fff]/.test(parts[0])) {
    return `/game move ${trimmed}`;
  }

  if (headLower === 'pass') {
    parts[0] = resolvePassVerb(locale, game);
    return `/game move ${parts.join(' ')}`;
  }

  // Darkchess uses "move" as a piece-move verb (not the generic /game move wrapper).
  if (game === 'darkchess' && headLower === 'move') {
    parts[0] = MOVE_VERBS.darkMove[locale];
    return `/game move ${parts.join(' ')}`;
  }

  if (headLower === 'flip') {
    parts[0] = MOVE_VERBS.flip[locale];
    return `/game move ${parts.join(' ')}`;
  }

  const mapped = MOVE_VERBS[headLower]?.[locale];
  if (mapped) {
    parts[0] = mapped;
    return `/game move ${parts.join(' ')}`;
  }

  return `/game move ${trimmed}`;
}

export function buildGameMoveFromKey(locale: Locale, key: keyof typeof MOVE_VERBS, ...args: string[]): string {
  const word = MOVE_VERBS[key][locale];
  return `/game move ${[word, ...args].join(' ')}`.trim();
}
