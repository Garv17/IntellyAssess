import type { ReactNode } from 'react';

export interface TabDef<T extends string> {
  key: T;
  label: string;
  icon?: ReactNode;
  count?: number;
}

interface Props<T extends string> {
  tabs: TabDef<T>[];
  value: T;
  onChange: (key: T) => void;
}

export default function Tabs<T extends string>({ tabs, value, onChange }: Props<T>) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={value === t.key}
          className={`tab ${value === t.key ? 'active' : ''}`}
          onClick={() => onChange(t.key)}
        >
          {t.icon}
          {t.label}
          {t.count != null && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}
