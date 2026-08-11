import { AlertTriangle, ChevronDown, ChevronRight, KeyRound, Link2 } from 'lucide-react';
import { useState } from 'react';
import { parseSqlSetup, type ParsedColumn, type ParsedTable } from '../lib/sqlPreview';

const PREVIEW_ROW_LIMIT_COMPACT = 3;
const PREVIEW_ROW_LIMIT_FULL = 8;

function headerColumns(columns: ParsedColumn[], firstRow: string[] | undefined): ParsedColumn[] {
  if (columns.length > 0) return columns;
  return (firstRow ?? []).map((_, i) => ({ name: `col${i + 1}`, type: '' }));
}

function TableCard({ table, rowLimit }: { table: ParsedTable; rowLimit: number }) {
  const [open, setOpen] = useState(true);
  const columns = headerColumns(table.columns, table.rows[0]);

  return (
    <div className="sql-table-block">
      <button type="button" className="sql-table-name" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        {table.name}
        <span className="sql-table-row-count muted">
          {table.rows.length} row{table.rows.length === 1 ? '' : 's'}
        </span>
      </button>
      {open && columns.length > 0 && (
        <div className="sql-data-table-wrap">
          <table className="sql-data-table">
            <thead>
              <tr>
                {columns.map((c, i) => (
                  <th key={`${c.name}-${i}`}>
                    <span className="sql-col-name">{c.name}</span>
                    {c.isPrimaryKey && (
                      <span className="sql-key-tag pk" title="Primary key">
                        <KeyRound size={10} /> PK
                      </span>
                    )}
                    {c.isForeignKey && (
                      <span className="sql-key-tag fk" title="Foreign key">
                        <Link2 size={10} /> FK
                      </span>
                    )}
                    {c.type && <span className="sql-col-type">{c.type}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            {table.rows.length > 0 && (
              <tbody>
                {table.rows.slice(0, rowLimit).map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            )}
          </table>
          {table.rows.length > rowLimit && (
            <div className="sql-more-rows muted small">
              +{table.rows.length - rowLimit} more row{table.rows.length - rowLimit === 1 ? '' : 's'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Renders a schema/seed SQL script as expandable "TableName" cards — one per
    table, each showing its columns (name, type, PK/FK tags) and a few sample
    rows. Used by the admin schema editor, admin grading review, and the
    student SQL workspace's schema explorer. Renders nothing when no CREATE
    TABLE can be parsed; callers should keep a <pre> fallback for that case. */
export default function SqlSchemaExplorer({ sql, compact }: { sql: string; compact?: boolean }) {
  const { tables, warnings } = parseSqlSetup(sql);
  if (tables.length === 0 && warnings.length === 0) return null;

  const rowLimit = compact ? PREVIEW_ROW_LIMIT_COMPACT : PREVIEW_ROW_LIMIT_FULL;

  return (
    <div className={`sql-schema-preview ${compact ? 'compact' : ''}`}>
      {warnings.map((w) => (
        <p key={w} className="sql-warning">
          <AlertTriangle size={13} />
          {w}
        </p>
      ))}
      {tables.map((table) => (
        <TableCard key={table.name} table={table} rowLimit={rowLimit} />
      ))}
    </div>
  );
}
