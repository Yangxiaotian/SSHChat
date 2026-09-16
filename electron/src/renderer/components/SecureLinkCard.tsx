import React, { useEffect, useState } from 'react';
import {
  defaultSecureLinkAction,
  defaultSecureLinkTitle,
  type SecureLinkPayload,
} from '../lib/secureLinks';
import { useTranslation } from '../i18n';
import { useChatStore } from '../store/chatStore';

interface SecureLinkCardProps {
  payload: SecureLinkPayload;
}

export default function SecureLinkCard({ payload }: SecureLinkCardProps) {
  const { locale, t } = useTranslation();
  const loc = locale === 'zh' ? 'zh' : 'en';
  const openCanvas = useChatStore((s) => s.openCanvas);
  const openPiano = useChatStore((s) => s.openPiano);
  const canvasSession = useChatStore((s) => s.canvasSession);
  const expectingOwnCanvas = useChatStore((s) => s.expectingOwnCanvas);
  const canvasRequestAt = useChatStore((s) => s.canvasRequestAt);
  const expectingOwnPiano = useChatStore((s) => s.expectingOwnPiano);
  const setExpectingOwnCanvas = useChatStore((s) => s.setExpectingOwnCanvas);
  const setExpectingOwnPiano = useChatStore((s) => s.setExpectingOwnPiano);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [autoOpened, setAutoOpened] = useState(false);

  const title = payload.title || defaultSecureLinkTitle(payload.kind, loc);
  const action = defaultSecureLinkAction(payload.kind, loc);
  const hint =
    payload.kind === 'canvas'
      ? t('secureLink.canvasHint')
      : payload.kind === 'piano'
        ? t('secureLink.pianoHint')
        : payload.kind === 'upload'
          ? t('secureLink.uploadHint')
          : t('secureLink.downloadHint');

  const canvasTokenFromUrl = (url: string): string => {
    try {
      const parts = new URL(url).pathname.split('/').filter(Boolean);
      if (parts[0] === 'canvas' && parts[1]) return parts[1];
    } catch {
      /* ignore */
    }
    return '';
  };

  const onOpen = async () => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      if (payload.kind === 'canvas') {
        openCanvas({ url: payload.url, key: payload.key });
        return;
      }
      if (payload.kind === 'piano') {
        openPiano({ url: payload.url, key: payload.key });
        return;
      }
      const result = await window.api.openSecureWebSession({
        kind: payload.kind,
        url: payload.url,
        key: payload.key,
      });
      if (!result?.ok) {
        setError(result?.error || t('secureLink.openFailed'));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t('secureLink.openFailed'));
    } finally {
      setBusy(false);
    }
  };

  // Same as Tk: only auto-open on the client that just clicked 画板/钢琴.
  useEffect(() => {
    if (autoOpened || busy) return;
    if (
      payload.kind === 'canvas' &&
      expectingOwnCanvas &&
      (!payload.receivedAt || payload.receivedAt >= canvasRequestAt)
    ) {
      setAutoOpened(true);
      setExpectingOwnCanvas(false);
      openCanvas({ url: payload.url, key: payload.key });
      return;
    }
    if (payload.kind === 'piano' && expectingOwnPiano) {
      setAutoOpened(true);
      setExpectingOwnPiano(false);
      openPiano({ url: payload.url, key: payload.key });
    }
  }, [
    autoOpened,
    busy,
    expectingOwnCanvas,
    canvasRequestAt,
    expectingOwnPiano,
    openCanvas,
    openPiano,
    payload.key,
    payload.kind,
    payload.url,
    payload.receivedAt,
    setExpectingOwnCanvas,
    setExpectingOwnPiano,
  ]);

  // Quick Tunnel hostname churn: if this board is already open, swap to the
  // new trycloudflare URL so the iframe does not stay on NXDOMAIN.
  useEffect(() => {
    if (payload.kind !== 'canvas' || !canvasSession) return;
    const nextTok = canvasTokenFromUrl(payload.url);
    const openTok = canvasTokenFromUrl(canvasSession.url);
    if (
      nextTok &&
      nextTok === openTok &&
      (payload.url !== canvasSession.url || payload.key !== canvasSession.key)
    ) {
      openCanvas({ url: payload.url, key: payload.key });
    }
  }, [
    canvasSession,
    openCanvas,
    payload.key,
    payload.kind,
    payload.url,
  ]);

  return (
    <div className={`secure-link-card kind-${payload.kind}`}>
      <div className="secure-link-icon" aria-hidden>
        {payload.kind === 'canvas' ? '✎' : payload.kind === 'piano' ? '🎹' : payload.kind === 'upload' ? '↑' : '↓'}
      </div>
      <div className="secure-link-body">
        <div className="secure-link-title">{title}</div>
        {payload.subtitle ? <div className="secure-link-sub">{payload.subtitle}</div> : null}
        <div className="secure-link-hint">{hint}</div>
        {error ? <div className="secure-link-error">{error}</div> : null}
      </div>
      <button className="secure-link-btn" type="button" onClick={onOpen} disabled={busy}>
        {busy ? t('secureLink.opening') : action}
      </button>
    </div>
  );
}
