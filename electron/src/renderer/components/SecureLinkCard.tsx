import React, { useState } from 'react';
import {
  defaultSecureLinkAction,
  defaultSecureLinkTitle,
  type SecureLinkPayload,
} from '../lib/secureLinks';
import { useTranslation } from '../i18n';

interface SecureLinkCardProps {
  payload: SecureLinkPayload;
}

export default function SecureLinkCard({ payload }: SecureLinkCardProps) {
  const { locale, t } = useTranslation();
  const loc = locale === 'zh' ? 'zh' : 'en';
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const title = payload.title || defaultSecureLinkTitle(payload.kind, loc);
  const action = defaultSecureLinkAction(payload.kind, loc);
  const hint =
    payload.kind === 'canvas'
      ? t('secureLink.canvasHint')
      : payload.kind === 'upload'
        ? t('secureLink.uploadHint')
        : t('secureLink.downloadHint');

  const onOpen = async () => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
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

  return (
    <div className={`secure-link-card kind-${payload.kind}`}>
      <div className="secure-link-icon" aria-hidden>
        {payload.kind === 'canvas' ? '✎' : payload.kind === 'upload' ? '↑' : '↓'}
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
