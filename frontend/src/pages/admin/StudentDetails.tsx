import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Download, Search, Users, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, downloadAttemptsExport, type AttemptFilters, type AttemptListPage, type Exam } from '../../api';
import AppShell from '../../components/AppShell';
import { Identity } from '../../components/Avatar';
import { ScoreBadge } from '../../components/Badge';
import EmptyState from '../../components/EmptyState';
import { SkeletonTable } from '../../components/Skeleton';

type SortKey = 'student_name' | 'exam_title' | 'score' | 'percentage' | 'started_at' | 'submitted_at';

const PAGE_SIZE = 20;

const EMPTY_DRAFT = {
  search: '',
  cohort: '',
  exam_id: '',
  status: '' as '' | 'completed' | 'pending',
  min_score: '',
  max_score: '',
  date_from: '',
  date_to: '',
};

function toAttemptFilters(query: typeof EMPTY_DRAFT): Omit<AttemptFilters, 'sort' | 'order' | 'page' | 'page_size'> {
  return {
    search: query.search || undefined,
    cohort: query.cohort || undefined,
    exam_id: query.exam_id || undefined,
    status: query.status || undefined,
    min_score: query.min_score ? Number(query.min_score) : undefined,
    max_score: query.max_score ? Number(query.max_score) : undefined,
    date_from: query.date_from || undefined,
    date_to: query.date_to || undefined,
  };
}

export default function StudentDetails() {
  const navigate = useNavigate();
  const [exams, setExams] = useState<Exam[]>([]);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [query, setQuery] = useState(EMPTY_DRAFT);
  const [showMore, setShowMore] = useState(false);
  const [sort, setSort] = useState<SortKey>('submitted_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AttemptListPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    void api.adminExams().then(setExams).catch(() => undefined);
  }, []);

  useEffect(() => {
    const filters: AttemptFilters = {
      ...toAttemptFilters(query),
      sort,
      order,
      page,
      page_size: PAGE_SIZE,
    };
    setLoading(true);
    api
      .attempts(filters)
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load attempts'))
      .finally(() => setLoading(false));
  }, [query, sort, order, page]);

  const applyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    setQuery(draft);
  };

  const clearFilters = () => {
    setDraft(EMPTY_DRAFT);
    setQuery(EMPTY_DRAFT);
    setPage(1);
  };

  const exportFiltered = async () => {
    setExporting(true);
    try {
      await downloadAttemptsExport({ ...toAttemptFilters(query), sort, order });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const toggleSort = (key: SortKey) => {
    if (sort === key) {
      setOrder(order === 'asc' ? 'desc' : 'asc');
    } else {
      setSort(key);
      setOrder('desc');
    }
    setPage(1);
  };

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sort !== column) return <ArrowUpDown size={12} className="sort-icon" />;
    return order === 'asc' ? <ArrowUp size={12} className="sort-icon" /> : <ArrowDown size={12} className="sort-icon" />;
  };

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const activeExam = exams.find((e) => e.id === query.exam_id);

  return (
    <AppShell title="Student Details" adminName="Admin">
      {error && <div className="banner error">{error}</div>}

      <section className="card">
        <form className="filter-bar" onSubmit={applyFilters}>
          <div className="filter-primary">
            <label className="grow">
              Search
              <input
                value={draft.search}
                onChange={(e) => setDraft({ ...draft, search: e.target.value })}
                placeholder="Name, email, enrollment ID"
              />
            </label>
            <label>
              Exam
              <select
                value={draft.exam_id}
                onChange={(e) => setDraft({ ...draft, exam_id: e.target.value })}
              >
                <option value="">All exams</option>
                {exams.map((exam) => (
                  <option key={exam.id} value={exam.id}>
                    {exam.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Status
              <select
                value={draft.status}
                onChange={(e) =>
                  setDraft({ ...draft, status: e.target.value as typeof draft.status })
                }
              >
                <option value="">Any</option>
                <option value="completed">Completed</option>
                <option value="pending">Pending</option>
              </select>
            </label>
            <button className="btn primary">
              <Search size={15} /> Apply
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => void exportFiltered()}
              disabled={exporting || loading}
            >
              <Download size={15} /> {exporting ? 'Exporting…' : 'Export to Excel'}
            </button>
          </div>

          <button
            type="button"
            className="filter-more-toggle"
            onClick={() => setShowMore((v) => !v)}
          >
            {showMore ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            More filters
          </button>

          {showMore && (
            <div className="filter-more">
              <label>
                Batch / cohort
                <input
                  value={draft.cohort}
                  onChange={(e) => setDraft({ ...draft, cohort: e.target.value })}
                  placeholder="e.g. 2026"
                />
              </label>
              <label>
                Min score
                <input
                  type="number"
                  value={draft.min_score}
                  onChange={(e) => setDraft({ ...draft, min_score: e.target.value })}
                />
              </label>
              <label>
                Max score
                <input
                  type="number"
                  value={draft.max_score}
                  onChange={(e) => setDraft({ ...draft, max_score: e.target.value })}
                />
              </label>
              <label>
                From
                <input
                  type="date"
                  value={draft.date_from}
                  onChange={(e) => setDraft({ ...draft, date_from: e.target.value })}
                />
              </label>
              <label>
                To
                <input
                  type="date"
                  value={draft.date_to}
                  onChange={(e) => setDraft({ ...draft, date_to: e.target.value })}
                />
              </label>
              <button type="button" className="btn link" onClick={clearFilters}>
                <X size={14} /> Clear all
              </button>
            </div>
          )}
        </form>
      </section>

      <section className="card">
        {loading ? (
          <SkeletonTable rows={6} />
        ) : (data?.items ?? []).length === 0 ? (
          <EmptyState
            icon={<Users size={24} />}
            title="No attempts match these filters"
            description="Try widening the date range or clearing a filter."
          />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className={`sortable ${sort === 'student_name' ? 'sorted' : ''}`} onClick={() => toggleSort('student_name')}>
                    Student <SortIcon column="student_name" />
                  </th>
                  <th className={`sortable ${sort === 'exam_title' ? 'sorted' : ''}`} onClick={() => toggleSort('exam_title')}>
                    Exam <SortIcon column="exam_title" />
                  </th>
                  <th>Batch</th>
                  <th>Status</th>
                  <th className={`sortable ${sort === 'score' ? 'sorted' : ''}`} onClick={() => toggleSort('score')}>
                    Score <SortIcon column="score" />
                  </th>
                  <th className={`sortable ${sort === 'percentage' ? 'sorted' : ''}`} onClick={() => toggleSort('percentage')}>
                    % <SortIcon column="percentage" />
                  </th>
                  <th className={`sortable ${sort === 'submitted_at' ? 'sorted' : ''}`} onClick={() => toggleSort('submitted_at')}>
                    Submitted <SortIcon column="submitted_at" />
                  </th>
                </tr>
              </thead>
              <tbody>
                {(data?.items ?? []).map((item) => (
                  <tr
                    key={item.attempt_id}
                    className="clickable-row"
                    onClick={() => navigate(`/admin/student-details/${item.attempt_id}`)}
                  >
                    <td>
                      <Identity name={item.student_name} sub={item.student_id} size="sm" />
                    </td>
                    <td>{item.exam_title}</td>
                    <td>{item.cohort ?? '—'}</td>
                    <td>
                      <span className={`tag ${item.status}`}>{item.status}</span>
                    </td>
                    <td className="num">
                      {item.total_score != null ? item.total_score : '—'}
                      {item.max_score != null ? ` / ${item.max_score}` : ''}
                    </td>
                    <td className="num">
                      <ScoreBadge percentage={item.percentage} passThreshold={activeExam?.pass_percentage} />
                    </td>
                    <td>{item.submitted_at ? new Date(item.submitted_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="form-actions">
          <button className="btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            <ChevronLeft size={15} /> Previous
          </button>
          <span className="muted small">
            Page {page} of {totalPages} · {total} attempt{total === 1 ? '' : 's'}
          </span>
          <button
            className="btn"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next <ChevronRight size={15} />
          </button>
        </div>
      </section>
    </AppShell>
  );
}
