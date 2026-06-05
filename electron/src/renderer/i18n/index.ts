import { useChatStore } from '../store/chatStore';
import { enMessages } from './messages/en';
import { zhMessages } from './messages/zh';
import { DEFAULT_LOCALE, LOCALE_STORAGE_KEY, type Locale } from './types';

type MessageDict = typeof zhMessages;

const MESSAGES: Record<Locale, MessageDict> = {
  zh: zhMessages,
  en: enMessages,
};

function readStoredLocale(): Locale {
  try {
    const raw = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (raw === 'zh' || raw === 'en') return raw;
  } catch {
    // no-op
  }
  return DEFAULT_LOCALE;
}

function getByPath(obj: Record<string, unknown>, path: string): string | undefined {
  const parts = path.split('.');
  let cur: unknown = obj;
  for (const part of parts) {
    if (!cur || typeof cur !== 'object' || !(part in (cur as object))) {
      return undefined;
    }
    cur = (cur as Record<string, unknown>)[part];
  }
  return typeof cur === 'string' ? cur : undefined;
}

export function t(locale: Locale, key: string, vars?: Record<string, string | number>): string {
  const text = getByPath(MESSAGES[locale] as unknown as Record<string, unknown>, key) ?? key;
  if (!vars) return text;
  return Object.entries(vars).reduce(
    (acc, [k, v]) => acc.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v)),
    text,
  );
}

export function useTranslation() {
  const locale = useChatStore((s) => s.locale);
  return {
    locale,
    t: (key: string, vars?: Record<string, string | number>) => t(locale, key, vars),
  };
}

export function initLocaleFromStorage(): Locale {
  return readStoredLocale();
}

export function persistLocale(locale: Locale): void {
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // no-op
  }
}

export { type Locale } from './types';
export { buildGameMove, buildGameMoveFromKey } from './gameCommands';
