export type LibraryBookItem = {
  id: string;
  index: number;
  format: string;
  name: string;
  size: string;
  bookmarkPage?: number;
};

export type LibraryBookmarkItem = {
  label: string;
  page: number;
};

export type LibraryReading = {
  title: string;
  page: number;
  total: number;
  body: string;
};

export type LibraryViewState = {
  books: LibraryBookItem[];
  bookmarks: LibraryBookmarkItem[];
  reading: LibraryReading | null;
  collectingBody: boolean;
  inCatalog: boolean;
  inBookmarks: boolean;
};

export const emptyLibraryViewState = (): LibraryViewState => ({
  books: [],
  bookmarks: [],
  reading: null,
  collectingBody: false,
  inCatalog: false,
  inBookmarks: false,
});

export type LibraryMessageEffect = {
  state: LibraryViewState;
  hideFromChat: boolean;
};

const PAGE_HEADER_RE = /^---\s*《(.+?)》\s*第\s*(\d+)\/(\d+)\s*页\s*---$/;
const CATALOG_HEADER_RE = /^---\s*图书馆\s*---$/;
const BOOKMARKS_HEADER_RE = /^---\s*我的书签\s*---$/;
const BOOK_LINE_RE =
  /^(\d+)\.\s+\[([A-Z]+)\]\s+(.+?)\s+\(([^)]+)\)(?:\s+·\s+书签第\s+(\d+)\s+页)?$/;
const BOOKMARK_LINE_RE = /^(.+?)\s+·\s+第\s+(\d+)\s+页$/;

export function applyLibrarySystemMessage(
  prev: LibraryViewState,
  rawContent: string,
): LibraryMessageEffect {
  const content = rawContent.trimEnd();
  let state = prev;

  if (CATALOG_HEADER_RE.test(content)) {
    return {
      state: {
        ...prev,
        books: [],
        inCatalog: true,
        inBookmarks: false,
        collectingBody: false,
      },
      hideFromChat: false,
    };
  }

  if (BOOKMARKS_HEADER_RE.test(content)) {
    return {
      state: {
        ...prev,
        bookmarks: [],
        inBookmarks: true,
        inCatalog: false,
        collectingBody: false,
      },
      hideFromChat: false,
    };
  }

  const bookMatch = content.match(BOOK_LINE_RE);
  if (state.inCatalog && bookMatch) {
    const idx = Number.parseInt(bookMatch[1], 10);
    const bookmarkPage = bookMatch[5] ? Number.parseInt(bookMatch[5], 10) : undefined;
    const item: LibraryBookItem = {
      id: String(idx),
      index: idx,
      format: bookMatch[2],
      name: bookMatch[3],
      size: bookMatch[4],
      bookmarkPage,
    };
    return {
      state: {
        ...state,
        books: [...state.books, item],
      },
      hideFromChat: false,
    };
  }

  const bookmarkMatch = content.match(BOOKMARK_LINE_RE);
  if (state.inBookmarks && bookmarkMatch) {
    return {
      state: {
        ...state,
        bookmarks: [
          ...state.bookmarks,
          {
            label: bookmarkMatch[1],
            page: Number.parseInt(bookmarkMatch[2], 10),
          },
        ],
      },
      hideFromChat: false,
    };
  }

  const pageMatch = content.match(PAGE_HEADER_RE);
  if (pageMatch) {
    return {
      state: {
        ...state,
        collectingBody: true,
        reading: {
          title: pageMatch[1],
          page: Number.parseInt(pageMatch[2], 10),
          total: Number.parseInt(pageMatch[3], 10),
          body: '',
        },
      },
      hideFromChat: true,
    };
  }

  if (state.collectingBody && state.reading) {
    if (content.startsWith('翻页：')) {
      return {
        state: {
          ...state,
          collectingBody: false,
        },
        hideFromChat: true,
      };
    }
    const body = state.reading.body ? `${state.reading.body}\n${content}` : content;
    return {
      state: {
        ...state,
        reading: {
          ...state.reading,
          body,
        },
      },
      hideFromChat: true,
    };
  }

  return { state, hideFromChat: false };
}

/** Rebuild library view from historical system messages (catalog/bookmarks fallback). */
export function rebuildLibraryViewFromMessages(
  messages: { type: string; sender: string; content: string }[],
): LibraryViewState {
  const systemMsgs = messages.filter((m) => m.type === 'system' && m.sender === '*');
  let state = emptyLibraryViewState();
  for (const msg of systemMsgs) {
    state = applyLibrarySystemMessage(state, msg.content).state;
  }
  return state;
}
