import { AlertCircle, FileBarChart2, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, tokens } from '../api';

export default function Login({ admin = false }: { admin?: boolean }) {
  const navigate = useNavigate();
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const pair = admin
        ? await api.adminLogin(identifier, password)
        : await api.studentLogin(identifier, password);
      tokens.set(pair.access_token, pair.refresh_token, admin ? 'admin' : 'student');
      navigate(admin ? '/admin' : '/dashboard', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-shell">
      <form className="card auth-card" onSubmit={submit}>
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '0.9rem' }}>
          <span className="brand-mark" style={{ width: 40, height: 40, borderRadius: 11 }}>
            <FileBarChart2 size={20} />
          </span>
        </div>
        <h1 style={{ textAlign: 'center' }}>{admin ? 'Administrator sign in' : 'Examination portal'}</h1>
        <p className="muted" style={{ textAlign: 'center' }}>
          {admin ? 'Manage exams, students and results.' : 'Sign in with your Enrollment ID.'}
        </p>

        <label>
          {admin ? 'Email' : 'Enrollment ID'}
          <input
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            autoComplete={admin ? 'email' : 'username'}
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
          {admin ? (
            <Link to="/">Student sign in</Link>
          ) : (
            <Link to="/admin/login">Administrator sign in</Link>
          )}
        </p>
      </form>
    </div>
  );
}
