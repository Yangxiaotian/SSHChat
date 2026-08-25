import { useTranslation } from '../i18n';
import { useChatStore } from '../store/chatStore';

/** Embed the server Excalidraw page; #k= is client-only key autofill. */
export default function CanvasPanel() {
  const { t } = useTranslation();
  const session = useChatStore((s) => s.canvasSession);
  const closeCanvas = useChatStore((s) => s.closeCanvas);

  if (!session) return null;

  const src = `${session.url}#k=${encodeURIComponent(session.key.toUpperCase())}`;

  return (
    <div className="canvas-panel">
      <div className="canvas-panel-toolbar">
        <div className="canvas-panel-title">{t('canvasPanel.title')}</div>
        <button type="button" className="mini-btn" onClick={() => closeCanvas()}>
          {t('canvasPanel.close')}
        </button>
      </div>
      <div className="canvas-panel-stage canvas-panel-stage-iframe">
        <iframe
          title={t('canvasPanel.title')}
          src={src}
          className="canvas-panel-frame"
          allow="clipboard-read; clipboard-write"
        />
      </div>
    </div>
  );
}
