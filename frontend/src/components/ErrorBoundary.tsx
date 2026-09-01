import { AlertCircle } from 'lucide-react';
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { getRequestId, log } from '../logger';

interface Props {
  children: ReactNode;
}
interface State {
  hasError: boolean;
  requestId: string | null;
}

/**
 * Last line of defence around the whole router.
 *
 * Before this existed a render crash mid-exam produced a white screen: no
 * message, no trace, and a student with no idea whether their work survived.
 * The fallback's job is only to answer that question calmly and get them back
 * in — recovery is a reload, not a retry button, because the crashed tree's
 * state is exactly what we don't want to resume from.
 *
 * Note this catches render/lifecycle errors only; throws from event handlers
 * and promises reach the window handlers installed by `installGlobalErrorHandlers`.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, requestId: null };

  static getDerivedStateFromError(): Partial<State> {
    // Capture the correlation ID at crash time — later API calls would
    // otherwise overwrite it before the student reads it off the screen.
    return { hasError: true, requestId: getRequestId() };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    log.error('react render crash', {
      error_name: error.name,
      error_message: error.message,
      stack: error.stack,
      component_stack: info.componentStack,
      path: window.location.pathname,
    });
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="auth-shell">
        <div className="card auth-card">
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', margin: 0 }}>
            <AlertCircle size={20} color="var(--danger)" />
            Something went wrong
          </h2>
          <p className="muted" style={{ marginTop: '0.85rem' }}>
            The page stopped responding. <strong>Your answers are saved</strong> — auto-save runs
            continuously and everything you submitted has already reached the server.
          </p>
          <p className="muted">
            Refresh this page to carry on from where you left off. Your exam timer keeps running on
            the server, so nothing has been lost or reset.
          </p>
          <button className="btn primary full" onClick={() => window.location.reload()}>
            Refresh and continue
          </button>
          {this.state.requestId && (
            // Shown so a student raising a ticket can quote the ID that ties
            // their screen to the backend's log line for the same request.
            <p className="muted small" style={{ marginTop: '0.9rem' }}>
              Reference: {this.state.requestId}
            </p>
          )}
        </div>
      </div>
    );
  }
}
