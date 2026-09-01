import { AlertTriangle, Bot, Code2, RefreshCw, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  api,
  type CodingSubmissionDetail,
  type CodingSubmissionListItem,
  type EvaluationStatus,
} from '../../api';
import { Identity } from '../../components/Avatar';
import Badge from '../../components/Badge';
import Markdown from '../../components/Markdown';
import { SkeletonCard } from '../../components/Skeleton';
import { errorContext, log } from '../../logger';

const STATUS_LABEL: Record<EvaluationStatus, string> = {
  pending: 'Queued',
  ai_evaluating: 'AI evaluating',
  ai_evaluated: 'AI evaluated',
  ai_failed: 'AI failed',
  admin_reviewed: 'Reviewed (draft)',
  finalized: 'Finalized',
};

const STATUS_VARIANT: Record<EvaluationStatus, 'success' | 'danger' | 'warning' | 'neutral'> = {
  pending: 'neutral',
  ai_evaluating: 'neutral',
  ai_evaluated: 'warning',
  ai_failed: 'danger',
  admin_reviewed: 'warning',
  finalized: 'success',
};

function StatusBadge({ status }: { status: EvaluationStatus }) {
  return <Badge variant={STATUS_VARIANT[status]}>{STATUS_LABEL[status]}</Badge>;
}

/** The coding evaluation queue. Route: /admin/coding-evaluation, with an
    optional :submissionId opening the review panel beside the list. */
// Bulk-retry only ever applies to submissions a re-evaluation can actually help —
// admin_reviewed/finalized rows are refused by the endpoint anyway (a human's
// decision is never clobbered by a retry), and ai_evaluated rows already have a
// usable recommendation.
const RETRYABLE_STATUSES: EvaluationStatus[] = ['pending', 'ai_failed', 'ai_evaluating'];

export default function CodingEvaluation() {
  const { submissionId } = useParams();
  const [items, setItems] = useState<CodingSubmissionListItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState<EvaluationStatus | ''>('');
  const [needsReview, setNeedsReview] = useState(false);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(submissionId ?? null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [retrying, setRetrying] = useState(false);
  const [retryNotice, setRetryNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api
      .codingSubmissions({
        status: statusFilter || undefined,
        needs_review: needsReview || undefined,
        search: search || undefined,
        page_size: 50,
      })
      .then((page) => {
        setItems(page.items);
        setTotal(page.total);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load submissions'));
  }, [statusFilter, needsReview, search]);

  useEffect(load, [load]);
  // A filter/search change can drop rows out of view (or bring different ones
  // in); stale checks against ids no longer shown would silently retry
  // submissions the admin can no longer see, so start clean on every load.
  useEffect(() => {
    setCheckedIds(new Set());
    setRetryNotice(null);
  }, [statusFilter, needsReview, search]);

  const retryableIds = (items ?? [])
    .filter((item) => RETRYABLE_STATUSES.includes(item.status))
    .map((item) => item.id);
  const allRetryableChecked =
    retryableIds.length > 0 && retryableIds.every((id) => checkedIds.has(id));

  const toggleAll = (checked: boolean) => {
    setCheckedIds(checked ? new Set(retryableIds) : new Set());
  };

  const toggleOne = (id: string, checked: boolean) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const retrySelected = async () => {
    const ids = [...checkedIds];
    if (ids.length === 0) return;
    setRetrying(true);
    setRetryNotice(null);
    const results = await Promise.allSettled(ids.map((id) => api.reevaluateCodingSubmission(id)));
    const failed = results.filter((r) => r.status === 'rejected').length;
    setRetrying(false);
    setCheckedIds(new Set());
    setRetryNotice(
      failed === 0
        ? `Re-evaluation queued for ${ids.length} submission(s).`
        : `Queued ${ids.length - failed} of ${ids.length}; ${failed} could not be queued.`,
    );
    load();
  };

  return (
    <div className="shell">
      <header className="top-bar">
        <Link to="/admin" className="btn ghost small">
          ← Dashboard
        </Link>
        <span className="grow" />
        <button className="btn ghost small" onClick={load}>
          <RefreshCw size={14} /> Refresh
        </button>
      </header>

      <main className="container wide">
        <div className="page-header">
          <div>
            <h1>
              <Code2 size={20} /> Coding evaluation
            </h1>
            <p className="muted small">
              AI scores are recommendations. A coding mark only counts toward the exam total once
              you finalize it.
            </p>
          </div>
        </div>

        {error && <div className="banner error">{error}</div>}

        <div className="card">
          <div className="filter-primary">
            <input
              placeholder="Search student name or ID…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as EvaluationStatus | '')}
            >
              <option value="">All statuses</option>
              {Object.entries(STATUS_LABEL).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={needsReview}
                onChange={(e) => setNeedsReview(e.target.checked)}
              />
              Needs manual review
            </label>
            <span className="muted small">{total} submission(s)</span>
            <span className="grow" />
            <button
              className="btn ghost small"
              disabled={checkedIds.size === 0 || retrying}
              onClick={() => void retrySelected()}
            >
              <RefreshCw size={14} /> Retry selected ({checkedIds.size})
            </button>
          </div>

          {retryNotice && <div className="banner ok">{retryNotice}</div>}

          {!items && <SkeletonCard />}
          {items && items.length === 0 && (
            <p className="muted">No coding submissions match these filters.</p>
          )}
          {items && items.length > 0 && (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        checked={allRetryableChecked}
                        disabled={retryableIds.length === 0}
                        onChange={(e) => toggleAll(e.target.checked)}
                        title="Select all retryable submissions"
                      />
                    </th>
                    <th>Student</th>
                    <th>Exam</th>
                    <th>Language</th>
                    <th>Submitted</th>
                    <th>Status</th>
                    <th>AI (rubric)</th>
                    <th>Final (exam)</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id} className={selected === item.id ? 'active-row' : ''}>
                      <td>
                        {RETRYABLE_STATUSES.includes(item.status) && (
                          <input
                            type="checkbox"
                            checked={checkedIds.has(item.id)}
                            onChange={(e) => toggleOne(item.id, e.target.checked)}
                          />
                        )}
                      </td>
                      <td>
                        <Identity name={item.student_name} sub={item.student_number} />
                      </td>
                      <td>{item.exam_title}</td>
                      <td>{item.language}</td>
                      <td>{new Date(item.submitted_at).toLocaleString()}</td>
                      <td>
                        <StatusBadge status={item.status} />
                        {item.manual_review_recommended && item.status !== 'finalized' && (
                          <div className="muted small urgent">Manual Review Recommended</div>
                        )}
                      </td>
                      <td>
                        {item.ai_score != null
                          ? `${item.ai_score} / ${item.rubric_max_marks}`
                          : '—'}
                      </td>
                      <td>
                        {item.final_score != null ? `${item.final_score} / ${item.max_marks}` : '—'}
                      </td>
                      <td>
                        <button className="btn small" onClick={() => setSelected(item.id)}>
                          Review
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {selected && (
          <SubmissionReview
            submissionId={selected}
            onClose={() => setSelected(null)}
            onSaved={load}
          />
        )}
      </main>
    </div>
  );
}

function SubmissionReview({
  submissionId,
  onClose,
  onSaved,
}: {
  submissionId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [detail, setDetail] = useState<CodingSubmissionDetail | null>(null);
  const [finalScore, setFinalScore] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(() => {
    setDetail(null);
    setError(null);
    api
      .codingSubmission(submissionId)
      .then((d) => {
        setDetail(d);
        // Prefer a score the admin already saved; otherwise seed the field with
        // the AI recommendation *scaled onto this question's marks* — ai_score is
        // on the rubric's scale, so copying it raw would under-mark a 20-mark
        // question and be rejected outright on a 5-mark one. Still only a
        // starting point: nothing is applied until the admin saves.
        setFinalScore(
          d.final_score != null ? String(d.final_score)
          : d.ai_score != null ? String(scaleAiScore(d))
          : '',
        );
        setComment(d.admin_comment ?? '');
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load submission'));
  }, [submissionId]);

  useEffect(load, [load]);

  const submit = async (finalize: boolean) => {
    if (!detail) return;
    const value = Number(finalScore);
    if (Number.isNaN(value) || value < 0 || value > detail.max_marks) {
      setError(`Final score must be between 0 and ${detail.max_marks}`);
      return;
    }
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const updated = await api.gradeCodingSubmission(submissionId, {
        final_score: value,
        admin_comment: comment || null,
        finalize,
      });
      setDetail(updated);
      setSaved(finalize ? 'Score finalized — it now counts toward the exam total.' : 'Draft saved.');
      onSaved();
    } catch (err) {
      // A score that didn't persist changes a student's result, so this is an
      // error even though the admin does see the message.
      log.error('coding score save failed', {
        submission_id: submissionId,
        finalize,
        ...errorContext(err),
      });
      setError(err instanceof Error ? err.message : 'Could not save the score');
    } finally {
      setBusy(false);
    }
  };

  const reevaluate = async () => {
    setBusy(true);
    setError(null);
    try {
      setDetail(await api.reevaluateCodingSubmission(submissionId));
      setSaved('Re-evaluation queued.');
      onSaved();
    } catch (err) {
      log.warn('re-evaluation could not be queued', {
        submission_id: submissionId,
        ...errorContext(err),
      });
      setError(err instanceof Error ? err.message : 'Could not queue a re-evaluation');
    } finally {
      setBusy(false);
    }
  };

  if (error && !detail) return <div className="banner error">{error}</div>;
  if (!detail) return <SkeletonCard />;

  const scored = detail.rubric.some((c) => c.awarded != null);

  return (
    <section className="card">
      <div className="card-header">
        <h2>
          <Identity name={detail.student_name} sub={`${detail.student_number} · ${detail.exam_title}`} />
        </h2>
        <span className="grow" />
        <StatusBadge status={detail.status} />
        <button className="btn ghost small" onClick={onClose}>
          Close
        </button>
      </div>

      {saved && <div className="banner ok">{saved}</div>}
      {error && <div className="banner error">{error}</div>}

      {detail.manual_review_recommended && detail.status !== 'finalized' && (
        <div className="banner review-flag">
          <AlertTriangle size={15} /> Manual Review Recommended
          {detail.ai_confidence != null && (
            <span className="muted small">
              {' '}
              (AI confidence {(detail.ai_confidence * 100).toFixed(0)}% — a review signal, not a
              calibrated probability)
            </span>
          )}
        </div>
      )}

      <dl className="meta stacked">
        <div>
          <dt>Language</dt>
          <dd>{detail.language}</dd>
        </div>
        <div>
          <dt>Submitted</dt>
          <dd>{new Date(detail.submitted_at).toLocaleString()}</dd>
        </div>
        <div>
          <dt>Marks available</dt>
          <dd>{detail.max_marks}</dd>
        </div>
      </dl>

      <h3>Question</h3>
      <Markdown>{detail.statement_md || detail.question_body_md}</Markdown>
      {detail.constraints_md && (
        <>
          <h4>Constraints</h4>
          <Markdown>{detail.constraints_md}</Markdown>
        </>
      )}

      <h3>Submitted code</h3>
      <pre className="code-block">{detail.code_text}</pre>

      <h3>
        <Bot size={16} /> AI evaluation
      </h3>

      {detail.status === 'ai_failed' && (
        <div className="banner error">
          <AlertTriangle size={15} />
          <div>
            <strong>AI evaluation failed — grade this manually.</strong>
            {detail.ai_error && <div className="muted small">{detail.ai_error}</div>}
          </div>
        </div>
      )}
      {(detail.status === 'pending' || detail.status === 'ai_evaluating') && (
        <p className="muted">Evaluation in progress. You can still grade this manually now.</p>
      )}

      {scored && (
        <>
          <p className="ai-recommended">
            <Sparkles size={15} /> AI Recommended: <strong>{detail.ai_score}</strong> /{' '}
            {detail.rubric_max_marks}
            {detail.ai_model && <span className="muted small"> · {detail.ai_model}</span>}
          </p>

          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Criterion</th>
                  <th>Awarded</th>
                  <th>Why this score</th>
                </tr>
              </thead>
              <tbody>
                {detail.rubric.map((c) => (
                  <tr key={c.key}>
                    <td>{c.key.replace(/_/g, ' ')}</td>
                    <td>
                      {c.awarded ?? '—'}/{c.max_marks}
                    </td>
                    <td>
                      {/* Older evaluations have scores but no justification. Showing
                          the rubric description alone would read as a reason, so it
                          is labelled as what it is. */}
                      {c.justification ? (
                        <span className="rubric-why">{c.justification}</span>
                      ) : (
                        <span className="muted small">No per-criterion reason recorded.</span>
                      )}
                      <span className="muted small rubric-criterion-desc">{c.description}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {detail.ai_reasoning && (
            <>
              <h4>Reasoning</h4>
              <p>{detail.ai_reasoning}</p>
            </>
          )}
          {detail.ai_strengths && detail.ai_strengths.length > 0 && (
            <>
              <h4>Strengths</h4>
              <ul>
                {detail.ai_strengths.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </>
          )}
          {detail.ai_issues && detail.ai_issues.length > 0 && (
            <>
              <h4>Issues</h4>
              <ul>
                {detail.ai_issues.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}

      {(detail.status === 'ai_failed' || detail.status === 'pending') && (
        <button className="btn ghost small" disabled={busy} onClick={() => void reevaluate()}>
          <RefreshCw size={14} /> Re-run AI evaluation
        </button>
      )}

      <h3>Final score</h3>
      <div className="final-score-row">
        <label>
          Score
          <input
            type="number"
            min={0}
            max={detail.max_marks}
            step="0.5"
            value={finalScore}
            onChange={(e) => setFinalScore(e.target.value)}
          />
        </label>
        <span className="muted">/ {detail.max_marks}</span>
        {detail.ai_score != null && (
          <button
            type="button"
            className="btn ghost small"
            onClick={() => setFinalScore(String(scaleAiScore(detail)))}
          >
            Accept AI Score
          </button>
        )}
      </div>

      <label>
        Admin comment
        <textarea
          rows={3}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Optional note for the record…"
        />
      </label>

      <div className="filter-primary">
        <button className="btn ghost" disabled={busy} onClick={() => void submit(false)}>
          Save draft
        </button>
        <button className="btn" disabled={busy} onClick={() => void submit(true)}>
          Save Final Score
        </button>
      </div>
      {detail.finalized_at && (
        <p className="muted small">
          Finalized {new Date(detail.finalized_at).toLocaleString()}
        </p>
      )}
    </section>
  );
}

/** The rubric is a fixed 10-point scale; a question may be worth something else.
    "Accept AI Score" therefore scales the recommendation onto this question's own
    marks rather than copying a number from a different scale. */
function scaleAiScore(detail: CodingSubmissionDetail): number {
  if (detail.ai_score == null) return 0;
  if (!detail.rubric_max_marks || detail.rubric_max_marks === detail.max_marks) {
    return detail.ai_score;
  }
  return Math.round((detail.ai_score / detail.rubric_max_marks) * detail.max_marks * 2) / 2;
}
