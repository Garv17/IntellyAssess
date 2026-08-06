import {
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Flag,
  Loader2,
  Play,
  Timer,
  WifiOff,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ApiError,
  api,
  type AnswerSave,
  type CodeRun,
  type ExamPaper,
  type Question,
} from '../api';
import CodeEditor from '../components/CodeEditor';
import { useAutoSave } from '../hooks/useAutoSave';
import { useExamTimer } from '../hooks/useExamTimer';

type AnswerMap = Record<string, AnswerSave>;

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable || Boolean(el.closest('.monaco-editor'));
}

export default function Exam() {
  const navigate = useNavigate();
  const [paper, setPaper] = useState<ExamPaper | null>(null);
  const [answers, setAnswers] = useState<AnswerMap>({});
  const [initialSeconds, setInitialSeconds] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const submittedRef = useRef(false);

  // The timer and the auto-save hook both need to trigger submission, but `finish`
  // needs both of them. An indirection ref breaks the cycle without stale closures.
  const finishRef = useRef<(auto: boolean) => void>(() => {});
  const expire = useCallback(() => finishRef.current(true), []);

  const autoSave = useAutoSave({ debounceMs: 1200, onExpired: expire });
  const { formatted, seconds } = useExamTimer(initialSeconds, expire);

  const finish = useCallback(
    async (auto: boolean) => {
      if (submittedRef.current) return;
      submittedRef.current = true;
      setSubmitting(true);
      try {
        await autoSave.flush();
        const receipt = await api.submit(auto);
        navigate('/submitted', { state: receipt, replace: true });
      } catch (err) {
        // A 409/410 means the server already finalized the attempt — the student is
        // done either way, so show the confirmation rather than a scary error.
        if (err instanceof ApiError && (err.status === 409 || err.status === 410)) {
          navigate('/submitted', { replace: true });
          return;
        }
        submittedRef.current = false;
        setError(err instanceof Error ? err.message : 'Submission failed. Please retry.');
      } finally {
        setSubmitting(false);
      }
    },
    [navigate, autoSave],
  );

  useEffect(() => {
    finishRef.current = (auto: boolean) => void finish(auto);
  }, [finish]);

  // Load the frozen paper and any previously saved answers. This is the resume path:
  // a refresh re-enters here and rebuilds the exact same state.
  useEffect(() => {
    (async () => {
      try {
        const [state, questions] = await Promise.all([api.examState(), api.examQuestions()]);
        setInitialSeconds(state.seconds_remaining);
        setPaper(questions);
        const map: AnswerMap = {};
        for (const answer of state.answers) map[answer.question_id] = answer;
        setAnswers(map);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          navigate('/dashboard', { replace: true });
          return;
        }
        setError(err instanceof Error ? err.message : 'Could not load the exam');
      } finally {
        setLoading(false);
      }
    })();
  }, [navigate]);

  const flat = useMemo(() => {
    if (!paper) return [];
    return paper.sections.flatMap((section) =>
      section.questions.map((question) => ({ section, question })),
    );
  }, [paper]);

  const current = flat[cursor];

  const update = useCallback(
    (questionId: string, patch: Partial<AnswerSave>, immediate: boolean) => {
      setAnswers((prev) => {
        const next: AnswerSave = { ...(prev[questionId] ?? { question_id: questionId }), ...patch };
        autoSave.queue(next, immediate);
        return { ...prev, [questionId]: next };
      });
    },
    [autoSave],
  );

  // Warn on accidental navigation away. Answers are already saved server-side, so
  // this is a courtesy, not a data-loss guard.
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (!submittedRef.current) e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, []);

  const flatLength = flat.length;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || confirmOpen) return;
      if (e.key === 'ArrowLeft') {
        setCursor((c) => Math.max(0, c - 1));
      } else if (e.key === 'ArrowRight') {
        setCursor((c) => Math.min(flatLength - 1, c + 1));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [flatLength, confirmOpen]);

  if (loading) return <div className="center">Loading your exam…</div>;
  if (error && !paper) return <div className="center error">{error}</div>;
  if (!paper || !current) return <div className="center">No questions available.</div>;

  const answered = Object.values(answers).filter(
    (a) => a.selected_option_id || (a.code_text && a.code_text.trim()),
  ).length;
  const lowTime = seconds <= 300;
  const criticalTime = seconds <= 60;

  return (
    <div className="exam">
      <header className={`exam-bar ${criticalTime ? 'critical-time' : lowTime ? 'low-time' : ''}`}>
        <div>
          <strong>{paper.title}</strong>
          <span className="muted"> · {current.section.title}</span>
        </div>
        <div className="exam-bar-right">
          <SaveIndicator status={autoSave.status} lastSavedAt={autoSave.lastSavedAt} />
          <div className="timer" aria-live="off">
            <Timer size={16} />
            {formatted}
          </div>
          <button className="btn primary" onClick={() => setConfirmOpen(true)}>
            Submit
          </button>
        </div>
      </header>

      <div className="exam-progress">
        <div className="exam-progress-label">
          <span>
            {answered} of {flat.length} answered
          </span>
          <span>{Math.round((answered / flat.length) * 100)}%</span>
        </div>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${(answered / flat.length) * 100}%` }} />
        </div>
      </div>

      {lowTime && (
        <div className="banner warn">
          Less than {criticalTime ? '1 minute' : '5 minutes'} remain. The exam submits automatically when the timer reaches zero.
        </div>
      )}
      {error && <div className="banner error">{error}</div>}

      <div className="exam-body">
        <main className="question-pane">
          <div className="question-head">
            <span className="pill">Question {cursor + 1} of {flat.length}</span>
            <span className="pill">{current.question.marks} marks</span>
            {current.question.negative_marks > 0 && (
              <span className="pill danger">−{current.question.negative_marks} if wrong</span>
            )}
            <label className="review-toggle">
              <input
                type="checkbox"
                checked={answers[current.question.id]?.is_marked_for_review ?? false}
                onChange={(e) =>
                  update(current.question.id, { is_marked_for_review: e.target.checked }, true)
                }
              />
              <Flag size={14} /> Mark for review
            </label>
          </div>

          <div key={current.question.id} className="question-fade">
            <QuestionView
              question={current.question}
              diGroup={
                current.question.di_group_id
                  ? current.section.di_groups.find((g) => g.id === current.question.di_group_id)
                  : undefined
              }
              answer={answers[current.question.id]}
              onChange={update}
            />
          </div>

          <div className="nav-row">
            <button
              className="btn"
              disabled={cursor === 0}
              onClick={() => setCursor((c) => Math.max(0, c - 1))}
            >
              <ChevronLeft size={15} /> Previous
            </button>
            <span className="nav-hint">
              <kbd className="kbd">←</kbd>
              <kbd className="kbd">→</kbd> to navigate
            </span>
            <button
              className="btn"
              disabled={cursor === flat.length - 1}
              onClick={() => setCursor((c) => Math.min(flat.length - 1, c + 1))}
            >
              Next <ChevronRight size={15} />
            </button>
          </div>
        </main>

        <aside className="palette">
          <div className="palette-stats">
            <div>
              <strong>{answered}</strong>
              <span>Answered</span>
            </div>
            <div>
              <strong>{flat.length - answered}</strong>
              <span>Remaining</span>
            </div>
          </div>
          {paper.sections.map((section) => (
            <div key={section.id} className="palette-section">
              <h4>{section.title}</h4>
              <div className="palette-grid">
                {section.questions.map((question) => {
                  const index = flat.findIndex((f) => f.question.id === question.id);
                  const answer = answers[question.id];
                  const isAnswered = Boolean(
                    answer?.selected_option_id || answer?.code_text?.trim(),
                  );
                  const classes = [
                    'palette-cell',
                    isAnswered ? 'answered' : '',
                    answer?.is_marked_for_review ? 'review' : '',
                    index === cursor ? 'active' : '',
                  ]
                    .filter(Boolean)
                    .join(' ');
                  return (
                    <button
                      key={question.id}
                      className={classes}
                      onClick={() => setCursor(index)}
                      title={question.type.toUpperCase()}
                    >
                      {index + 1}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          <div className="legend">
            <span><i className="swatch answered" /> Answered</span>
            <span><i className="swatch review" /> For review</span>
            <span><i className="swatch" /> Not answered</span>
          </div>
        </aside>
      </div>

      {confirmOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>Submit your exam?</h3>
            <p>
              You have answered <strong>{answered}</strong> of <strong>{flat.length}</strong>{' '}
              questions. This cannot be undone.
            </p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setConfirmOpen(false)} disabled={submitting}>
                Keep working
              </button>
              <button
                className="btn primary"
                onClick={() => void finish(false)}
                disabled={submitting}
              >
                {submitting ? 'Submitting…' : 'Submit exam'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SaveIndicator({
  status,
  lastSavedAt,
}: {
  status: string;
  lastSavedAt: Date | null;
}) {
  const label =
    status === 'saving'
      ? 'Saving…'
      : status === 'pending'
        ? 'Unsaved changes'
        : status === 'offline'
          ? 'Reconnecting…'
          : lastSavedAt
            ? `Saved ${lastSavedAt.toLocaleTimeString()}`
            : 'All answers saved';
  const icon =
    status === 'saving' ? (
      <Loader2 size={14} className="spinner" />
    ) : status === 'pending' ? (
      <AlertCircle size={14} />
    ) : status === 'offline' ? (
      <WifiOff size={14} />
    ) : (
      <CheckCircle2 size={14} />
    );
  return (
    <span className={`save-indicator ${status}`}>
      {icon}
      {label}
    </span>
  );
}

function QuestionView({
  question,
  diGroup,
  answer,
  onChange,
}: {
  question: Question;
  diGroup?: { title: string; passage_md: string | null; image_url: string | null };
  answer?: AnswerSave;
  onChange: (id: string, patch: Partial<AnswerSave>, immediate: boolean) => void;
}) {
  if (question.type === 'coding' && question.coding_problem) {
    return <CodingView question={question} answer={answer} onChange={onChange} />;
  }

  return (
    <div>
      {diGroup && (
        <div className="di-stimulus">
          <h4>{diGroup.title}</h4>
          {diGroup.image_url && (
            <img src={diGroup.image_url} alt={diGroup.title} className="di-image" />
          )}
          {diGroup.passage_md && <pre className="di-passage">{diGroup.passage_md}</pre>}
        </div>
      )}
      <p className="question-body">{question.body_md}</p>
      <div className="options">
        {question.options.map((option, index) => (
          <label
            key={option.id}
            className={`option ${answer?.selected_option_id === option.id ? 'selected' : ''}`}
          >
            <input
              type="radio"
              name={question.id}
              checked={answer?.selected_option_id === option.id}
              // MCQ selections save immediately: they're discrete, cheap, and the
              // most painful thing to lose.
              onChange={() => onChange(question.id, { selected_option_id: option.id }, true)}
            />
            <span className="option-letter">{String.fromCharCode(65 + index)}</span>
            <span>{option.body}</span>
          </label>
        ))}
      </div>
      {answer?.selected_option_id && (
        <button
          className="btn link"
          onClick={() => onChange(question.id, { selected_option_id: null }, true)}
        >
          Clear response
        </button>
      )}
    </div>
  );
}

function CodingView({
  question,
  answer,
  onChange,
}: {
  question: Question;
  answer?: AnswerSave;
  onChange: (id: string, patch: Partial<AnswerSave>, immediate: boolean) => void;
}) {
  const problem = question.coding_problem!;
  const [language, setLanguage] = useState(answer?.language ?? problem.allowed_languages[0]);
  const [run, setRun] = useState<CodeRun | null>(null);
  const [running, setRunning] = useState(false);
  const pollRef = useRef<number | null>(null);

  const code = answer?.code_text ?? problem.starter_code[language] ?? '';

  useEffect(() => () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
  }, []);

  const runCode = async () => {
    setRunning(true);
    setRun(null);
    try {
      const { run_id } = await api.runCode(question.id, language, code);
      // Judging is queued, so poll rather than blocking a request for seconds.
      pollRef.current = window.setInterval(async () => {
        try {
          const result = await api.codeRun(run_id);
          setRun(result);
          if (result.status === 'done' || result.status === 'error') {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setRunning(false);
          }
        } catch {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setRunning(false);
        }
      }, 1200);
    } catch (err) {
      setRunning(false);
      setRun({
        run_id: '',
        status: 'error',
        passed: 0,
        total: 0,
        results: [],
        error: err instanceof Error ? err.message : 'Run failed',
      });
    }
  };

  return (
    <div className="coding">
      <div className="coding-statement">
        <pre>{problem.statement_md}</pre>
        {problem.sample_test_cases.length > 0 && (
          <div className="samples">
            <h5>Sample cases</h5>
            {problem.sample_test_cases.map((tc, i) => (
              <div key={tc.id} className="sample">
                <div>
                  <span className="muted">Input {i + 1}</span>
                  <pre>{tc.stdin}</pre>
                </div>
                <div>
                  <span className="muted">Expected</span>
                  <pre>{tc.expected_stdout}</pre>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="coding-editor">
        <div className="editor-bar">
          <select
            value={language}
            onChange={(e) => {
              const next = e.target.value;
              setLanguage(next);
              // Only swap in the starter template if the student hasn't written anything,
              // so changing language never destroys work.
              const isUntouched = !answer?.code_text?.trim();
              onChange(
                question.id,
                {
                  language: next,
                  code_text: isUntouched ? (problem.starter_code[next] ?? '') : answer?.code_text,
                },
                true,
              );
            }}
          >
            {problem.allowed_languages.map((lang) => (
              <option key={lang} value={lang}>
                {lang}
              </option>
            ))}
          </select>
          <span className="muted">
            {problem.time_limit_ms} ms · {problem.memory_limit_mb} MB
          </span>
          <button className="btn" onClick={() => void runCode()} disabled={running}>
            {running ? <Loader2 size={15} className="spinner" /> : <Play size={15} />}
            {running ? 'Running…' : 'Run against samples'}
          </button>
        </div>

        {/* Paste is allowed but every run and the final submission are recorded,
            so copied code stays reviewable after the fact. */}
        <div className="editor-frame">
          <CodeEditor
            language={language}
            value={code}
            onChange={(value) => onChange(question.id, { code_text: value, language }, false)}
          />
        </div>

        {run && (
          <div className="run-output">
            {run.error ? (
              <pre className="error">{run.error}</pre>
            ) : (
              <>
                <div className={run.passed === run.total ? 'verdict pass' : 'verdict fail'}>
                  {run.passed === run.total ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                  {run.passed} / {run.total} sample cases passed
                </div>
                {run.results.map((result) => (
                  <div key={result.index} className="case">
                    <span className={result.passed ? 'ok' : 'bad'}>
                      Case {result.index + 1}: {result.verdict}
                    </span>
                    {!result.passed && (
                      <div className="case-detail">
                        <div>
                          <span className="muted">Expected</span>
                          <pre>{result.expected}</pre>
                        </div>
                        <div>
                          <span className="muted">Your output</span>
                          <pre>{result.actual || '(empty)'}</pre>
                        </div>
                        {result.stderr && (
                          <div>
                            <span className="muted">Stderr</span>
                            <pre>{result.stderr}</pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                <p className="muted small">
                  Hidden test cases are evaluated after you submit.
                </p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
