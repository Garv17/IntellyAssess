import { useEffect, useRef, useState } from 'react';
import { tokens, type LiveMonitor } from '../api';
import { log } from '../logger';

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
      // The URL carries the access token as a query parameter (browsers can't
      // set headers on a WebSocket handshake) — never log it.
      log.info('live monitor socket connecting', { exam_id: examId });
      socket = new WebSocket(url);
      socket.onopen = () => {
        log.info('live monitor socket open', { exam_id: examId });
        setStatus('open');
      };
      socket.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data?.type === 'ping') return;
        onSnapshotRef.current(data);
      };
      socket.onclose = (e) => {
        log.info('live monitor socket closed', {
          exam_id: examId,
          code: e.code,
          clean: e.wasClean,
          will_retry: !closed,
        });
        setStatus('offline');
        if (!closed) retryTimer = window.setTimeout(connect, 5000);
      };
      // The browser's error event carries no detail by design; the close that
      // follows it is where the code actually is. Logged anyway so a monitor
      // stuck in a connect/error/retry loop is visible as more than silence.
      socket.onerror = () => {
        log.warn('live monitor socket error', { exam_id: examId });
        socket?.close();
      };
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
