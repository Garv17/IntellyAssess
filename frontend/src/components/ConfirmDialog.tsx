import { AlertTriangle, Info } from 'lucide-react';
import type { ReactNode } from 'react';

interface Props {
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: 'danger' | 'info';
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Modal replacement for window.confirm() — same modal-backdrop/modal shell used
    elsewhere in the app, with a consistent icon/tone and busy state. */
export default function ConfirmDialog({
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'danger',
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal">
        <div className={`modal-icon ${tone}`}>
          {tone === 'danger' ? <AlertTriangle size={20} /> : <Info size={20} />}
        </div>
        <h3>{title}</h3>
        <p className="muted">{description}</p>
        <div className="modal-actions">
          <button className="btn" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button className={`btn ${tone === 'danger' ? 'danger' : 'primary'}`} onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
