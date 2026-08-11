import { CheckCircle2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { api, tokens, type Receipt } from '../api';

export default function Submitted() {
  const location = useLocation();
  const navigate = useNavigate();
  const [receipt, setReceipt] = useState<Receipt | null>((location.state as Receipt) ?? null);

  // Reached by a direct visit or a refresh: fetch the receipt so the confirmation
  // is never a blank page.
  useEffect(() => {
    if (receipt) return;
    api
      .submit(false)
      .then(setReceipt)
      .catch(() => {
        void api
          .me()
          .then(() => undefined)
          .catch(() => navigate('/', { replace: true }));
      });
  }, [receipt, navigate]);

  const signOut = () => {
    tokens.clear();
    navigate('/', { replace: true });
  };

  return (
    <div className="auth-shell">
      <div className="card auth-card" style={{ textAlign: 'center' }}>
        <div className="tick">
          <CheckCircle2 size={26} />
        </div>
        <h1>Submitted!</h1>
        <p className="muted">You made it to the finish line. Nicely done.</p>
        <p className="muted">Go take a well-earned break.</p>

        <button className="btn full" onClick={signOut}>
          Sign out
        </button>
      </div>
    </div>
  );
}
