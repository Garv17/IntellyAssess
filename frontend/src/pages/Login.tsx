import { AlertCircle, FileBarChart2, Loader2, MailCheck } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, tokens } from '../api';

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
        <span className="brand-mark" style={{ width: 40, height: 40, borderRadius: 11 }}>
          <FileBarChart2 size={20} />
        </span>
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
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.requestMagicLink(email);
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send the sign-in link');
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <div className="card auth-card">
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '0.9rem' }}>
          <span className="brand-mark" style={{ width: 40, height: 40, borderRadius: 11 }}>
            <MailCheck size={20} />
          </span>
        </div>
        <h1 style={{ textAlign: 'center' }}>Check your email</h1>
        <p className="muted" style={{ textAlign: 'center' }}>
          If <strong>{email}</strong> is registered, a sign-in link is on its way. It expires in
          15 minutes and works once.
        </p>
        <button className="btn full" onClick={() => setSent(false)}>
          Use a different email
        </button>
        <p className="muted small">
          <Link to="/admin/login">Administrator sign in</Link>
        </p>
      </div>
    );
  }

  return (
    <form className="card auth-card" onSubmit={submit}>
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '0.9rem' }}>
        <span className="brand-mark" style={{ width: 40, height: 40, borderRadius: 11 }}>
          <FileBarChart2 size={20} />
        </span>
      </div>
      <h1 style={{ textAlign: 'center' }}>Examination portal</h1>
      <p className="muted" style={{ textAlign: 'center' }}>
        Enter your registered email and we'll send you a sign-in link.
      </p>

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

      {error && (
        <div className="banner error">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      <button className="btn primary full" disabled={busy}>
        {busy && <Loader2 size={15} className="spinner" />}
        {busy ? 'Sending…' : 'Send sign-in link'}
      </button>

      <p className="muted small">
        <Link to="/admin/login">Administrator sign in</Link>
      </p>
    </form>
  );
}

export default function Login({ admin = false }: { admin?: boolean }) {
  return (
    <div className="auth-shell">{admin ? <AdminLoginForm /> : <StudentLoginForm />}</div>
  );
}
