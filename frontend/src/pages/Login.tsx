import { AlertCircle, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, tokens } from '../api';
import logo from '../assets/logo.png';

function AdminLoginForm() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const pair = await api.adminLogin(email, password);
      tokens.set(pair.access_token, pair.refresh_token, 'admin');
      navigate('/admin', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card auth-card" onSubmit={submit}>
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '0.9rem' }}>
        <img src={logo} alt="IntellyAssess" className="brand-mark" style={{ width: 44, height: 44 }} />
      </div>
      <h1 style={{ textAlign: 'center' }}>Administrator sign in</h1>
      <p className="muted" style={{ textAlign: 'center' }}>Manage exams, students and results.</p>

      <label>
        Email
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          autoFocus
          required
        />
      </label>

      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </label>

      {error && (
        <div className="banner error">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      <button className="btn primary full" disabled={busy}>
        {busy && <Loader2 size={15} className="spinner" />}
        {busy ? 'Signing in…' : 'Sign in'}
      </button>

      <p className="muted small">
        <Link to="/">Student sign in</Link>
      </p>
    </form>
  );
}

function StudentLoginForm() {
  return (
    <div className="card auth-card">
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '0.9rem' }}>
        <img src={logo} alt="IntellyAssess" className="brand-mark" style={{ width: 44, height: 44 }} />
      </div>
      <h1 style={{ textAlign: 'center' }}>Examination portal</h1>
      <p className="muted" style={{ textAlign: 'center' }}>
        Open the sign-in link sent to your registered email to start your exam. Links are issued
        by your administrator. If you haven't received one, please contact them.
      </p>
    </div>
  );
}

export default function Login({ admin = false }: { admin?: boolean }) {
  return (
    <div className="auth-shell">{admin ? <AdminLoginForm /> : <StudentLoginForm />}</div>
  );
}
