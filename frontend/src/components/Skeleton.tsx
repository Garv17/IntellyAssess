export function SkeletonText({ width = '100%' }: { width?: string | number }) {
  return <div className="skeleton skeleton-text" style={{ width }} />;
}

export function SkeletonCard() {
  return <div className="skeleton skeleton-card" />;
}

export function SkeletonRow() {
  return <div className="skeleton skeleton-row" />;
}

export function SkeletonAvatar({ size = 34 }: { size?: number }) {
  return <div className="skeleton skeleton-avatar" style={{ width: size, height: size }} />;
}

export function SkeletonStatRow({ count = 4 }: { count?: number }) {
  return (
    <div className="stat-row">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="stat">
          <div className="skeleton skeleton-avatar" style={{ width: 34, height: 34, borderRadius: 8 }} />
          <SkeletonText width="60%" />
          <SkeletonText width="40%" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div>
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  );
}

export function SkeletonCardGrid({ count = 3 }: { count?: number }) {
  return (
    <div className="card-grid">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
