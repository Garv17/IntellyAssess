import { AlertCircle, Loader2, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, type InviteRedeem } from '../api';

export default function InviteLanding() {
  const [params] = useSearchParams();
  const [invite, setInvite] = useState<InviteRedeem | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = params.get('token');
    if (!token) {
      setError('This link is missing its token.');
      return;
    }
    api
      .redeemInvite(token)
      .then(setInvite)
      .catch((err) => {
        setError(
          err instanceof Error ? err.message : 'This invite link is invalid or has expired.',
        );
      });
  }, [params]);

  return (
    <div className="auth-shell">
      <div className="card auth-card">
        <h1 style={{ textAlign: 'center' }}>Your exam PIN</h1>

        {error ? (
          <div className="banner error">
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        ) : !invite ? (
          <div
            style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', justifyContent: 'center' }}
          >
            <Loader2 size={18} className="spinner" />
            <span>Fetching your PIN…</span>
          </div>
        ) : (
          <>
            <p className="muted" style={{ textAlign: 'center' }}>
              for <strong>{invite.exam_title}</strong>
            </p>
            <div
              style={{
                fontSize: '2.4rem',
                fontWeight: 700,
                letterSpacing: '0.3em',
                textAlign: 'center',
                margin: '0.75rem 0',
              }}
            >
              {invite.pin}
            </div>
            <p className="muted small" style={{ textAlign: 'center' }}>
              Valid until {new Date(invite.pin_expires_at).toLocaleTimeString()}. Reload this page
              for a fresh PIN if it expires before you use it.
            </p>
            <a
              className="btn primary full"
              href={`seb://${window.location.host}/uploads/seb/${invite.exam_id}.seb`}
            >
              <ShieldCheck size={15} style={{ verticalAlign: -2, marginRight: 6 }} />
              Launch Safe Exam Browser
            </a>
            <p className="muted small" style={{ textAlign: 'center' }}>
              Once it opens, type the PIN above. Don't share it with anyone else.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
