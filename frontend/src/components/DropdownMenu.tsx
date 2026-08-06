import { MoreHorizontal } from 'lucide-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

interface ItemProps {
  icon?: ReactNode;
  danger?: boolean;
  onClick?: () => void;
  href?: string;
  children: ReactNode;
}

export function DropdownItem({ icon, danger, onClick, href, children }: ItemProps) {
  const className = `dropdown-item${danger ? ' danger' : ''}`;
  if (href) {
    return (
      <a className={className} href={href}>
        {icon}
        {children}
      </a>
    );
  }
  return (
    <button type="button" className={className} onClick={onClick}>
      {icon}
      {children}
    </button>
  );
}

export function DropdownSeparator() {
  return <div className="dropdown-sep" />;
}

interface Props {
  trigger?: ReactNode;
  align?: 'left' | 'right';
  children: ReactNode;
}

/**
 * Icon-button trigger + floating panel, rendered through a portal so it can't be
 * clipped by a scrolling ancestor (e.g. a table wrapped in overflow-x:auto, which
 * forces overflow-y:auto too per the CSS overflow spec). Position is computed from
 * the trigger's bounding rect and the panel closes on scroll rather than tracking it,
 * which is simpler and matches how most menus behave when the page moves under them.
 */
export default function DropdownMenu({ trigger, align = 'right', children }: Props) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const openMenu = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) {
      setCoords(
        align === 'left'
          ? { top: rect.bottom + 6, left: rect.left }
          : { top: rect.bottom + 6, left: rect.right },
      );
    }
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current && !menuRef.current.contains(target)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onScroll = () => setOpen(false);
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, [open]);

  return (
    <div className="dropdown">
      <button
        ref={triggerRef}
        type="button"
        className="btn icon ghost"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openMenu())}
      >
        {trigger ?? <MoreHorizontal size={16} />}
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            className="dropdown-menu portal"
            role="menu"
            style={{
              position: 'fixed',
              top: coords.top,
              left: align === 'left' ? coords.left : undefined,
              right: align === 'left' ? undefined : `calc(100vw - ${coords.left}px)`,
            }}
            onClick={() => setOpen(false)}
          >
            {children}
          </div>,
          document.body,
        )}
    </div>
  );
}
