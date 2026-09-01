import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, tokens, type RecoveryPreview } from '../api';
import { formatClock } from '../hooks/useExamTimer';

export default function ResumeExam() {
  const { token = '' } = useParams();
  const navigate = useNavigate();
  const [preview, setPreview] = useState<RecoveryPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);

  useEffect(() => {
    if (!token) {
      setError('This link is missing its token.');
      return;
    }
    api
      .recoveryPreview(token)
      .then((data) => {
        if (data.already_used) {
          setError('This recovery link has already been used.');
        } else {
          setPreview(data);
        }
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : 'This recovery link is invalid or has expired.'),
      );
  }, [token]);

  const continueExam = async () => {
    setContinuing(true);
    setError(null);
    try {
      const pair = await api.recoveryContinue(token);
      tokens.set(pair.access_token, pair.refresh_token, 'student');
      navigate('/exam', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not resume your exam.');
      setContinuing(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="card auth-card">
        {error ? (
          <>
            <div className="banner error">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
            <p className="muted small">Please contact your administrator for a new resume link.</p>
          </>
        ) : !preview ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', justifyContent: 'center' }}>
            <Loader2 size={18} className="spinner" />
            <span>Checking your exam session…</span>
          </div>
        ) : (
          <>
            <div className="banner ok">
              <CheckCircle2 size={16} />
              <span>Your previous exam session has been restored.</span>
            </div>
            <p>
              Previously answered questions: <strong>{preview.answered_count}</strong>
            </p>
            <p>
              Time remaining: <strong>{formatClock(preview.remaining_seconds)}</strong>
            </p>
            <p className="muted small">
              Your existing answers will be preserved — you'll continue from where you left off.
            </p>
            <button className="btn primary" style={{ width: '100%' }} disabled={continuing} onClick={() => void continueExam()}>
              {continuing ? 'Resuming…' : 'Continue Exam'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
