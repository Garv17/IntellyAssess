import { useEffect, useRef, useState } from 'react';
import { tokens, type LiveMonitor } from '../api';

export type SocketStatus = 'connecting' | 'open' | 'offline';

/**
 * Push updates for the admin Live Monitor. Reconnects on a flat 5s timer (no
 * backoff) to match this app's existing polling/retry idiom elsewhere
 * (auto-save's safety interval, the exam timer heartbeat).
 */
export function useLiveMonitorSocket(
  examId: string,
  onSnapshot: (data: LiveMonitor) => void,
): SocketStatus {
  const [status, setStatus] = useState<SocketStatus>('connecting');
  const onSnapshotRef = useRef(onSnapshot);
  onSnapshotRef.current = onSnapshot;

  useEffect(() => {
    let closed = false;
    let socket: WebSocket | null = null;
    let retryTimer: number | null = null;

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${window.location.host}/api/admin/exams/${examId}/live/ws?token=${encodeURIComponent(tokens.access ?? '')}`;
      setStatus('connecting');
      socket = new WebSocket(url);
      socket.onopen = () => setStatus('open');
      socket.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data?.type === 'ping') return;
        onSnapshotRef.current(data);
      };
      socket.onclose = () => {
        setStatus('offline');
        if (!closed) retryTimer = window.setTimeout(connect, 5000);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [examId]);

  return status;
}
