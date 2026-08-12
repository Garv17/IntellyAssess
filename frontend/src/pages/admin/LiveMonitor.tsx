import { ArrowDown, ArrowUp, ArrowUpDown, Clock3, Download, Eye, Search, Users } from 'lucide-react';
import { memo, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, downloadResults, type LiveMonitor as Monitor, type LiveRow } from '../../api';
import { useLiveMonitorSocket } from '../../hooks/useLiveMonitorSocket';
import { formatClock } from '../../hooks/useExamTimer';

type SortKey = 'name' | 'seconds_remaining' | 'answered_count' | 'focus_loss_count';

export default function LiveMonitor() {
  const { examId = '' } = useParams();
  const [data, setData] = useState<Monitor | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'in_progress' | 'not_started' | 'submitted'>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey | null>(null);
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');

  // First paint via REST; every update after that arrives over the socket below.
  useEffect(() => {
    let cancelled = false;
    api
      .liveMonitor(examId)
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Load failed');
      });
    return () => {
      cancelled = true;
    };
  }, [examId]);

  const status = useLiveMonitorSocket(examId, (next) => {
    setData(next);
    setError(null);
  });

  const toggleSort = (key: SortKey) => {
    if (sort === key) {
      setOrder(order === 'asc' ? 'desc' : 'asc');
    } else {
      setSort(key);
      setOrder('desc');
    }
  };

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sort !== column) return <ArrowUpDown size={12} className="sort-icon" />;
    return order === 'asc' ? <ArrowUp size={12} className="sort-icon" /> : <ArrowDown size={12} className="sort-icon" />;
  };

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    let filtered = (data?.rows ?? []).filter((row) => {
      if (filter === 'not_started' && row.attempt_status !== null) return false;
      if (filter === 'in_progress' && row.attempt_status !== 'in_progress') return false;
      if (filter === 'submitted' && (row.attempt_status === null || row.attempt_status === 'in_progress')) return false;
      if (!q) return true;
      return row.student_id.toLowerCase().includes(q) || row.name.toLowerCase().includes(q);
    });
    if (sort) {
      filtered = [...filtered].sort((a, b) => {
        const va = a[sort] ?? -1;
        const vb = b[sort] ?? -1;
        const cmp = typeof va === 'string' ? va.localeCompare(vb as string) : (va as number) - (vb as number);
        return order === 'asc' ? cmp : -cmp;
      });
    }
    return filtered;
  }, [data, filter, search, sort, order]);

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
        {status === 'offline' && (
          <div className="banner error">Live updates disconnected. Showing last known state.</div>
        )}

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
          <label className="search-box">
            <Search size={14} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name or enrollment ID"
            />
          </label>
          <span className={`save-indicator ${status === 'open' ? 'saved' : status === 'connecting' ? 'pending' : 'offline'}`} style={{ marginLeft: 'auto' }}>
            <span className="live-dot" />
            {status === 'open' ? 'Live' : status === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
            {data && ` · server ${new Date(data.server_time).toLocaleTimeString()}`}
          </span>
        </div>

        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Enrollment ID</th>
                <th className="sortable" onClick={() => toggleSort('name')}>
                  Name <SortIcon column="name" />
                </th>
                <th>Status</th>
                <th className="sortable" onClick={() => toggleSort('seconds_remaining')}>
                  Time left <SortIcon column="seconds_remaining" />
                </th>
                <th className="sortable" onClick={() => toggleSort('answered_count')}>
                  Answered <SortIcon column="answered_count" />
                </th>
                <th className="sortable" onClick={() => toggleSort('focus_loss_count')}>
                  Tab switches <SortIcon column="focus_loss_count" />
                </th>
                <th>Submitted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <LiveMonitorRow
                  key={row.student_id}
                  attemptId={row.attempt_id}
                  studentId={row.student_id}
                  name={row.name}
                  attemptStatus={row.attempt_status}
                  secondsRemaining={row.seconds_remaining}
                  answeredCount={row.answered_count}
                  focusLossCount={row.focus_loss_count}
                  submittedAt={row.submitted_at}
                />
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted">
                    {search ? `No students match "${search}".` : 'No students match this filter.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <p className="muted small">
          Answered counts can lag by up to 2 minutes; auto-saved answers are flushed from the
          buffer in batches. Tab switches are advisory signals for review, not evidence.
        </p>
      </main>
    </div>
  );
}

const LiveMonitorRow = memo(function LiveMonitorRow({
  attemptId,
  studentId,
  name,
  attemptStatus,
  secondsRemaining,
  answeredCount,
  focusLossCount,
  submittedAt,
}: {
  attemptId: LiveRow['attempt_id'];
  studentId: LiveRow['student_id'];
  name: LiveRow['name'];
  attemptStatus: LiveRow['attempt_status'];
  secondsRemaining: LiveRow['seconds_remaining'];
  answeredCount: LiveRow['answered_count'];
  focusLossCount: LiveRow['focus_loss_count'];
  submittedAt: LiveRow['submitted_at'];
}) {
  return (
    <tr>
      <td>{studentId}</td>
      <td>{name}</td>
      <td>
        <span className={`tag ${attemptStatus ?? 'none'}`}>{attemptStatus ?? 'not started'}</span>
      </td>
      <td className={secondsRemaining !== null && secondsRemaining < 300 ? 'urgent' : ''}>
        {secondsRemaining !== null ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
            <Clock3 size={13} />
            {formatClock(secondsRemaining)}
          </span>
        ) : (
          '—'
        )}
      </td>
      <td>{answeredCount}</td>
      <td className={focusLossCount > 5 ? 'urgent' : ''}>{focusLossCount}</td>
      <td>{submittedAt ? new Date(submittedAt).toLocaleTimeString() : '—'}</td>
      <td>
        {attemptId && (
          <Link className="btn ghost small" to={`/admin/student-details/${attemptId}`}>
            <Eye size={14} /> View
          </Link>
        )}
      </td>
    </tr>
  );
});

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
