import { Clock3, Download, Eye, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, downloadResults, type LiveMonitor as Monitor } from '../../api';
import { formatClock } from '../../hooks/useExamTimer';

export default function LiveMonitor() {
  const { examId = '' } = useParams();
  const [data, setData] = useState<Monitor | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'in_progress' | 'not_started' | 'submitted'>('all');

  // Polled rather than websocket-driven: one 5 s request per admin is ~1 rps total,
  // and polling degrades gracefully when the network hiccups.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api.liveMonitor(examId);
        if (!cancelled) {
          setData(next);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Refresh failed');
      }
    };
    void tick();
    const interval = window.setInterval(() => void tick(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [examId]);

  const rows = (data?.rows ?? []).filter((row) => {
    if (filter === 'all') return true;
    if (filter === 'not_started') return row.attempt_status === null;
    if (filter === 'in_progress') return row.attempt_status === 'in_progress';
    return row.attempt_status !== null && row.attempt_status !== 'in_progress';
  });

  return (
    <div className="shell">
      <header className="top-bar">
        <strong>
          <Link to="/admin" className="btn link">
            ← Exams
          </Link>{' '}
          Live monitor {data && `· ${data.title}`}
        </strong>
        <button className="btn" onClick={() => void downloadResults(examId)}>
          <Download size={15} /> Export results
        </button>
      </header>

      <main className="container wide">
        {error && <div className="banner error">{error}</div>}

        <div className="stat-row">
          <Stat label="Total students" value={data?.total_students ?? 0} icon={<Users size={17} />} />
          <Stat label="Not started" value={data?.not_started ?? 0} tone="neutral" />
          <Stat label="In progress" value={data?.in_progress ?? 0} tone="active" live />
          <Stat label="Submitted" value={data?.submitted ?? 0} tone="ok" />
        </div>

        <div className="chip-row">
          {(['all', 'in_progress', 'not_started', 'submitted'] as const).map((f) => (
            <button
              key={f}
              className={`chip ${filter === f ? 'active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f.replace('_', ' ')}
            </button>
          ))}
          <span className="muted small" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <span className="live-dot" /> Refreshes every 5s
            {data && ` · server ${new Date(data.server_time).toLocaleTimeString()}`}
          </span>
        </div>

        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Enrollment ID</th>
                <th>Name</th>
                <th>Status</th>
                <th>Time left</th>
                <th>Answered</th>
                <th>Focus losses</th>
                <th>Submitted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.student_id}>
                  <td>{row.student_id}</td>
                  <td>{row.name}</td>
                  <td>
                    <span className={`tag ${row.attempt_status ?? 'none'}`}>
                      {row.attempt_status ?? 'not started'}
                    </span>
                  </td>
                  <td className={row.seconds_remaining !== null && row.seconds_remaining < 300 ? 'urgent' : ''}>
                    {row.seconds_remaining !== null ? (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
                        <Clock3 size={13} />
                        {formatClock(row.seconds_remaining)}
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>{row.answered_count}</td>
                  <td className={row.focus_loss_count > 5 ? 'urgent' : ''}>
                    {row.focus_loss_count}
                  </td>
                  <td>
                    {row.submitted_at ? new Date(row.submitted_at).toLocaleTimeString() : '—'}
                  </td>
                  <td>
                    {row.attempt_id && (
                      <Link className="btn ghost small" to={`/admin/student-details/${row.attempt_id}`}>
                        <Eye size={14} /> View
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted">
                    No students match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <p className="muted small">
          Answered counts can lag by a few seconds — auto-saved answers are flushed from the
          buffer in batches. Focus losses are advisory signals for review, not evidence.
        </p>
      </main>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = 'neutral',
  icon,
  live,
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'active' | 'ok';
  icon?: React.ReactNode;
  live?: boolean;
}) {
  return (
    <div className={`stat ${tone}`}>
      {icon && <div className="stat-icon">{icon}</div>}
      <strong>
        {live && <span className="live-dot" style={{ marginRight: 6 }} />}
        {value}
      </strong>
      <span>{label}</span>
    </div>
  );
}
