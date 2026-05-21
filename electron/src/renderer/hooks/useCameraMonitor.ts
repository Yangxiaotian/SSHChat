import { useEffect, useRef, useCallback, useState } from 'react';

interface UseCameraMonitorResult {
  videoRef: React.RefObject<HTMLVideoElement>;
  personCount: number;
  isRunning: boolean;
  modelLoaded: boolean;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
}

// Module-level state: persists across component mounts/unmounts (tab switches, sidebar hide)
let sharedStream: MediaStream | null = null;
let sharedModel: any = null;
let sharedModelLoaded = false;
let sharedIsRunning = false;
let sharedPersonCount = 0;
let sharedVideo: HTMLVideoElement | null = null;
let detectRunning = false; // prevents concurrent detect() calls
const sharedListeners = new Set<(count: number) => void>();
const sharedStateListeners = new Set<() => void>();

function notifyCountListeners(count: number) {
  sharedPersonCount = count;
  for (const fn of sharedListeners) fn(count);
}

function notifyStateListeners() {
  for (const fn of sharedStateListeners) fn();
}

async function loadModelShared(): Promise<any> {
  if (sharedModel) return sharedModel;
  const tf = await import('@tensorflow/tfjs');
  await tf.setBackend('webgl');
  const cocoSsd = await import('@tensorflow-models/coco-ssd');
  const model = await cocoSsd.load({
    base: 'lite_mobilenet_v2',
    modelUrl: './models/ssdlite_mobilenet_v2/model.json',
  });
  sharedModel = model;
  sharedModelLoaded = true;
  return model;
}

// Serialized detection loop: each frame waits for inference to finish before scheduling next.
// This prevents requestAnimationFrame from queuing up multiple concurrent detect() calls
// which was the root cause of multi-second latency.
function ensureDetectionLoop() {
  if (detectRunning) return;
  detectRunning = true;

  const detect = async () => {
    if (!sharedIsRunning) {
      detectRunning = false;
      return;
    }
    const video = sharedVideo;
    if (!video || !sharedModel || video.readyState < 2) {
      // Video not ready — retry very soon without wasting a full rAF cycle
      setTimeout(detect, 50);
      return;
    }
    try {
      const predictions: { class: string; score: number }[] = await sharedModel.detect(video);
      const count = predictions.filter((p) => p.class === 'person' && p.score > 0.5).length;
      notifyCountListeners(count);
    } catch {
      // video detached mid-frame; skip
    }
    // Schedule next detection immediately after current one finishes
    if (sharedIsRunning) {
      requestAnimationFrame(detect);
    } else {
      detectRunning = false;
    }
  };

  // Kick off first detection immediately
  requestAnimationFrame(detect);
}

function stopShared() {
  sharedIsRunning = false;
  detectRunning = false;
  if (sharedStream) {
    sharedStream.getTracks().forEach((t) => t.stop());
    sharedStream = null;
  }
  if (sharedModel) {
    try { sharedModel.dispose?.(); } catch { /* ignore */ }
    sharedModel = null;
    sharedModelLoaded = false;
  }
  sharedVideo = null;
  notifyCountListeners(0);
  notifyStateListeners();
}

export function useCameraMonitor(
  enabled: boolean,
  onPersonCountChange: (count: number) => void,
): UseCameraMonitorResult {
  const videoRef = useRef<HTMLVideoElement>(null!);
  const [personCount, setPersonCount] = useState(sharedPersonCount);
  const [isRunning, setIsRunning] = useState(sharedIsRunning);
  const [modelLoaded, setModelLoaded] = useState(sharedModelLoaded);
  const [error, setError] = useState<string | null>(null);

  // Sync with shared state
  useEffect(() => {
    sharedListeners.add(onPersonCountChange);
    const syncState = () => {
      setIsRunning(sharedIsRunning);
      setModelLoaded(sharedModelLoaded);
      setPersonCount(sharedPersonCount);
    };
    sharedStateListeners.add(syncState);
    syncState();
    return () => {
      sharedListeners.delete(onPersonCountChange);
      sharedStateListeners.delete(syncState);
    };
  }, [onPersonCountChange]);

  // Attach stream to video element when component mounts/remounts.
  // On unmount, only detach the DOM element — camera and model stay alive.
  useEffect(() => {
    if (sharedStream && sharedIsRunning && videoRef.current) {
      sharedVideo = videoRef.current;
      sharedVideo.srcObject = sharedStream;
      sharedVideo.play().catch(() => { /* ignore autoplay block */ });
    }
    return () => {
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
      sharedVideo = null;
    };
  }, []);

  const start = useCallback(async () => {
    try {
      setError(null);
      if (!sharedStream) {
        // Request 1080p for maximum coverage; browser falls back to best available
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: 'user',
            width: { ideal: 1920 },
            height: { ideal: 1080 },
          },
          audio: false,
        });
        sharedStream = stream;
      }
      if (videoRef.current) {
        sharedVideo = videoRef.current;
        sharedVideo.srcObject = sharedStream;
        await sharedVideo.play();
      }
      const model = await loadModelShared();
      if (!model) {
        if (sharedStream) {
          sharedStream.getTracks().forEach((t) => t.stop());
          sharedStream = null;
        }
        sharedVideo = null;
        setError('模型加载失败');
        return;
      }
      sharedIsRunning = true;
      notifyStateListeners();
      ensureDetectionLoop();
    } catch (e: any) {
      setError('摄像头访问失败: ' + e.message);
    }
  }, []);

  const stop = useCallback(() => {
    stopShared();
  }, []);

  // Cleanup on app unload only (not on component unmount)
  useEffect(() => {
    return () => {
      stopShared();
    };
  }, []);

  return { videoRef, personCount, isRunning, modelLoaded, error, start, stop };
}
