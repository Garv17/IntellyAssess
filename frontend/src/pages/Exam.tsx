import { AlertTriangle, ChevronLeft, ChevronRight, Flag, Timer } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, api, type AnswerSave, type ExamPaper, type Me } from '../api';
import CodingWorkspace from '../components/CodingWorkspace';
import { CodingEditorPane, CodingProblemPane } from '../components/CodingView';
import QuestionNavigator from '../components/QuestionNavigator';
import QuestionView from '../components/QuestionView';
import SaveIndicator from '../components/SaveIndicator';
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
  const [me, setMe] = useState<Me | null>(null);
  const [answers, setAnswers] = useState<AnswerMap>({});
  const [initialSeconds, setInitialSeconds] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [tabWarningCount, setTabWarningCount] = useState(0);
  const submittedRef = useRef(false);

  // The timer and the auto-save hook both need to trigger submission, but `finish`
  // needs both of them. An indirection ref breaks the cycle without stale closures.
  const finishRef = useRef<(auto: boolean) => void>(() => {});
  const expire = useCallback(() => finishRef.current(true), []);

  const onTabSwitch = useCallback(() => {
    setTabWarningCount((c) => c + 1);
  }, []);

  const autoSave = useAutoSave({ debounceMs: 1200, onExpired: expire });
  const { formatted, seconds } = useExamTimer(initialSeconds, expire, onTabSwitch);

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
        const [state, questions, profile] = await Promise.all([
          api.examState(),
          api.examQuestions(),
          api.me(),
        ]);
        setInitialSeconds(state.seconds_remaining);
        setPaper(questions);
        setMe(profile);
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
      if (isTypingTarget(e.target) || confirmOpen || tabWarningCount > 0) return;
      if (e.key === 'ArrowLeft') {
        setCursor((c) => Math.max(0, c - 1));
        void autoSave.flush();
      } else if (e.key === 'ArrowRight') {
        setCursor((c) => Math.min(flatLength - 1, c + 1));
        void autoSave.flush();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [flatLength, confirmOpen, tabWarningCount, autoSave]);

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
          {me && (
            <span className="pill student-name">
              {me.name}
              {me.role === 'student' && me.identifier && <span className="muted"> · {me.identifier}</span>}
            </span>
          )}
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

      {(() => {
        const questionHead = (
          <div className="question-head">
            <span className="pill">
              Question {cursor + 1} of {flat.length}
            </span>
            <span className="pill">{current.question.marks} marks</span>
            {current.question.negative_marks > 0 && (
              <span className="pill danger">−{current.question.negative_marks} if wrong</span>
            )}
          </div>
        );

        const navRow = (
          <div className="nav-row">
            <button
              className="btn"
              disabled={cursor === 0}
              onClick={() => {
                setCursor((c) => Math.max(0, c - 1));
                void autoSave.flush();
              }}
            >
              <ChevronLeft size={15} /> Previous
            </button>
            <span className="nav-hint">
              <kbd className="kbd">←</kbd>
              <kbd className="kbd">→</kbd> to navigate
            </span>
            <div className="nav-next-group">
              <button
                className="btn"
                disabled={cursor === flat.length - 1}
                onClick={() => {
                  setCursor((c) => Math.min(flat.length - 1, c + 1));
                  void autoSave.flush();
                }}
              >
                Next <ChevronRight size={15} />
              </button>
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
          </div>
        );

        const navigator = (
          <QuestionNavigator
            sections={paper.sections}
            flat={flat}
            cursor={cursor}
            answers={answers}
            answered={answered}
            onSelect={(index) => {
              setCursor(index);
              void autoSave.flush();
            }}
          />
        );

        if (current.question.type === 'coding' && current.question.coding_problem) {
          return (
            <CodingWorkspace
              problemHead={questionHead}
              problemBody={<CodingProblemPane key={current.question.id} question={current.question} />}
              problemFoot={navRow}
              editorPane={
                <CodingEditorPane
                  key={current.question.id}
                  question={current.question}
                  answer={answers[current.question.id]}
                  onChange={update}
                />
              }
              navigatorPane={navigator}
            />
          );
        }

        return (
          <div className="exam-body">
            <main className="question-pane">
              {questionHead}
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
              {navRow}
            </main>
            <aside className="palette">{navigator}</aside>
          </div>
        );
      })()}

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

      {tabWarningCount > 0 && (
        <div className="modal-backdrop" role="alertdialog" aria-modal="true" aria-label="Tab switch warning">
          <div className="modal tab-warning">
            <div className="modal-icon danger xl pulse">
              <AlertTriangle size={30} />
            </div>
            <h3>Tab switch detected</h3>
            <p>
              You left the exam tab. This has been recorded and will be visible to the
              proctor for review.
              {tabWarningCount > 1 && (
                <>
                  {' '}
                  <strong>This is warning #{tabWarningCount}.</strong> Repeated switching may
                  be flagged as suspicious activity.
                </>
              )}
            </p>
            <div className="modal-actions">
              <button className="btn danger" onClick={() => setTabWarningCount(0)}>
                I understand, return to exam
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
