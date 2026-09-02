import { useTranslation } from '../i18n';
import { useChatStore } from '../store/chatStore';

/** Embed the server room piano page; #k= is client-only key autofill. */
export default function PianoPanel() {
  const { t } = useTranslation();
  const session = useChatStore((s) => s.pianoSession);
  const closePiano = useChatStore((s) => s.closePiano);
  const maximized = useChatStore((s) => s.pianoMaximized);
  const setPianoMaximized = useChatStore((s) => s.setPianoMaximized);

  if (!session) return null;

  const src = `${session.url}#k=${encodeURIComponent(session.key.toUpperCase())}`;

  return (
    <div className={`canvas-panel piano-panel${maximized ? ' maximized' : ''}`}>
      <div className="canvas-panel-toolbar">
        <div className="canvas-panel-title">{t('pianoPanel.title')}</div>
        <div className="canvas-panel-toolbar-actions">
          <button
            type="button"
            className="mini-btn"
            onClick={() => setPianoMaximized(!maximized)}
          >
            {maximized ? t('pianoPanel.restore') : t('pianoPanel.maximize')}
          </button>
          <button type="button" className="mini-btn" onClick={() => closePiano()}>
            {t('pianoPanel.close')}
          </button>
        </div>
      </div>
      <div className="canvas-panel-stage canvas-panel-stage-iframe">
        <iframe
          title={t('pianoPanel.title')}
          src={src}
          className="canvas-panel-frame"
          allow="autoplay"
        />
      </div>
    </div>
  );
}
