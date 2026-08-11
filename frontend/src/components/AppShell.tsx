import { FileBarChart2, LayoutGrid, LogOut, Menu, Users, X } from 'lucide-react';
import { type ReactNode, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { tokens } from '../api';
import { Identity } from './Avatar';
import DropdownMenu, { DropdownItem } from './DropdownMenu';

const NAV = [
  { to: '/admin', label: 'Exams', icon: <LayoutGrid size={17} />, match: (p: string) => p === '/admin' || p.startsWith('/admin/exams') },
  { to: '/admin/student-details', label: 'Student Details', icon: <Users size={17} />, match: (p: string) => p.startsWith('/admin/student-details') },
];

interface Props {
  title: ReactNode;
  actions?: ReactNode;
  adminName?: string;
  children: ReactNode;
}

export default function AppShell({ title, actions, adminName, children }: Props) {
  const location = useLocation();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);

  const signOut = () => {
    tokens.clear();
    navigate('/admin/login', { replace: true });
  };

  return (
    <div className="app-shell">
      {mobileOpen && <div className="app-shell-scrim" onClick={() => setMobileOpen(false)} />}
      <aside className={`app-sidebar ${mobileOpen ? 'open' : ''}`}>
        <div className="app-sidebar-brand">
          <span className="brand">
            <span className="brand-mark">
              <FileBarChart2 size={16} />
            </span>
            IntellyAssess
          </span>
        </div>
        <nav className="app-nav">
          <span className="app-nav-section">Workspace</span>
          {NAV.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`app-nav-link ${item.match(location.pathname) ? 'active' : ''}`}
              onClick={() => setMobileOpen(false)}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="app-sidebar-footer">
          <button className="app-nav-link" onClick={signOut}>
            <LogOut size={17} />
            Sign out
          </button>
        </div>
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button
              className="mobile-nav-toggle"
              onClick={() => setMobileOpen((v) => !v)}
              aria-label="Toggle navigation"
            >
              {mobileOpen ? <X size={18} /> : <Menu size={18} />}
            </button>
            <h1>{title}</h1>
          </div>
          <div className="app-topbar-right">
            {actions}
            <DropdownMenu
              align="right"
              trigger={adminName ? <Identity name={adminName} size="sm" /> : undefined}
            >
              <DropdownItem icon={<LogOut size={15} />} onClick={signOut} danger>
                Sign out
              </DropdownItem>
            </DropdownMenu>
          </div>
        </header>
        <main className="app-page">{children}</main>
      </div>
    </div>
  );
}
