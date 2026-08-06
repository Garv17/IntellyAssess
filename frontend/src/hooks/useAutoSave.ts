import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, type AnswerSave } from '../api';

export type SaveStatus = 'idle' | 'pending' | 'saving' | 'saved' | 'offline';

interface Options {
  /** Debounce for keystroke-driven answers (code editor). */
  debounceMs?: number;
  /** Called when the server reports the exam window has closed. */
  onExpired?: () => void;
}

/**
 * Batching auto-save.
 *
 * Answers accumulate in a pending map keyed by question id, so rapid edits to the
 * same question collapse into one request instead of one per keystroke. The map is
 * only cleared after the server acknowledges — a failed flush is retried on the next
 * tick rather than dropped, which is what makes a flaky network survivable.
 */
export function useAutoSave({ debounceMs = 1200, onExpired }: Options = {}) {
  const pending = useRef<Map<string, AnswerSave>>(new Map());
  const timer = useRef<number | null>(null);
  const inFlight = useRef(false);
  const [status, setStatus] = useState<SaveStatus>('idle');
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);

  const flush = useCallback(async () => {
    if (inFlight.current || pending.current.size === 0) return;
    const batch = Array.from(pending.current.values());
    inFlight.current = true;
    setStatus('saving');
    try {
      await api.saveAnswers(batch);
      // Only drop entries that were part of this batch; anything edited during the
      // request stays pending for the next flush.
      for (const item of batch) {
        const current = pending.current.get(item.question_id);
        if (current && shallowEqual(current, item)) pending.current.delete(item.question_id);
      }
      setStatus(pending.current.size > 0 ? 'pending' : 'saved');
      setLastSavedAt(new Date());
    } catch (err) {
      if (err instanceof ApiError && err.status === 410) {
        onExpired?.();
        return;
      }
      setStatus('offline');
    } finally {
      inFlight.current = false;
    }
  }, [onExpired]);

  const queue = useCallback(
    (answer: AnswerSave, immediate = false) => {
      pending.current.set(answer.question_id, answer);
      setStatus('pending');
      if (timer.current) window.clearTimeout(timer.current);
      if (immediate) {
        void flush();
      } else {
        timer.current = window.setTimeout(() => void flush(), debounceMs);
      }
    },
    [debounceMs, flush],
  );

  // Periodic safety flush: catches anything a failed debounce left behind.
  useEffect(() => {
    const interval = window.setInterval(() => void flush(), 10_000);
    return () => window.clearInterval(interval);
  }, [flush]);

  // Best-effort flush when the tab goes away. Fired on visibilitychange rather than
  // beforeunload because mobile browsers frequently skip the latter.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === 'hidden') void flush();
    };
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('pagehide', onHide);
    return () => {
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('pagehide', onHide);
    };
  }, [flush]);

  return { queue, flush, status, lastSavedAt, pendingCount: () => pending.current.size };
}

function shallowEqual(a: AnswerSave, b: AnswerSave): boolean {
  return (
    a.selected_option_id === b.selected_option_id &&
    a.code_text === b.code_text &&
    a.language === b.language &&
    a.is_marked_for_review === b.is_marked_for_review
  );
}
