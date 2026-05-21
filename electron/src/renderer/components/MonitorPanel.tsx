import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useChatStore } from '../store/chatStore';
import { useCameraMonitor } from '../hooks/useCameraMonitor';
import { ProcessInfo } from '../../shared/protocol';

export default function MonitorPanel() {
  const {
    monitorEnabled, setMonitorEnabled,
    setMonitorPersonCount,
    monitorTargetProcesses, addMonitorTargetProcess, removeMonitorTargetProcess,
    monitorAction, setMonitorAction,
    monitorCooldown, setMonitorCooldown,
  } = useChatStore();

  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [processFilter, setProcessFilter] = useState('');
  const [loadingProcesses, setLoadingProcesses] = useState(false);
  const cooldownRef = useRef(false);
  const cooldownTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup cooldown timer on unmount
  useEffect(() => {
    return () => {
      if (cooldownTimerRef.current) {
        clearTimeout(cooldownTimerRef.current);
      }
    };
  }, []);

  const handlePersonCount = useCallback((count: number) => {
    setMonitorPersonCount(count);
  }, [setMonitorPersonCount]);

  const { videoRef, personCount, isRunning, modelLoaded, error, start, stop } =
    useCameraMonitor(monitorEnabled, handlePersonCount);

  // Auto-start camera when component mounts with monitor already enabled,
  // or re-attach detection loop when remounting while already running.
  useEffect(() => {
    if (monitorEnabled && !isRunning) {
      start();
    }
    // If the monitor was already running (module-level state) but this component
    // just mounted, the detection loop needs the new video element reference.
    // The hook's mount effect re-attaches the stream; we restart detection via start().
    if (monitorEnabled && isRunning && videoRef.current && !videoRef.current.srcObject) {
      start();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Trigger action when 2+ people detected
  useEffect(() => {
    if (!monitorEnabled || !isRunning || monitorCooldown || cooldownRef.current) return;
    if (personCount < 2) return;

    cooldownRef.current = true;
    setMonitorCooldown(true);

    const executeAction = async () => {
      if (monitorAction === 'minimize') {
        await window.api.minimizeWindow();
      } else if (monitorAction === 'close') {
        await window.api.closeApp();
      } else if (monitorAction === 'kill') {
        for (const proc of monitorTargetProcesses) {
          await window.api.killProcess(proc);
        }
        await window.api.minimizeWindow();
      }
    };

    executeAction();

    // Cooldown: avoid repeated triggers for 10 seconds
    cooldownTimerRef.current = setTimeout(() => {
      cooldownRef.current = false;
      setMonitorCooldown(false);
      cooldownTimerRef.current = null;
    }, 10000);
  }, [personCount, monitorEnabled, isRunning, monitorAction, monitorTargetProcesses, monitorCooldown, setMonitorCooldown]);

  const handleToggle = async () => {
    if (monitorEnabled) {
      stop();
      setMonitorEnabled(false);
    } else {
      setMonitorEnabled(true);
      await start();
    }
  };

  const loadProcesses = async () => {
    setLoadingProcesses(true);
    try {
      const list = await window.api.getProcesses();
      setProcesses(list);
    } catch {
      // ignore
    } finally {
      setLoadingProcesses(false);
    }
  };

  const filteredProcesses = processes.filter((p) =>
    p.name.toLowerCase().includes(processFilter.toLowerCase())
  );

  return (
    <div className="monitor-panel">
      {/* Camera Preview */}
      <div className="monitor-section">
        <div className="monitor-section-title">Camera</div>
        <div className="monitor-preview">
          <video
            ref={videoRef}
            className="monitor-video"
            muted
            playsInline
            style={{ display: isRunning ? 'block' : 'none' }}
          />
          {!isRunning && (
            <div className="monitor-preview-placeholder">
              Camera Off
            </div>
          )}
        </div>
        {error && <div className="monitor-error">{error}</div>}
      </div>

      {/* Controls */}
      <div className="monitor-section">
        <div className="monitor-section-title">Status</div>
        <div className="monitor-status-row">
          <span className={`monitor-dot ${isRunning ? 'active' : ''}`} />
          <span>{isRunning ? 'Running' : 'Stopped'}</span>
          {isRunning && (
            <span className="monitor-count">
              Persons: <strong className={personCount >= 2 ? 'danger' : ''}>{personCount}</strong>
            </span>
          )}
        </div>
        {!modelLoaded && isRunning && (
          <div className="monitor-loading">Loading model...</div>
        )}
        {monitorCooldown && (
          <div className="monitor-cooldown">Cooldown active...</div>
        )}
        <button className="monitor-btn" onClick={handleToggle}>
          {monitorEnabled ? 'Stop Monitor' : 'Start Monitor'}
        </button>
      </div>

      {/* Action Settings */}
      <div className="monitor-section">
        <div className="monitor-section-title">Action on 2+ Persons</div>
        <div className="monitor-radio-group">
          <label className="monitor-radio">
            <input
              type="radio"
              name="monitor-action"
              value="minimize"
              checked={monitorAction === 'minimize'}
              onChange={() => setMonitorAction('minimize')}
            />
            <span>Minimize Window</span>
          </label>
          <label className="monitor-radio">
            <input
              type="radio"
              name="monitor-action"
              value="close"
              checked={monitorAction === 'close'}
              onChange={() => setMonitorAction('close')}
            />
            <span>Close App</span>
          </label>
          <label className="monitor-radio">
            <input
              type="radio"
              name="monitor-action"
              value="kill"
              checked={monitorAction === 'kill'}
              onChange={() => setMonitorAction('kill')}
            />
            <span>Kill Processes + Minimize</span>
          </label>
        </div>
      </div>

      {/* Target Processes */}
      <div className="monitor-section">
        <div className="monitor-section-title">
          Target Processes
          <button
            className="monitor-refresh-btn"
            onClick={loadProcesses}
            disabled={loadingProcesses}
            title="Refresh process list"
          >
            {loadingProcesses ? '...' : '↻'}
          </button>
        </div>

        {/* Current targets */}
        {monitorTargetProcesses.length > 0 && (
          <div className="monitor-targets">
            {monitorTargetProcesses.map((name) => (
              <div key={name} className="monitor-target-tag">
                <span>{name}</span>
                <button
                  className="monitor-target-remove"
                  onClick={() => removeMonitorTargetProcess(name)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Process browser */}
        {processes.length > 0 && (
          <>
            <input
              className="monitor-filter-input"
              placeholder="Filter processes..."
              value={processFilter}
              onChange={(e) => setProcessFilter(e.target.value)}
            />
            <div className="monitor-process-list">
              {filteredProcesses.map((p) => (
                <div
                  key={p.name}
                  className={`monitor-process-item ${monitorTargetProcesses.includes(p.name) ? 'selected' : ''}`}
                  onClick={() => {
                    if (monitorTargetProcesses.includes(p.name)) {
                      removeMonitorTargetProcess(p.name);
                    } else {
                      addMonitorTargetProcess(p.name);
                    }
                  }}
                >
                  <span className="monitor-process-name">{p.name}</span>
                  <span className="monitor-process-pid">PID {p.pid}</span>
                </div>
              ))}
            </div>
          </>
        )}
        {processes.length === 0 && (
          <div className="monitor-hint">
            Click ↻ to load system processes
          </div>
        )}
      </div>
    </div>
  );
}
