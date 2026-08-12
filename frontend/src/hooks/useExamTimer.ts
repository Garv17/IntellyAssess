import { useEffect, useRef, useState } from 'react';
import { api } from '../api';

/**
 * Countdown display, corrected against the server.
 *
 * The local interval only ticks the number down for smoothness; every heartbeat
 * replaces it with the server's authoritative value. A student who changes their
 * system clock, suspends the laptop, or throttles the tab still ends up with the
 * server's remaining time.
 */
export function useExamTimer(
  initialSeconds: number,
  onExpire: () => void,
  onTabSwitch?: () => void,
) {
  const [seconds, setSeconds] = useState(initialSeconds);
  const expired = useRef(false);
  const focusLosses = useRef(0);

  useEffect(() => {
    setSeconds(initialSeconds);
  }, [initialSeconds]);

  useEffect(() => {
    const tick = window.setInterval(() => {
      setSeconds((prev) => {
        const next = Math.max(0, prev - 1);
        if (next === 0 && !expired.current) {
          expired.current = true;
          onExpire();
        }
        return next;
      });
    }, 1000);
    return () => window.clearInterval(tick);
  }, [onExpire]);

  // Server sync + advisory focus-loss reporting.
  useEffect(() => {
    const sync = async () => {
      try {
        const reportFocus = focusLosses.current > 0;
        const hb = await api.heartbeat(reportFocus);
        if (reportFocus) focusLosses.current = 0;
        setSeconds(hb.seconds_remaining);
        if (hb.seconds_remaining <= 0 && !expired.current) {
          expired.current = true;
          onExpire();
        }
      } catch {
        // A failed heartbeat is not fatal — the local countdown carries on and the
        // server-side sweeper still guarantees submission.
      }
    };
    const interval = window.setInterval(() => void sync(), 30_000);
    void sync();
    return () => window.clearInterval(interval);
  }, [onExpire]);

  useEffect(() => {
    const onBlur = () => {
      if (document.visibilityState === 'hidden') {
        focusLosses.current += 1;
        onTabSwitch?.();
      }
    };
    document.addEventListener('visibilitychange', onBlur);
    return () => document.removeEventListener('visibilitychange', onBlur);
  }, [onTabSwitch]);

  return { seconds, formatted: formatClock(seconds) };
}

export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, totalSeconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}
