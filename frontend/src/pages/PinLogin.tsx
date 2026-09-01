import { AlertCircle, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError, api, tokens } from '../api';
import { errorContext, log } from '../logger';

export default function PinLogin() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const examId = params.get('exam') ?? '';
  const [pin, setPin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!examId) {
      setError('This page was opened without an exam reference — check the Start URL.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const pair = await api.pinLogin(examId, pin);
      tokens.set(pair.access_token, pair.refresh_token, 'student');
      navigate('/dashboard', { replace: true });
    } catch (err) {
      // The PIN itself is never logged — this is a live exam credential.
      log.warn('pin login failed', { exam_id: examId, ...errorContext(err) });
      setError(err instanceof ApiError ? err.message : 'Could not sign in with that PIN.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="card auth-card">
        <h1 style={{ textAlign: 'center' }}>Enter your PIN</h1>
        <p className="muted" style={{ textAlign: 'center' }}>
          Open the link emailed to you (in a normal browser, outside Safe Exam Browser) to see
          your PIN, then type it here.
        </p>
        <form className="stack" onSubmit={submit}>
          <label>
            PIN
            <input
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              inputMode="numeric"
              maxLength={8}
              autoFocus
              required
            />
          </label>
          {error && (
            <div className="banner error">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}
          <div className="form-actions">
            <button className="btn primary full" disabled={busy}>
              {busy ? <Loader2 size={15} className="spinner" /> : 'Continue'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
