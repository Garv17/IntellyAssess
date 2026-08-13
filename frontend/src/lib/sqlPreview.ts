// Best-effort rendering helpers for SQL test cases: turns the raw setup script
// (CREATE TABLE + INSERT statements) and pipe-delimited result text into
// structured data for preview components. This never touches grading — the
// stored expected output is still raw text, compared by a human reviewer byte for
// byte. A parse miss here just means a caller falls back to raw <pre> text.

export interface ParsedColumn {
  name: string;
  type: string;
  isPrimaryKey?: boolean;
  isForeignKey?: boolean;
}

export interface ParsedTable {
  name: string;
  columns: ParsedColumn[];
  rows: string[][];
}

export interface ParsedSqlSetup {
  tables: ParsedTable[];
  warnings: string[];
}

const CONSTRAINT_KEYWORDS = /^(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT)\b/i;

/** Splits on a separator character, ignoring separators inside quotes or nested parens. */
function splitTopLevel(text: string, sep: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = '';
  let quote: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      current += ch;
      if (ch === quote) {
        if (text[i + 1] === quote) {
          current += text[++i];
        } else {
          quote = null;
        }
      }
      continue;
    }
    if (ch === "'" || ch === '"' || ch === '`') {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === '(') {
      depth++;
      current += ch;
      continue;
    }
    if (ch === ')') {
      depth--;
      current += ch;
      continue;
    }
    if (ch === sep && depth === 0) {
      parts.push(current);
      current = '';
      continue;
    }
    current += ch;
  }
  parts.push(current);
  return parts;
}

/** Splits a script into individual statements on top-level `;`. */
function splitStatements(sql: string): string[] {
  return splitTopLevel(sql, ';');
}

/** Pulls `(a, b), (c, d)` style value tuples out of an INSERT's VALUES clause. */
function extractTuples(valuesText: string): string[] {
  const tuples: string[] = [];
  let depth = 0;
  let current = '';
  let quote: string | null = null;
  for (let i = 0; i < valuesText.length; i++) {
    const ch = valuesText[i];
    if (quote) {
      current += ch;
      if (ch === quote) {
        if (valuesText[i + 1] === quote) {
          current += valuesText[++i];
        } else {
          quote = null;
        }
      }
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      if (depth > 0) current += ch;
      continue;
    }
    if (ch === '(') {
      depth++;
      if (depth > 1) current += ch;
      continue;
    }
    if (ch === ')') {
      depth--;
      if (depth === 0) {
        tuples.push(current);
        current = '';
      } else {
        current += ch;
      }
      continue;
    }
    if (depth > 0) current += ch;
  }
  return tuples;
}

function unquote(value: string): string {
  if (value.length >= 2) {
    const first = value[0];
    const last = value[value.length - 1];
    if ((first === "'" && last === "'") || (first === '"' && last === '"')) {
      return value
        .slice(1, -1)
        .replace(/''/g, "'")
        .replace(/""/g, '"');
    }
  }
  return value;
}

/** Parses a test case's setup SQL into per-table schema + sample rows, plus any
    authoring problems detected (currently: INSERT into a table with no CREATE
    TABLE in the same script — the exact bug class that produces a bare
    "no such table" runtime error). */
export function parseSqlSetup(sql: string): ParsedSqlSetup {
  const tables = new Map<string, ParsedTable>();
  const handled = new Set<string>(); // created, or already warned about
  const warnings: string[] = [];

  for (const stmt of splitStatements(sql)) {
    const trimmed = stmt.trim();
    if (!trimmed) continue;

    const createMatch = trimmed.match(
      /^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["'`]?(\w+)["'`]?\s*\(([\s\S]*)\)\s*$/i,
    );
    if (createMatch) {
      const name = createMatch[1];
      const columns: ParsedColumn[] = [];
      const constraintLines: string[] = [];
      for (const colDef of splitTopLevel(createMatch[2], ',')) {
        const c = colDef.trim();
        if (!c) continue;
        if (CONSTRAINT_KEYWORDS.test(c)) {
          constraintLines.push(c);
          continue;
        }
        const m = c.match(/^["'`]?(\w+)["'`]?\s+([\s\S]+)$/);
        const colName = m ? m[1] : c;
        const colType = m ? m[2].trim() : '';
        columns.push({
          name: colName,
          type: colType,
          isPrimaryKey: /PRIMARY\s+KEY/i.test(colType) || undefined,
          isForeignKey: /REFERENCES/i.test(colType) || undefined,
        });
      }
      // Table-level constraints (PRIMARY KEY (a, b), FOREIGN KEY (a) REFERENCES ...)
      // tag the columns they name instead of being discarded outright.
      for (const line of constraintLines) {
        const isForeign = /^FOREIGN\s+KEY/i.test(line);
        const isPrimary = /^PRIMARY\s+KEY/i.test(line);
        if (!isForeign && !isPrimary) continue;
        const colsMatch = line.match(/\(([^()]*)\)/);
        if (!colsMatch) continue;
        const names = splitTopLevel(colsMatch[1], ',').map((n) => n.trim().replace(/["'`]/g, '').toLowerCase());
        for (const col of columns) {
          if (!names.includes(col.name.toLowerCase())) continue;
          if (isPrimary) col.isPrimaryKey = true;
          if (isForeign) col.isForeignKey = true;
        }
      }
      tables.set(name.toLowerCase(), { name, columns, rows: [] });
      handled.add(name.toLowerCase());
      continue;
    }

    const insertMatch = trimmed.match(
      /^INSERT\s+INTO\s+["'`]?(\w+)["'`]?\s*(?:\(([^()]*)\))?\s*VALUES\s*([\s\S]*)$/i,
    );
    if (insertMatch) {
      const name = insertMatch[1];
      const key = name.toLowerCase();
      let table = tables.get(key);
      if (!table) {
        const explicitCols = insertMatch[2];
        const inferredCols = explicitCols
          ? splitTopLevel(explicitCols, ',').map((c) => ({ name: c.trim(), type: '' }))
          : [];
        table = { name, columns: inferredCols, rows: [] };
        tables.set(key, table);
      }
      if (!handled.has(key)) {
        warnings.push(
          `Table '${name}' is inserted into but never created (no CREATE TABLE) in this setup SQL — it will fail with "no such table" at runtime.`,
        );
        handled.add(key);
      }
      for (const tuple of extractTuples(insertMatch[3])) {
        const cells = splitTopLevel(tuple, ',').map((c) => unquote(c.trim()));
        table.rows.push(cells);
        if (table.columns.length === 0) {
          table.columns = cells.map((_, i) => ({ name: `col${i + 1}`, type: '' }));
        }
      }
    }
  }

  return { tables: Array.from(tables.values()), warnings };
}

/** Parses `expected_stdout`/actual-output text (one row per line, columns
    separated by `|`, no header) into a row/cell grid. */
export function parsePipeRows(text: string | null | undefined): string[][] {
  if (!text) return [];
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => line.split('|').map((cell) => cell.trim()));
}

/** Row-index-aligned mismatch flags, mirroring how grading actually compares
    output (exact text, row order matters). */
export function diffRowIndexes(expected: string[][], actual: string[][]): boolean[] {
  const len = Math.max(expected.length, actual.length);
  const mismatches: boolean[] = [];
  for (let i = 0; i < len; i++) {
    const e = expected[i];
    const a = actual[i];
    mismatches.push(!e || !a || e.length !== a.length || e.some((v, j) => v !== a[j]));
  }
  return mismatches;
}
