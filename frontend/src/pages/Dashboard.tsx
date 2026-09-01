import { CalendarClock, CheckCircle2, LogOut, TimerReset } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError, api, tokens, type ExamSummary, type Me } from '../api';
import logo from '../assets/logo.png';
import { Identity } from '../components/Avatar';
import DropdownMenu, { DropdownItem } from '../components/DropdownMenu';
import EmptyState from '../components/EmptyState';
import { SkeletonCardGrid } from '../components/Skeleton';
import { errorContext, log } from '../logger';

// SEB's own browser identifies itself in the UA string (e.g. "...SEB/3.7...").
// Used to tell "launch SEB" (outside it) apart from "start exam" (already inside it).
const inSafeExamBrowser = /SEB[/ ]|SafeExamBrowser/i.test(navigator.userAgent);

export default function Dashboard() {
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [exams, setExams] = useState<ExamSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [profile, list] = await Promise.all([api.me(), api.availableExams()]);
        setMe(profile);
        setExams(list);
        // Resume straight into an exam that's already running — a student who
        // refreshed should not have to find their way back manually.
        if (list.some((e) => e.attempt_status === 'in_progress')) {
          navigate('/exam', { replace: true });
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          navigate('/', { replace: true });
          return;
        }
        log.warn('dashboard load failed', errorContext(err));
        setError(err instanceof Error ? err.message : 'Could not load exams');
      } finally {
        setLoading(false);
      }
    })();
  }, [navigate]);

  const start = async (examId: string) => {
    setStarting(examId);
    setError(null);
    try {
      await api.startExam(examId);
      navigate('/exam');
    } catch (err) {
      // A student blocked at the door on exam day — the exam id is the thing
      // api.ts's path-level line can't tell you.
      log.error('exam start failed', { exam_id: examId, ...errorContext(err) });
      setError(err instanceof Error ? err.message : 'Could not start the exam');
      setStarting(null);
    }
  };

  const signOut = async () => {
    try {
      await api.logout();
    } finally {
      tokens.clear();
      navigate('/', { replace: true });
    }
  };

  return (
    <div className="shell">
      <header className="top-bar">
        <span className="brand">
          <img src={logo} alt="IntellyAssess" className="brand-mark" />
          IntellyAssess
        </span>
        <div className="top-bar-right">
          {me && (
            <DropdownMenu trigger={<Identity name={me.name} sub={me.identifier} size="sm" />}>
              <DropdownItem icon={<LogOut size={15} />} onClick={() => void signOut()} danger>
                Sign out
              </DropdownItem>
            </DropdownMenu>
          )}
        </div>
      </header>

      <main className="container">
        <div className="page-header">
          <div>
            <h1>Your exams</h1>
            <p>Exams currently open for you to take.</p>
          </div>
        </div>

        {error && <div className="banner error">{error}</div>}

        {loading ? (
          <SkeletonCardGrid count={3} />
        ) : exams.length === 0 ? (
          <div className="card">
            <EmptyState
              icon={<CalendarClock size={24} />}
              title="No exams open right now"
              description="Check back at your scheduled time. This page updates automatically once an exam window opens."
            />
          </div>
        ) : (
          <div className="exam-grid">
            {exams.map((exam) => {
              const done = exam.attempt_status && exam.attempt_status !== 'in_progress';
              return (
                <div key={exam.id} className="card exam-card">
                  <div className="question-row-head" style={{ marginBottom: '0.35rem' }}>
                    <h3 style={{ margin: 0 }}>{exam.title}</h3>
                    {done && (
                      <span className="tag submitted">
                        <CheckCircle2 size={12} /> submitted
                      </span>
                    )}
                  </div>
                  {exam.description && <p className="muted">{exam.description}</p>}
                  <dl className="meta">
                    <div>
                      <dt>Duration</dt>
                      <dd>{exam.duration_minutes} minutes</dd>
                    </div>
                    {exam.ends_at && (
                      <div>
                        <dt>Closes</dt>
                        <dd>{new Date(exam.ends_at).toLocaleString()}</dd>
                      </div>
                    )}
                  </dl>

                  {done ? (
                    <div className="banner ok">
                      <CheckCircle2 size={16} />
                      <span>Submitted. Results will be published by your institution.</span>
                    </div>
                  ) : (
                    <>
                      <ul className="rules">
                        <li>
                          {exam.requires_seb
                            ? 'This exam must be taken inside Safe Exam Browser.'
                            : 'One attempt only. The timer starts as soon as you begin.'}
                        </li>
                        <li>Answers save automatically; refreshing is safe.</li>
                        <li>The exam submits itself when the timer reaches zero.</li>
                      </ul>
                      {exam.requires_seb && !inSafeExamBrowser ? (
                        <>
                          <a
                            className="btn primary full"
                            href={`seb://${window.location.host}/uploads/seb/${exam.id}.seb`}
                          >
                            Launch in Safe Exam Browser
                          </a>
                          <p className="muted small">
                            Don't have SEB installed?{' '}
                            <a href={`/uploads/seb/${exam.id}.seb`}>Download the config file</a> and
                            open it after installing Safe Exam Browser.
                          </p>
                        </>
                      ) : (
                        <button
                          className="btn primary full"
                          disabled={starting === exam.id}
                          onClick={() => void start(exam.id)}
                        >
                          {starting === exam.id ? (
                            <>
                              <TimerReset size={15} className="spinner" /> Starting…
                            </>
                          ) : (
                            'Start exam'
                          )}
                        </button>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
