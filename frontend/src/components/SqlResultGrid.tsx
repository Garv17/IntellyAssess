import { diffRowIndexes, parsePipeRows } from '../lib/sqlPreview';

/** Renders pipe-delimited SQL result text (one row per line, no header) as a real,
    professional-looking table — real column names when the admin provided them
    (`columnNames`, from CodingProblem.sql_result_columns), else synthetic "Col N".
    When `compareTo` is given, rows that don't match at the same index are
    highlighted — the expected-vs-actual diff a Wrong Answer verdict needs.
    Renders nothing when there's no parseable content; callers keep a <pre> fallback. */
export default function SqlResultGrid({
  text,
  compareTo,
  compact,
  columnNames,
  caption,
}: {
  text: string | null | undefined;
  compareTo?: string | null;
  compact?: boolean;
  columnNames?: string[] | null;
  caption?: string;
}) {
  const rows = parsePipeRows(text);
  if (rows.length === 0) return null;

  const compareRows = compareTo != null ? parsePipeRows(compareTo) : null;
  const mismatches = compareRows ? diffRowIndexes(compareRows, rows) : null;
  const columnCount = Math.max(...rows.map((r) => r.length), columnNames?.length ?? 0);

  return (
    <div className={`sql-result-table-wrap ${compact ? 'compact' : ''}`}>
      <table className="sql-result-table">
        <thead>
          <tr>
            {Array.from({ length: columnCount }, (_, i) => (
              <th key={i}>{columnNames?.[i] ?? `Col ${i + 1}`}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={mismatches?.[i] ? 'sql-row-mismatch' : ''}>
              {Array.from({ length: columnCount }, (_, j) => (
                <td key={j}>{row[j] ?? ''}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!compact && (
        <div className="sql-result-caption muted small">
          {rows.length} row{rows.length === 1 ? '' : 's'}
          {caption ? ` · ${caption}` : ''}
        </div>
      )}
    </div>
  );
}
