import { LifeBuoy, RefreshCw, Send } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type RecoveryCandidate, type RecoveryReason } from '../../api';
import ConfirmDialog from '../../components/ConfirmDialog';
import EmptyState from '../../components/EmptyState';
import { SkeletonTable } from '../../components/Skeleton';
import { useToast } from '../../components/Toast';
import { formatClock } from '../../hooks/useExamTimer';

const REASONS: { value: RecoveryReason; label: string }[] = [
  { value: 'UNEXPECTED_AUTO_SUBMIT', label: 'Unexpected Auto Submit' },
  { value: 'NETWORK_ISSUE', label: 'Network Issue' },
  { value: 'BROWSER_CRASH', label: 'Browser Crash' },
  { value: 'SYSTEM_RESTART', label: 'System Restart' },
  { value: 'SESSION_ISSUE', label: 'Session Issue' },
  { value: 'SERVER_OR_API_ISSUE', label: 'Server/API Issue' },
  { value: 'EXAM_PAGE_CLOSED', label: 'Exam Page Closed' },
  { value: 'OTHER', label: 'Other' },
];

type PendingAction = 'send_resume_link' | 'resume_now';

export default function ExamRecovery() {
  const { examId = '' } = useParams();
  const toast = useToast();
  const [rows, setRows] = useState<RecoveryCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState<RecoveryReason>('UNEXPECTED_AUTO_SUBMIT');
  const [note, setNote] = useState('');
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [singleAttemptId, setSingleAttemptId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .recoveryCandidates(examId)
      .then((data) => {
        setRows(data);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load recoverable attempts'))
      .finally(() => setLoading(false));
  };

  useEffect(load, [examId]);

  const eligible = useMemo(() => rows.filter((r) => r.remaining_seconds > 0 && r.reopen_count < 10), [rows]);
  const allEligibleSelected = eligible.length > 0 && eligible.every((r) => selected.has(r.attempt_id));

  const toggleAll = () => {
    if (allEligibleSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(eligible.map((r) => r.attempt_id)));
    }
  };

  const toggleOne = (attemptId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(attemptId)) next.delete(attemptId);
      else next.add(attemptId);
      return next;
    });
  };

  const openBulk = (action: PendingAction) => {
    if (selected.size === 0) return;
    setPendingAction(action);
    setSingleAttemptId(null);
  };

  const openSingle = (attemptId: string, action: PendingAction) => {
    setSingleAttemptId(attemptId);
    setPendingAction(action);
  };

  const closeDialog = () => {
    setPendingAction(null);
    setSingleAttemptId(null);
  };

  const targetIds = singleAttemptId ? [singleAttemptId] : Array.from(selected);
  const targetRows = rows.filter((r) => targetIds.includes(r.attempt_id));

  const confirm = async () => {
    if (!pendingAction) return;
    if (reason === 'OTHER' && !note.trim()) {
      toast.error('A note is required when reason is Other.');
      return;
    }
    setBusy(true);
    try {
      const result = await api.bulkRecovery({
        attempt_ids: targetIds,
        action: pendingAction,
        reason,
        note: note.trim() || undefined,
      });
      const failures = result.results.filter((r) => !r.success);
      if (failures.length === 0) {
        toast.success(
          pendingAction === 'resume_now'
            ? `Resumed ${result.successful} attempt${result.successful === 1 ? '' : 's'}.`
            : `Sent ${result.successful} resume link${result.successful === 1 ? '' : 's'}.`,
        );
      } else {
        toast.error(
          `${result.successful} succeeded, ${failures.length} failed: ${failures
            .map((f) => f.reason)
            .filter(Boolean)
            .join('; ')}`,
        );
      }
      setSelected(new Set());
      closeDialog();
      load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Recovery request failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell">
      <header className="top-bar">
        <strong>
          <Link to="/admin" className="btn link">
            ← Exams
          </Link>{' '}
          Exam recovery
        </strong>
        <button className="btn" onClick={load} disabled={loading}>
          <RefreshCw size={15} /> Refresh
        </button>
      </header>

      <main className="container wide">
        {error && <div className="banner error">{error}</div>}

        <section className="card">
          <p className="muted small">
            Temporary safeguard for students whose exam was interrupted (auto-submit, network,
            browser crash, session issue, etc). Resuming restores their existing attempt, answers,
            and the exact time they had left — it never restarts the exam.
          </p>

          <div className="filter-bar">
            <label>
              Reason
              <select value={reason} onChange={(e) => setReason(e.target.value as RecoveryReason)}>
                {REASONS.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="grow">
              Note {reason === 'OTHER' && <span className="muted small">(required)</span>}
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Optional context for the audit trail"
              />
            </label>
            <button className="btn primary" disabled={selected.size === 0} onClick={() => openBulk('send_resume_link')}>
              <Send size={15} /> Send Resume Links ({selected.size})
            </button>
            <button className="btn" disabled={selected.size === 0} onClick={() => openBulk('resume_now')}>
              <LifeBuoy size={15} /> Resume Now ({selected.size})
            </button>
          </div>
        </section>

        <section className="card">
          {loading ? (
            <SkeletonTable rows={5} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<LifeBuoy size={24} />}
              title="No recoverable attempts"
              description="No auto-submitted attempts were found for this exam."
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>
                      <input type="checkbox" checked={allEligibleSelected} onChange={toggleAll} />
                    </th>
                    <th>Student</th>
                    <th>Status</th>
                    <th>Time used</th>
                    <th>Remaining</th>
                    <th>Answered</th>
                    <th>Recoveries</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const capped = row.reopen_count >= 10;
                    const canRecover = row.remaining_seconds > 0 && !capped;
                    return (
                      <tr key={row.attempt_id}>
                        <td>
                          <input
                            type="checkbox"
                            disabled={!canRecover}
                            checked={selected.has(row.attempt_id)}
                            onChange={() => toggleOne(row.attempt_id)}
                          />
                        </td>
                        <td>
                          {row.student_name}
                          <div className="muted small">{row.student_id}</div>
                        </td>
                        <td>
                          <span className="tag auto_submitted">Auto Submitted</span>
                        </td>
                        <td>{formatClock(row.time_used_seconds)}</td>
                        <td>{canRecover ? formatClock(row.remaining_seconds) : '—'}</td>
                        <td>{row.answered_count}</td>
                        <td>
                          {capped ? (
                            <span className="muted small">Max reached (10/10)</span>
                          ) : (
                            `${row.reopen_count} of 10`
                          )}
                        </td>
                        <td>
                          {canRecover ? (
                            <button className="btn small" onClick={() => openSingle(row.attempt_id, 'resume_now')}>
                              Resume
                            </button>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>

      {pendingAction && (
        <ConfirmDialog
          title={pendingAction === 'resume_now' ? 'Resume attempts now?' : 'Send resume links?'}
          tone="info"
          busy={busy}
          confirmLabel={pendingAction === 'resume_now' ? 'Resume Now' : 'Send Resume Links'}
          onConfirm={() => void confirm()}
          onCancel={closeDialog}
          description={
            <div>
              <p>
                {targetRows.length} student{targetRows.length === 1 ? '' : 's'} selected
              </p>
              <ul>
                {targetRows.map((r) => (
                  <li key={r.attempt_id}>
                    {r.student_name} → {formatClock(r.remaining_seconds)} remaining
                  </li>
                ))}
              </ul>
            </div>
          }
        />
      )}
    </div>
  );
}
