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

export function useCameraMonitor(
  enabled: boolean,
  onPersonCountChange: (count: number) => void,
): UseCameraMonitorResult {
  const videoRef = useRef<HTMLVideoElement>(null!);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number>(0);
  const modelRef = useRef<any>(null);
  const [personCount, setPersonCount] = useState(0);
  const [isRunning, setIsRunning] = useState(false);
  const [modelLoaded, setModelLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const loadModel = useCallback(async () => {
    if (modelRef.current) return modelRef.current;
    try {
      const tf = await import('@tensorflow/tfjs');
      // Disable WebGL caching to disk
      await tf.setBackend('webgl');
      const cocoSsd = await import('@tensorflow-models/coco-ssd');
      // Load model from local files — no CDN request
      const model = await cocoSsd.load({
        base: 'lite_mobilenet_v2',
        modelUrl: './models/ssdlite_mobilenet_v2/model.json',
      });
      modelRef.current = model;
      setModelLoaded(true);
      return model;
    } catch (e: any) {
      setError('Failed to load detection model: ' + e.message);
      return null;
    }
  }, []);

  const detectFrame = useCallback(async () => {
    const video = videoRef.current;
    const model = modelRef.current;
    if (!video || !model || video.readyState < 2) {
      rafRef.current = requestAnimationFrame(detectFrame);
      return;
    }

    // Run person detection — no camera data is saved anywhere
    const predictions: { class: string; score: number }[] = await model.detect(video);
    const count = predictions.filter((p) => p.class === 'person' && p.score > 0.5).length;

    setPersonCount(count);
    onPersonCountChange(count);

    if (enabledRef.current) {
      rafRef.current = requestAnimationFrame(detectFrame);
    }
  }, [onPersonCountChange]);

  const start = useCallback(async () => {
    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: 640, height: 480 },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      const model = await loadModel();
      if (!model) {
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        if (videoRef.current) videoRef.current.srcObject = null;
        return;
      }
      setIsRunning(true);
      rafRef.current = requestAnimationFrame(detectFrame);
    } catch (e: any) {
      setError('Camera access denied: ' + e.message);
    }
  }, [loadModel, detectFrame]);

  const stop = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    // Clear TF.js tensors to free GPU memory
    if (modelRef.current) {
      try { modelRef.current.dispose?.(); } catch { /* ignore */ }
      modelRef.current = null;
      setModelLoaded(false);
    }
    setIsRunning(false);
    setPersonCount(0);
    onPersonCountChange(0);
  }, [onPersonCountChange]);

  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  return { videoRef, personCount, isRunning, modelLoaded, error, start, stop };
}
