import { AlertCircle, CheckCircle2, Loader2, WifiOff } from 'lucide-react';

export default function SaveIndicator({
  status,
  lastSavedAt,
}: {
  status: string;
  lastSavedAt: Date | null;
}) {
  const label =
    status === 'saving'
      ? 'Saving…'
      : status === 'pending'
        ? 'Unsaved changes'
        : status === 'offline'
          ? 'Reconnecting…'
          : lastSavedAt
            ? `Saved ${lastSavedAt.toLocaleTimeString()}`
            : 'All answers saved';
  const icon =
    status === 'saving' ? (
      <Loader2 size={14} className="spinner" />
    ) : status === 'pending' ? (
      <AlertCircle size={14} />
    ) : status === 'offline' ? (
      <WifiOff size={14} />
    ) : (
      <CheckCircle2 size={14} />
    );
  return (
    <span className={`save-indicator ${status}`}>
      {icon}
      {label}
    </span>
  );
}
