import { AlertCircle, Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, tokens } from '../api';

export default function MagicLinkCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const requested = useRef(false);

  useEffect(() => {
    const token = params.get('token');
    if (!token) {
      setError('This link is missing its token.');
      return;
    }
    // Guard against StrictMode's dev-mode double-invoke: this token is single-use,
    // so a second verify call would always fail with "already used".
    if (requested.current) return;
    requested.current = true;
    api
      .verifyMagicLink(token)
      .then((pair) => {
        tokens.set(pair.access_token, pair.refresh_token, 'student');
        navigate('/dashboard', { replace: true });
      })
      .catch((err) => {
        setError(
          err instanceof Error
            ? err.message
            : 'This link is invalid or has expired.',
        );
      });
  }, [params, navigate]);

  return (
    <div className="auth-shell">
      <div className="card auth-card">
        {error ? (
          <>
            <div className="banner error">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
            <p className="muted small">
              Please contact your administrator for a new sign-in link.
            </p>
          </>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', justifyContent: 'center' }}>
            <Loader2 size={18} className="spinner" />
            <span>Signing you in…</span>
          </div>
        )}
      </div>
    </div>
  );
}
