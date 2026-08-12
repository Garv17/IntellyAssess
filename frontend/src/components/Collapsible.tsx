import { ChevronRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';

interface Props {
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
}

export default function Collapsible({ title, defaultOpen = true, children, className }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`collapsible${className ? ` ${className}` : ''}`}>
      <button
        type="button"
        className="collapsible-head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <ChevronRight size={15} className={`chevron ${open ? 'open' : ''}`} />
        {title}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}
