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

  // Auto-start camera on first mount if monitor is enabled.
  // On remount (tab switch / sidebar toggle), the hook's mount effect
  // re-attaches the existing stream to the new video element — no need to call start() again.
  useEffect(() => {
    if (monitorEnabled && !isRunning) {
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
        <div className="monitor-section-title">摄像头</div>
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
              摄像头未开启
            </div>
          )}
        </div>
        {error && <div className="monitor-error">{error}</div>}
      </div>

      {/* Controls */}
      <div className="monitor-section">
        <div className="monitor-section-title">状态</div>
        <div className="monitor-status-row">
          <span className={`monitor-dot ${isRunning ? 'active' : ''}`} />
          <span>{isRunning ? '运行中' : '已停止'}</span>
          {isRunning && (
            <span className="monitor-count">
              检测人数: <strong className={personCount >= 2 ? 'danger' : ''}>{personCount}</strong>
            </span>
          )}
        </div>
        {!modelLoaded && isRunning && (
          <div className="monitor-loading">正在加载模型...</div>
        )}
        {monitorCooldown && (
          <div className="monitor-cooldown">冷却中...</div>
        )}
        <button className="monitor-btn" onClick={handleToggle}>
          {monitorEnabled ? '停止监控' : '启动监控'}
        </button>
      </div>

      {/* Action Settings */}
      <div className="monitor-section">
        <div className="monitor-section-title">检测到 2 人及以上时执行</div>
        <div className="monitor-radio-group">
          <label className="monitor-radio">
            <input
              type="radio"
              name="monitor-action"
              value="minimize"
              checked={monitorAction === 'minimize'}
              onChange={() => setMonitorAction('minimize')}
            />
            <span>最小化当前窗口</span>
          </label>
          <label className="monitor-radio">
            <input
              type="radio"
              name="monitor-action"
              value="close"
              checked={monitorAction === 'close'}
              onChange={() => setMonitorAction('close')}
            />
            <span>关闭本应用</span>
          </label>
          <label className="monitor-radio">
            <input
              type="radio"
              name="monitor-action"
              value="kill"
              checked={monitorAction === 'kill'}
              onChange={() => setMonitorAction('kill')}
            />
            <span>结束指定进程 + 最小化</span>
          </label>
        </div>
      </div>

      {/* Target Processes */}
      <div className="monitor-section">
        <div className="monitor-section-title">
          目标进程
          <button
            className="monitor-refresh-btn"
            onClick={loadProcesses}
            disabled={loadingProcesses}
            title="刷新进程列表"
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
              placeholder="搜索进程..."
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
            点击 ↻ 加载系统进程列表
          </div>
        )}
      </div>
    </div>
  );
}
