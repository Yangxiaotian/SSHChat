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

// Module-level state: persists across component mounts/unmounts (tab switches)
let sharedStream: MediaStream | null = null;
let sharedModel: any = null;
let sharedModelLoaded = false;
let sharedRafId = 0;
let sharedIsRunning = false;
let sharedPersonCount = 0;
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

function startDetectionLoop(video: HTMLVideoElement) {
  // Cancel any existing loop first to prevent duplicates
  if (sharedRafId) {
    cancelAnimationFrame(sharedRafId);
    sharedRafId = 0;
  }
  const detect = async () => {
    if (!video || !sharedModel || video.readyState < 2) {
      sharedRafId = requestAnimationFrame(detect);
      return;
    }
    const predictions: { class: string; score: number }[] = await sharedModel.detect(video);
    const count = predictions.filter((p) => p.class === 'person' && p.score > 0.5).length;
    notifyCountListeners(count);
    if (sharedIsRunning) {
      sharedRafId = requestAnimationFrame(detect);
    }
  };
  sharedRafId = requestAnimationFrame(detect);
}

function stopShared() {
  if (sharedRafId) {
    cancelAnimationFrame(sharedRafId);
    sharedRafId = 0;
  }
  if (sharedStream) {
    sharedStream.getTracks().forEach((t) => t.stop());
    sharedStream = null;
  }
  if (sharedModel) {
    try { sharedModel.dispose?.(); } catch { /* ignore */ }
    sharedModel = null;
    sharedModelLoaded = false;
  }
  sharedIsRunning = false;
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
  const mountedRef = useRef(false);

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

  // Re-attach stream to video element when component mounts/remounts
  useEffect(() => {
    mountedRef.current = true;
    if (sharedStream && videoRef.current && sharedIsRunning) {
      videoRef.current.srcObject = sharedStream;
      videoRef.current.play().catch(() => { /* ignore autoplay block */ });
    }
    return () => {
      mountedRef.current = false;
      // Detach stream from video element but don't stop it
      if (videoRef.current) {
        videoRef.current.srcObject = null;
      }
    };
  }, []);

  const start = useCallback(async () => {
    try {
      setError(null);
      if (!sharedStream) {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'user', width: 640, height: 480 },
          audio: false,
        });
        sharedStream = stream;
      }
      if (videoRef.current && mountedRef.current) {
        videoRef.current.srcObject = sharedStream;
        await videoRef.current.play();
      }
      const model = await loadModelShared();
      if (!model) {
        if (sharedStream) {
          sharedStream.getTracks().forEach((t) => t.stop());
          sharedStream = null;
        }
        setError('Failed to load detection model');
        return;
      }
      sharedIsRunning = true;
      notifyStateListeners();
      if (videoRef.current && mountedRef.current) {
        startDetectionLoop(videoRef.current);
      }
    } catch (e: any) {
      setError('Camera access denied: ' + e.message);
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
