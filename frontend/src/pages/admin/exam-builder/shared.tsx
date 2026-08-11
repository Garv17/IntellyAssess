import { Plus, Trash2, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, type Difficulty, type ParamDef } from '../../../api';
import Markdown from '../../../components/Markdown';

export interface FormProps {
  sectionId: string;
  onDone: () => void;
  onError: (msg: string) => void;
}

// Must match the keys in backend/app/services/sandbox.py's LANGUAGES dict — that's
// what actually knows how to compile/run each one.
export const CODING_LANGUAGES = ['python', 'javascript', 'c', 'cpp', 'java', 'sql'];

// Must match app.services.harness.SUPPORTED_FUNCTION_LANGUAGES.
export const FUNCTION_LANGUAGES = ['python', 'javascript', 'java', 'cpp', 'c'];

// Must match app.services.harness.PARAM_TYPES — that's what actually knows how to
// decode/encode each one, per language.
export const PARAM_TYPES = [
  'int', 'float', 'bool', 'string', 'char',
  'int[]', 'float[]', 'bool[]', 'string[]', 'int[][]',
];

// Test cases run through a real program (or, for SQL, a real database) — not a
// structured LeetCode-style function call. This is the single most common way a
// coding question ends up broken: someone pastes a problem statement's example
// table directly into stdin/expected output instead of writing what the program
// actually reads and prints.
export function codingCaseHint(languages: string[]): string {
  if (languages.length > 0 && languages.every((l) => l === 'sql')) {
    return (
      "For SQL questions, stdin is the schema setup that runs before the student's " +
      "query (CREATE TABLE + INSERT statements), not a copy of the problem's " +
      'example table. Expected stdout is the query result: one row per line, columns ' +
      "separated by |, no header row. E.g. stdin: \"CREATE TABLE t(x INT); INSERT INTO " +
      't VALUES (1),(2);", expected stdout for `SELECT x FROM t;`: "1\\n2".'
    );
  }
  return (
    'stdin is exactly what a correct submission reads from standard input; expected ' +
    "stdout is exactly what it prints (trailing whitespace per line is ignored). Write " +
    "the literal input/output text; don't paste an example table from the problem " +
    'statement.'
  );
}

const BOILERPLATE_PLACEHOLDER: Record<string, string> = {
  python: 'class Solution:\n    def solve(self, ...):\n        pass',
  cpp: 'class Solution {\npublic:\n    // ...\n};',
  java: 'class Solution {\n    // ...\n}',
  javascript: 'function solve(...) {\n\n}',
  c: 'int* solve(...) {\n\n}',
  sql: '-- Leave blank, or seed a comment describing the schema the query should target.\n-- Students write the query itself; there is no function signature for SQL.',
};

export function boilerplateHint(lang: string): string {
  return BOILERPLATE_PLACEHOLDER[lang] ?? '';
}

export function emptyParam(): ParamDef {
  return { name: '', type: 'int' };
}

/** SQL-specific test case editor: setup SQL (schema + seed data) and expected result,
    plus a "run it for real" button so the admin never has to hand-compute a JOIN/GROUP
    BY result — the same trap the generic stdin/expected-stdout hint warns about. */
export function SqlCaseFields({
  stdin,
  expectedStdout,
  referenceQuery,
  onChange,
}: {
  stdin: string;
  expectedStdout: string;
  referenceQuery: string;
  onChange: (next: { stdin: string; expected_stdout: string }) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ stdout: string; error: string | null } | null>(null);

  const run = async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await api.previewSql(stdin, referenceQuery));
    } catch (err) {
      setResult({ stdout: '', error: err instanceof Error ? err.message : 'Preview failed' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="case-grid">
        <label>
          Setup SQL (schema + seed data for this case)
          <textarea
            value={stdin}
            rows={3}
            spellCheck={false}
            className="code-textarea"
            placeholder={'CREATE TABLE t(x INT);\nINSERT INTO t VALUES (1),(2);'}
            onChange={(e) => onChange({ stdin: e.target.value, expected_stdout: expectedStdout })}
          />
        </label>
        <label>
          Expected result (one row per line, columns separated by |, no header)
          <textarea
            value={expectedStdout}
            rows={3}
            spellCheck={false}
            className="code-textarea"
            placeholder="1\n2"
            onChange={(e) => onChange({ stdin, expected_stdout: e.target.value })}
          />
        </label>
      </div>
      <div className="stack" style={{ gap: '0.4rem' }}>
        <button
          type="button"
          className="btn"
          disabled={busy || !referenceQuery.trim()}
          title={!referenceQuery.trim() ? 'Fill in the reference query above first' : undefined}
          onClick={run}
        >
          {busy ? 'Running…' : 'Run reference query against this setup'}
        </button>
        {result?.error && (
          <p className="small" style={{ color: 'var(--danger)' }}>
            {result.error}
          </p>
        )}
        {result && !result.error && (
          <div className="stack" style={{ gap: '0.4rem' }}>
            <pre className="code-textarea sub-card" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
              {result.stdout || '(empty result set)'}
            </pre>
            <button
              type="button"
              className="btn link"
              onClick={() => onChange({ stdin, expected_stdout: result.stdout })}
            >
              Use this as the expected result
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** A Markdown textarea with a Preview toggle, so admins can check rendering
    (code blocks, tables, images) before saving without leaving the form. */
export function MarkdownField({
  label,
  value,
  onChange,
  rows = 6,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  required?: boolean;
}) {
  const [preview, setPreview] = useState(false);
  return (
    <div className="field">
      <div className="field-label-row">
        <span className="field-label">{label}</span>
        <button type="button" className="btn link" onClick={() => setPreview((p) => !p)}>
          {preview ? 'Edit' : 'Preview'}
        </button>
      </div>
      {preview ? (
        <div className="markdown-preview">
          <Markdown>{value || '*Nothing to preview yet.*'}</Markdown>
        </div>
      ) : (
        <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={rows} required={required} />
      )}
    </div>
  );
}

/** Dynamic name+type parameter list for a function signature — add/remove rows,
    type comes from the same closed registry the judge harness understands. */
export function ParametersEditor({
  parameters,
  onChange,
}: {
  parameters: ParamDef[];
  onChange: (parameters: ParamDef[]) => void;
}) {
  return (
    <div className="stack">
      {parameters.map((p, i) => (
        <div key={i} className="param-row">
          <input
            value={p.name}
            placeholder="paramName"
            onChange={(e) => {
              const next = [...parameters];
              next[i] = { ...p, name: e.target.value };
              onChange(next);
            }}
          />
          <select
            value={p.type}
            onChange={(e) => {
              const next = [...parameters];
              next[i] = { ...p, type: e.target.value };
              onChange(next);
            }}
          >
            {PARAM_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn link danger"
            onClick={() => onChange(parameters.filter((_, idx) => idx !== i))}
            title="Remove parameter"
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button type="button" className="btn" onClick={() => onChange([...parameters, emptyParam()])}>
        <Plus size={14} /> Add parameter
      </button>
    </div>
  );
}

/** Live boilerplate preview per language, fetched from the same
    generate_boilerplate() the judge actually uses at run time — never a
    hand-duplicated template — so this can never drift from what a student sees. */
export function BoilerplatePreview({
  functionName,
  returnType,
  parameters,
  languages,
}: {
  functionName: string;
  returnType: string;
  parameters: ParamDef[];
  languages: string[];
}) {
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const ready = functionName.trim().length > 0 && parameters.every((p) => p.name.trim().length > 0);
  const key = JSON.stringify({ functionName, returnType, parameters, languages });

  useEffect(() => {
    if (!ready || languages.length === 0) {
      setPreviews({});
      return;
    }
    const timer = window.setTimeout(() => {
      Promise.all(
        languages.map((lang) =>
          api
            .previewBoilerplate(lang, functionName, returnType, parameters)
            .then((r): [string, string] => [lang, r.code])
            .catch((): [string, string] => [lang, '(preview failed)']),
        ),
      ).then((entries) => setPreviews(Object.fromEntries(entries)));
    }, 400);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ready]);

  if (languages.length === 0) {
    return <p className="muted small">Select at least one language to preview its boilerplate.</p>;
  }
  if (!ready) {
    return <p className="muted small">Fill in the function name and every parameter name to preview.</p>;
  }

  return (
    <div className="boilerplate-preview-grid">
      {languages.map((lang) => (
        <div key={lang} className="boilerplate-preview-card">
          <div className="field-label">{lang}</div>
          <pre>{previews[lang] ?? '…'}</pre>
        </div>
      ))}
    </div>
  );
}

export function TagInput({ tags, onChange }: { tags: string[]; onChange: (tags: string[]) => void }) {
  const [draft, setDraft] = useState('');
  const commit = () => {
    const t = draft.trim();
    if (t && !tags.includes(t) && tags.length < 10) onChange([...tags, t]);
    setDraft('');
  };
  return (
    <div className="tag-input-row">
      {tags.map((t) => (
        <span key={t} className="tag-chip">
          {t}
          <button type="button" onClick={() => onChange(tags.filter((x) => x !== t))}>
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        placeholder={tags.length === 0 ? 'Add tags (press Enter)…' : ''}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commit();
          } else if (e.key === 'Backspace' && !draft && tags.length) {
            onChange(tags.slice(0, -1));
          }
        }}
        onBlur={commit}
      />
    </div>
  );
}

export function DifficultyPicker({
  value,
  onChange,
}: {
  value: Difficulty | null;
  onChange: (d: Difficulty | null) => void;
}) {
  const options: Difficulty[] = ['easy', 'medium', 'hard'];
  return (
    <div className="difficulty-picker">
      {options.map((d) => (
        <button
          key={d}
          type="button"
          className={`difficulty-option ${value === d ? `selected ${d}` : ''}`}
          onClick={() => onChange(value === d ? null : d)}
        >
          {d}
        </button>
      ))}
    </div>
  );
}
