import {
  Activity as ActivityIcon,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Code2,
  Database,
  FileQuestion,
  Hash,
  ListChecks,
  Play,
  SkipForward,
  Trophy,
  XCircle,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type ActivityEvent, type AttemptOverview, type QuestionReview } from '../../api';
import { Identity } from '../../components/Avatar';
import Badge from '../../components/Badge';
import { SkeletonCard, SkeletonText } from '../../components/Skeleton';
import Tabs from '../../components/Tabs';

const STATUS_ICON: Record<QuestionReview['status'], React.ReactNode> = {
  correct: <CheckCircle2 size={16} className="urgent" style={{ color: 'var(--ok)' }} />,
  incorrect: <XCircle size={16} style={{ color: 'var(--danger)' }} />,
  skipped: <SkipForward size={16} style={{ color: 'var(--muted)' }} />,
};

const STATUS_VARIANT: Record<QuestionReview['status'], 'success' | 'danger' | 'neutral'> = {
  correct: 'success',
  incorrect: 'danger',
  skipped: 'neutral',
};

type TabKey = 'overview' | 'questions' | 'activity';

export default function AttemptDetail() {
  const { attemptId = '' } = useParams();
  const [overview, setOverview] = useState<AttemptOverview | null>(null);
  const [questions, setQuestions] = useState<QuestionReview[] | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[] | null>(null);
  const [tab, setTab] = useState<TabKey>('overview');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOverview(null);
    setQuestions(null);
    setActivity(null);
    setError(null);
    setTab('overview');
    Promise.all([api.attemptOverview(attemptId), api.attemptQuestions(attemptId)])
      .then(([o, q]) => {
        setOverview(o);
        setQuestions(q);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load attempt'));
  }, [attemptId]);

  useEffect(() => {
    if (tab !== 'activity' || activity !== null) return;
    void api
      .attemptActivity(attemptId)
      .then((t) => setActivity(t.events))
      .catch(() => setActivity([]));
  }, [tab, activity, attemptId]);

  const correctCount = questions?.filter((q) => q.status === 'correct').length ?? 0;
  const incorrectCount = questions?.filter((q) => q.status === 'incorrect').length ?? 0;
  const skippedCount = questions?.filter((q) => q.status === 'skipped').length ?? 0;

  return (
    <div className="shell">
      <header className="top-bar">
        <Link to="/admin/student-details" className="btn ghost small">
          ← Student Details
        </Link>
        {overview && (
          <span className={`tag ${overview.passed === null ? 'none' : overview.passed ? 'submitted' : 'closed'}`}>
            {overview.passed === null ? overview.status : overview.passed ? 'Pass' : 'Fail'}
          </span>
        )}
      </header>

      <main className="container wide">
        {error && <div className="banner error">{error}</div>}

        {!overview && !error && (
          <>
            <SkeletonCard />
          </>
        )}

        {overview && (
          <>
            <div className="page-header">
              <Identity name={overview.student_name} sub={`${overview.student_id} · ${overview.email ?? 'no email'}`} size="lg" />
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontWeight: 650 }}>{overview.exam_title}</div>
                <div className="muted small">{overview.cohort ?? 'No batch'}</div>
              </div>
            </div>

            <div className="stat-row">
              <div className="stat">
                <div className="stat-icon">
                  <Trophy size={17} />
                </div>
                <strong>
                  {overview.total_score ?? '—'}
                  {overview.max_score != null ? ` / ${overview.max_score}` : ''}
                </strong>
                <span>Score</span>
              </div>
              <div className="stat active">
                <strong>{overview.percentage != null ? `${overview.percentage}%` : '—'}</strong>
                <span>Percentage</span>
              </div>
              <div className="stat">
                <div className="stat-icon">
                  <Hash size={17} />
                </div>
                <strong>{overview.rank ?? '—'}</strong>
                <span>Rank</span>
              </div>
              <div className="stat">
                <div className="stat-icon">
                  <Clock size={17} />
                </div>
                <strong>
                  {overview.time_taken_minutes != null ? `${overview.time_taken_minutes}m` : '—'}
                </strong>
                <span>Time taken</span>
              </div>
              <div className={`stat ${overview.passed ? 'ok' : overview.passed === false ? '' : ''}`}>
                <strong>
                  {overview.passed === null ? (
                    '—'
                  ) : (
                    <Badge variant={overview.passed ? 'success' : 'danger'}>
                      {overview.passed ? 'Pass' : 'Fail'}
                    </Badge>
                  )}
                </strong>
                <span>Result</span>
              </div>
            </div>

            <Tabs
              value={tab}
              onChange={setTab}
              tabs={[
                { key: 'overview', label: 'Overview', icon: <ListChecks size={15} /> },
                { key: 'questions', label: 'Questions', icon: <FileQuestion size={15} />, count: questions?.length },
                { key: 'activity', label: 'Activity', icon: <ActivityIcon size={15} /> },
              ]}
            />

            {tab === 'overview' && (
              <>
                <div className="card">
                  <div className="card-header">
                    <h2>Attempt summary</h2>
                  </div>
                  <dl className="meta stacked">
                    <div>
                      <dt>Status</dt>
                      <dd>
                        <span className={`tag ${overview.status}`}>{overview.status}</span>
                      </dd>
                    </div>
                    <div>
                      <dt>Focus losses</dt>
                      <dd className={overview.focus_loss_count > 5 ? 'urgent' : ''}>
                        {overview.focus_loss_count}
                      </dd>
                    </div>
                    <div>
                      <dt>Started</dt>
                      <dd>{overview.started_at ? new Date(overview.started_at).toLocaleString() : '—'}</dd>
                    </div>
                    <div>
                      <dt>Submitted</dt>
                      <dd>{overview.submitted_at ? new Date(overview.submitted_at).toLocaleString() : '—'}</dd>
                    </div>
                  </dl>
                </div>

                {questions && (
                  <div className="card">
                    <div className="card-header">
                      <h2>Answer breakdown</h2>
                    </div>
                    <div className="stat-row compact" style={{ marginBottom: 0 }}>
                      <div className="stat ok">
                        <strong>{correctCount}</strong>
                        <span>Correct</span>
                      </div>
                      <div className="stat">
                        <strong style={{ color: 'var(--danger)' }}>{incorrectCount}</strong>
                        <span>Incorrect</span>
                      </div>
                      <div className="stat">
                        <strong>{skippedCount}</strong>
                        <span>Skipped</span>
                      </div>
                    </div>
                  </div>
                )}

                {overview.sections.length > 0 && (
                  <section className="card">
                    <div className="card-header">
                      <h2>Section-wise marks</h2>
                    </div>
                    <div className="bars">
                      {overview.sections.map((s) => (
                        <div key={s.section_id} className="bar-row">
                          <span className="bar-label">{s.title}</span>
                          <div className="bar-track">
                            <div
                              className="bar-fill"
                              style={{ width: `${s.max_score > 0 ? (s.score / s.max_score) * 100 : 0}%` }}
                            />
                          </div>
                          <span className="bar-value">
                            {s.score}/{s.max_score}
                          </span>
                        </div>
                      ))}
                    </div>
                  </section>
                )}
              </>
            )}

            {tab === 'questions' && questions && (
              <section className="card">
                {questions.length === 0 && <p className="muted">No questions on this exam.</p>}
                {questions.map((q, idx) => (
                  <QuestionReviewCard key={q.question_id} q={q} index={idx} />
                ))}
              </section>
            )}

            {tab === 'activity' && (
              <section className="card">
                <div className="card-header">
                  <h2>Activity timeline</h2>
                </div>
                {activity === null ? (
                  <SkeletonText width="100%" />
                ) : activity.length === 0 ? (
                  <p className="muted small">No recorded activity for this attempt.</p>
                ) : (
                  <div className="timeline">
                    {activity.map((event, i) => (
                      <div className="timeline-item" key={i}>
                        <div
                          className={`timeline-dot ${
                            event.type === 'submitted'
                              ? 'ok'
                              : event.type === 'focus_loss'
                                ? 'warn'
                                : event.type === 'started'
                                  ? 'ok'
                                  : 'neutral'
                          }`}
                        />
                        <span className="timeline-label">{event.label}</span>
                        <span className="timeline-time">{new Date(event.timestamp).toLocaleString()}</span>
                        {event.detail && <div className="timeline-detail">{event.detail}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function QuestionReviewCard({ q, index }: { q: QuestionReview; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="question-card">
      <div className="question-card-head" onClick={() => setOpen((v) => !v)}>
        <ChevronRight size={16} className={`chevron ${open ? 'open' : ''}`} />
        {STATUS_ICON[q.status]}
        <span style={{ fontWeight: 600 }}>Q{index + 1}</span>
        <span className="badge badge-neutral">{q.type.toUpperCase()}</span>
        <span className="grow" />
        <Badge variant={STATUS_VARIANT[q.status]}>{q.status}</Badge>
        <span className="muted small">
          {q.marks_awarded} / {q.marks} marks
        </span>
      </div>

      {open && (
        <div className="question-card-body">
          {q.di_context && (
            <div className="di-context">
              <strong>{q.di_context.title}</strong>
              {q.di_context.image_url && (
                <img src={q.di_context.image_url} alt={q.di_context.title} className="di-image" />
              )}
              {q.di_context.passage_md && <pre className="di-passage">{q.di_context.passage_md}</pre>}
            </div>
          )}

          <p className="question-body">{q.body_md}</p>

          {q.coding ? (
            <CodingReviewBlock coding={q.coding} />
          ) : (
            <dl className="meta stacked">
              <div>
                <dt>Student's answer</dt>
                <dd>{q.student_answer ?? '(not answered)'}</dd>
              </div>
              <div>
                <dt>Correct answer</dt>
                <dd>{q.correct_answer ?? '—'}</dd>
              </div>
            </dl>
          )}

          {q.explanation_md && (
            <div className="explanation-block">
              <strong>Explanation:</strong> {q.explanation_md}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CodingReviewBlock({ coding }: { coding: NonNullable<QuestionReview['coding']> }) {
  const isSql = coding.language.toLowerCase() === 'sql';
  return (
    <div>
      <p className="muted small" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
        {isSql ? <Database size={14} /> : <Code2 size={14} />}
        Language: <strong>{coding.language}</strong>
        {isSql && <span className="badge badge-info">SQL query</span>}
        · Status: <strong>{coding.status}</strong> · {coding.passed} / {coding.total} test cases
        passed · Score {coding.score.toFixed(2)}
      </p>
      <pre className="code-block">{coding.code_text}</pre>
      {coding.cases.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>#</th>
                <th>Verdict</th>
                <th>Time (ms)</th>
                <th>{isSql ? 'Query input' : 'stdin'}</th>
                <th>Expected</th>
                <th>Actual</th>
              </tr>
            </thead>
            <tbody>
              {coding.cases.map((c) => (
                <tr key={c.index}>
                  <td>{c.index + 1}</td>
                  <td>
                    <Badge variant={c.passed ? 'success' : 'danger'} icon={c.passed ? <Play size={11} /> : <AlertTriangle size={11} />}>
                      {c.verdict}
                    </Badge>
                  </td>
                  <td>{c.time_ms ?? '—'}</td>
                  <td>
                    <pre className="code-block small">{c.stdin ?? '—'}</pre>
                  </td>
                  <td>
                    <pre className="code-block small">{c.expected ?? '—'}</pre>
                  </td>
                  <td>
                    <pre className="code-block small">{c.actual ?? '—'}</pre>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {coding.cases.length === 0 && (
        <p className="muted small">No per-case results recorded for this submission.</p>
      )}
    </div>
  );
}
