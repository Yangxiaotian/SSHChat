import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useChatStore } from '../store/chatStore';
import { useTranslation } from '../i18n';

const LOGICAL_W = 1200;
const LOGICAL_H = 800;

type StrokeEvent = {
  seq?: number;
  kind?: string;
  color?: string;
  width?: number;
  points?: number[][];
};

function tokenFromCanvasUrl(url: string): string | null {
  try {
    const u = new URL(url);
    const parts = u.pathname.replace(/\/+$/, '').split('/').filter(Boolean);
    if (parts.length >= 2 && parts[0] === 'canvas') return parts[1];
  } catch {
    // ignore
  }
  return null;
}

export default function CanvasPanel() {
  const { t } = useTranslation();
  const session = useChatStore((s) => s.canvasSession);
  const closeCanvas = useChatStore((s) => s.closeCanvas);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawingRef = useRef(false);
  const pointsRef = useRef<number[][]>([]);
  const ticketRef = useRef('');
  const sinceRef = useRef(0);
  const historyRef = useRef<StrokeEvent[]>([]);
  const syncingRef = useRef(false);
  const pollCountRef = useRef(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [meta, setMeta] = useState('');
  const [color, setColor] = useState('#222222');
  const [width, setWidth] = useState(3);
  const [ready, setReady] = useState(false);

  const drawStroke = useCallback((stroke: StrokeEvent) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const pts = stroke.points || [];
    if (!pts.length) return;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = stroke.color || '#222';
    ctx.lineWidth = stroke.width || 3;
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i += 1) ctx.lineTo(pts[i][0], pts[i][1]);
    if (pts.length === 1) ctx.lineTo(pts[0][0] + 0.01, pts[0][1]);
    ctx.stroke();
  }, []);

  const clearLocal = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }, []);

  const rememberStroke = useCallback((ev: StrokeEvent) => {
    if (ev.kind !== 'stroke') return;
    const seq = Number(ev.seq || 0);
    if (seq && historyRef.current.some((h) => Number(h.seq || 0) === seq)) return;
    historyRef.current.push(ev);
  }, []);

  const replayHistory = useCallback(() => {
    clearLocal();
    for (const ev of historyRef.current) drawStroke(ev);
  }, [clearLocal, drawStroke]);

  const paintAll = useCallback(() => {
    replayHistory();
    if (drawingRef.current && pointsRef.current.length) {
      drawStroke({
        points: pointsRef.current,
        color,
        width,
        kind: 'stroke',
      });
    }
  }, [color, drawStroke, replayHistory, width]);

  const applyEvent = useCallback(
    (ev: StrokeEvent) => {
      if (!ev) return;
      if (ev.kind === 'clear') {
        historyRef.current = [];
        return;
      }
      if (ev.kind === 'stroke') rememberStroke(ev);
    },
    [rememberStroke],
  );

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const syncOnce = useCallback(
    async (baseUrl: string, token: string, initial: boolean) => {
      if (!ticketRef.current || syncingRef.current) return;
      syncingRef.current = true;
      try {
        pollCountRef.current += 1;
        const rebuild = initial || pollCountRef.current % 40 === 0;
        if (rebuild) sinceRef.current = 0;
        const res = await window.api.canvasHttp({
          url: `${baseUrl}/canvas/${token}/sync?since=${sinceRef.current}&ticket=${encodeURIComponent(ticketRef.current)}`,
          method: 'GET',
          headers: { 'X-Canvas-Ticket': ticketRef.current },
        });
        if (!res.ok) {
          setStatus(t('canvasPanel.syncError'));
          setError(res.error || 'sync failed');
          return;
        }
        const data = res.json || {};
        const bits: string[] = [];
        if (data.participant) bits.push(`${t('canvasPanel.you')}: ${data.participant}`);
        if (data.room) bits.push(`${t('canvasPanel.room')}: #${data.room}`);
        setMeta(bits.join(' · '));
        if (rebuild) historyRef.current = [];
        for (const ev of data.events || []) {
          applyEvent(ev);
          sinceRef.current = Math.max(sinceRef.current, Number(ev.seq || 0));
        }
        paintAll();
        if (!initial) setStatus(t('canvasPanel.ready'));
        setError('');
      } finally {
        syncingRef.current = false;
      }
    },
    [applyEvent, paintAll, t],
  );

  useEffect(() => {
    let cancelled = false;
    stopPoll();
    ticketRef.current = '';
    sinceRef.current = 0;
    historyRef.current = [];
    pollCountRef.current = 0;
    setReady(false);
    setError('');
    setMeta('');
    clearLocal();
    if (!session) return undefined;

    const token = tokenFromCanvasUrl(session.url);
    if (!token) {
      setError(t('canvasPanel.badUrl'));
      return undefined;
    }
    let baseUrl = '';
    try {
      const u = new URL(session.url);
      baseUrl = `${u.protocol}//${u.host}`;
    } catch {
      setError(t('canvasPanel.badUrl'));
      return undefined;
    }

    (async () => {
      setStatus(t('canvasPanel.unlocking'));
      const auth = await window.api.canvasHttp({
        url: `${baseUrl}/canvas/${token}/auth`,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: session.key }),
      });
      if (cancelled) return;
      if (!auth.ok || !auth.json?.ticket) {
        setError(auth.error || t('canvasPanel.authFailed'));
        setStatus('');
        return;
      }
      ticketRef.current = String(auth.json.ticket);
      setReady(true);
      setStatus(t('canvasPanel.ready'));
      await syncOnce(baseUrl, token, true);
      if (cancelled) return;
      pollRef.current = setInterval(() => {
        void syncOnce(baseUrl, token, false);
      }, 900);
    })();

    return () => {
      cancelled = true;
      stopPoll();
    };
  }, [session, clearLocal, stopPoll, syncOnce, t]);

  const pointerPos = (e: React.PointerEvent<HTMLCanvasElement>): number[] => {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const x = ((e.clientX - rect.left) * LOGICAL_W) / rect.width;
    const y = ((e.clientY - rect.top) * LOGICAL_H) / rect.height;
    return [
      Math.max(0, Math.min(LOGICAL_W, x)),
      Math.max(0, Math.min(LOGICAL_H, y)),
    ];
  };

  const postStroke = async (points: number[][]) => {
    if (!session || !ticketRef.current || points.length < 1) return;
    const token = tokenFromCanvasUrl(session.url);
    if (!token) return;
    const u = new URL(session.url);
    const baseUrl = `${u.protocol}//${u.host}`;
    const res = await window.api.canvasHttp({
      url: `${baseUrl}/canvas/${token}/stroke`,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Canvas-Ticket': ticketRef.current,
      },
      body: JSON.stringify({ color, width, points }),
    });
    if (!res.ok) {
      setStatus(t('canvasPanel.syncError'));
      setError(res.error || 'stroke failed');
      return;
    }
    const seq = Number(res.json?.event?.seq || 0);
    if (seq) sinceRef.current = Math.max(sinceRef.current, seq);
    rememberStroke({
      seq,
      kind: 'stroke',
      color,
      width,
      points,
    });
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!ready) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drawingRef.current = true;
    pointsRef.current = [pointerPos(e)];
    drawStroke({ points: pointsRef.current, color, width, kind: 'stroke' });
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current) return;
    const pt = pointerPos(e);
    const prev = pointsRef.current[pointsRef.current.length - 1];
    pointsRef.current.push(pt);
    drawStroke({ points: [prev, pt], color, width, kind: 'stroke' });
  };

  const onPointerUp = () => {
    if (!drawingRef.current) return;
    drawingRef.current = false;
    const pts = pointsRef.current.slice();
    pointsRef.current = [];
    void postStroke(pts);
  };

  const onClear = async () => {
    if (!session || !ticketRef.current) return;
    if (!window.confirm(t('canvasPanel.clearConfirm'))) return;
    const token = tokenFromCanvasUrl(session.url);
    if (!token) return;
    const u = new URL(session.url);
    const baseUrl = `${u.protocol}//${u.host}`;
    const res = await window.api.canvasHttp({
      url: `${baseUrl}/canvas/${token}/clear`,
      method: 'POST',
      headers: { 'X-Canvas-Ticket': ticketRef.current },
    });
    if (!res.ok) {
      setError(res.error || 'clear failed');
      return;
    }
    clearLocal();
    historyRef.current = [];
    const seq = Number(res.json?.event?.seq || 0);
    if (seq) sinceRef.current = Math.max(sinceRef.current, seq);
  };

  if (!session) return null;

  return (
    <div className="canvas-panel">
      <div className="canvas-panel-toolbar">
        <div className="canvas-panel-title">{t('canvasPanel.title')}</div>
        <div className="canvas-panel-meta">{meta || status}</div>
        <label className="canvas-panel-tool">
          {t('canvasPanel.color')}
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} disabled={!ready} />
        </label>
        <label className="canvas-panel-tool">
          {t('canvasPanel.width')}
          <input
            type="range"
            min={1}
            max={16}
            value={width}
            onChange={(e) => setWidth(Number(e.target.value))}
            disabled={!ready}
          />
        </label>
        <button type="button" className="mini-btn" onClick={() => void onClear()} disabled={!ready}>
          {t('canvasPanel.clear')}
        </button>
        <button type="button" className="mini-btn" onClick={() => closeCanvas()}>
          {t('canvasPanel.close')}
        </button>
      </div>
      {error ? <div className="canvas-panel-error">{error}</div> : null}
      <div className="canvas-panel-stage">
        <canvas
          ref={canvasRef}
          className="canvas-panel-board"
          width={LOGICAL_W}
          height={LOGICAL_H}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        />
      </div>
    </div>
  );
}
