const PALETTE = ['#2563eb', '#7c3aed', '#059669', '#d97706', '#db2777', '#0891b2', '#4f46e5', '#65a30d'];

function hashName(name: string): number {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return hash;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

interface Props {
  name: string;
  size?: 'sm' | 'md' | 'lg';
}

export default function Avatar({ name, size = 'md' }: Props) {
  const color = PALETTE[hashName(name) % PALETTE.length];
  return (
    <span className={`avatar avatar-${size}`} style={{ background: color }} aria-hidden="true">
      {initials(name)}
    </span>
  );
}

export function Identity({
  name,
  sub,
  size = 'md',
}: {
  name: string;
  sub?: string | null;
  size?: 'sm' | 'md' | 'lg';
}) {
  return (
    <span className="avatar-row">
      <Avatar name={name} size={size} />
      <span>
        <span className="identity-name">{name}</span>
        {sub && (
          <>
            <br />
            <span className="identity-sub">{sub}</span>
          </>
        )}
      </span>
    </span>
  );
}
