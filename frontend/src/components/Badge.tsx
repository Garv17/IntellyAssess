import type { ReactNode } from 'react';

export type BadgeVariant = 'neutral' | 'blue' | 'success' | 'warning' | 'danger' | 'info';

interface Props {
  variant?: BadgeVariant;
  icon?: ReactNode;
  dot?: boolean;
  children: ReactNode;
}

export default function Badge({ variant = 'neutral', icon, dot, children }: Props) {
  return (
    <span className={`badge badge-${variant}${dot ? ' badge-dot' : ''}`}>
      {icon}
      {children}
    </span>
  );
}

/** Green/amber/red by percentage, relative to a pass threshold when known. */
export function ScoreBadge({
  percentage,
  passThreshold,
}: {
  percentage: number | null;
  passThreshold?: number | null;
}) {
  if (percentage == null) return <Badge variant="neutral">—</Badge>;
  const threshold = passThreshold ?? 40;
  const tier = percentage >= threshold + 15 ? 'high' : percentage >= threshold ? 'mid' : 'low';
  const variant = tier === 'high' ? 'success' : tier === 'mid' ? 'warning' : 'danger';
  return (
    <Badge variant={variant}>
      <span className="score-badge">{percentage}%</span>
    </Badge>
  );
}

export function DifficultyBadge({ difficulty }: { difficulty: string | null | undefined }) {
  if (!difficulty) return null;
  return <span className={`badge difficulty-${difficulty}`}>{difficulty}</span>;
}
