import { ImagePlus, Plus, Trash2, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, type Difficulty, type ParamDef } from '../../../api';
import Markdown from '../../../components/Markdown';
import SqlResultGrid from '../../../components/SqlResultGrid';
import SqlSchemaExplorer from '../../../components/SqlSchemaExplorer';
import { errorContext, log } from '../../../logger';

export interface FormProps {
  sectionId: string;
  onDone: () => void;
  onError: (msg: string) => void;
}

// Must match backend.app.schemas.CodingLanguage. SQL questions use the dedicated
// SqlQuestionForm/SqlQuestionEditForm instead of this generic programming-language
// form, so it isn't offered here as a checkbox.
export const CODING_LANGUAGES = ['python', 'javascript', 'c', 'cpp', 'java'];

// Display metadata only — it labels the student's editor and tells the AI
// evaluator which dialect to mark against. Must match backend.app.schemas.SqlDialect.
export const SQL_DIALECTS: { value: string; label: string }[] = [
  { value: 'sqlite', label: 'SQLite' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mssql', label: 'SQL Server' },
];

// Must match app.services.harness.SUPPORTED_FUNCTION_LANGUAGES.
export const FUNCTION_LANGUAGES = ['python', 'javascript', 'java', 'cpp', 'c'];

// Must match app.services.harness.PARAM_TYPES — that's what renders each one into
// the per-language starter code.
export const PARAM_TYPES = [
  'int', 'float', 'bool', 'string', 'char',
  'int[]', 'float[]', 'bool[]', 'string[]', 'int[][]',
];

// Test cases describe what a real program would read and print — not a structured
// LeetCode-style function call. This is the single most common way a coding question
// ends up unusable: someone pastes a problem statement's example table directly into
// stdin/expected output instead of writing what the program actually reads and prints.
export function codingCaseHint(): string {
  return (
    'stdin is exactly what a correct submission reads from standard input; expected ' +
    "stdout is exactly what it prints. Sample cases are shown to students as worked " +
    'examples; hidden cases are optional and only give the AI evaluator extra context ' +
    "about the intended behaviour. Write the literal input/output text; don't paste an " +
    'example table from the problem statement.'
  );
}

const BOILERPLATE_PLACEHOLDER: Record<string, string> = {
  python: 'class Solution:\n    def solve(self, ...):\n        pass',
  cpp: 'class Solution {\npublic:\n    // ...\n};',
  java: 'class Solution {\n    // ...\n}',
  javascript: 'function solve(...) {\n\n}',
  c: 'int* solve(...) {\n\n}',
};

export function boilerplateHint(lang: string): string {
  return BOILERPLATE_PLACEHOLDER[lang] ?? '';
}

export function emptyParam(): ParamDef {
  return { name: '', type: 'int' };
}

/** Database dialect picker for SQL questions. Display metadata only: it labels
    the student's editor and tells the AI evaluator which dialect to mark
    against. Nothing executes the query either way. */
export function SqlDialectPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="field">
      <span className="field-label">Database dialect</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {SQL_DIALECTS.map((d) => (
          <option key={d.value} value={d.value}>
            {d.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** The question-level schema/seed data editor — one shared script every test
    case's query runs against, instead of each test case duplicating its own
    CREATE TABLE + INSERT setup. Live preview reuses the same parser the
    student-facing schema explorer uses, so what the admin sees while typing is
    exactly what students will see. */
export function SqlSchemaEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="field">
      <span className="field-label">Schema &amp; seed data (SQL)</span>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={8}
        spellCheck={false}
        className="code-textarea"
        placeholder={
          'CREATE TABLE employees (\n  id INT PRIMARY KEY,\n  name TEXT,\n  dept_id INT\n);\n' +
          "INSERT INTO employees VALUES (1, 'Aarav', 10), (2, 'Bhavna', 20);"
        }
        required
      />
      <p className="muted small">
        Write the full schema once — CREATE TABLE + INSERT statements. Every test case's query runs
        against this same schema; only add per-case setup below for a rare case-specific variant.
      </p>
      {value.trim() && <SqlSchemaExplorer sql={value} compact />}
    </div>
  );
}

/** Per-test-case SQL fields: an optional rare per-case setup addendum, a scratch
    reference query (not saved to the question, but lifted up to the parent form
    so "Preview student view" can show it), and the expected result.

    The reference query used to drive a real "run it" preview against the judge
    sandbox. This build has no sandbox, so the expected result is typed in by
    hand — the same as for every other language. */
export function SqlTestCaseFields({
  extraSetup,
  expectedStdout,
  referenceQuery,
  onReferenceQueryChange,
  onChange,
}: {
  extraSetup: string;
  expectedStdout: string;
  referenceQuery: string;
  onReferenceQueryChange: (v: string) => void;
  onChange: (next: { stdin: string; expected_stdout: string }) => void;
}) {
  const [showExtra, setShowExtra] = useState(extraSetup.trim().length > 0);

  return (
    <div className="stack">
      <button type="button" className="btn link" onClick={() => setShowExtra((s) => !s)}>
        {showExtra ? 'Hide' : 'Add'} case-specific extra setup (rare)
      </button>
      {showExtra && (
        <label>
          Extra setup for this case only (optional, runs after the shared schema above)
          <textarea
            value={extraSetup}
            rows={2}
            spellCheck={false}
            className="code-textarea"
            placeholder="INSERT INTO employees VALUES (99, 'Extra', 10);"
            onChange={(e) => onChange({ stdin: e.target.value, expected_stdout: expectedStdout })}
          />
        </label>
      )}
      <label>
        Reference query (your own working — not saved, not shown to students)
        <textarea
          value={referenceQuery}
          rows={3}
          spellCheck={false}
          className="code-textarea"
          placeholder="SELECT name FROM employees WHERE dept_id = 10;"
          onChange={(e) => onReferenceQueryChange(e.target.value)}
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
          onChange={(e) => onChange({ stdin: extraSetup, expected_stdout: e.target.value })}
        />
        {expectedStdout.trim() && <SqlResultGrid text={expectedStdout} compact />}
      </label>
    </div>
  );
}

/** A Markdown textarea with a Preview toggle, so admins can check rendering
    (code blocks, tables, images) before saving without leaving the form.
    `allowImageUpload` adds an "Insert image" button that uploads a file and
    appends its markdown image tag to the field — the only way to put an
    image into an MCQ/coding question body, since unlike a DI group there is
    no separate image_url column for these question types. */
export function MarkdownField({
  label,
  value,
  onChange,
  rows = 6,
  required = false,
  allowImageUpload = false,
  onError,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  required?: boolean;
  allowImageUpload?: boolean;
  onError?: (msg: string) => void;
}) {
  const [preview, setPreview] = useState(false);
  const [uploading, setUploading] = useState(false);

  const insertImage = async (file: File) => {
    setUploading(true);
    try {
      const { image_url } = await api.uploadImage(file);
      const tag = `![image](${image_url})`;
      onChange(value.trim() ? `${value}\n\n${tag}` : tag);
    } catch (err) {
      onError?.(err instanceof Error ? err.message : 'Image upload failed');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="field">
      <div className="field-label-row">
        <span className="field-label">{label}</span>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {allowImageUpload && (
            <label className="btn link" style={{ cursor: 'pointer' }}>
              <ImagePlus size={13} /> {uploading ? 'Uploading…' : 'Insert image'}
              <input
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void insertImage(file);
                  e.target.value = '';
                }}
              />
            </label>
          )}
          <button type="button" className="btn link" onClick={() => setPreview((p) => !p)}>
            {preview ? 'Edit' : 'Preview'}
          </button>
        </div>
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
    type comes from the same closed registry the boilerplate generator understands. */
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
    generate_boilerplate() the server actually stores as starter code — never a
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
            .catch((err): [string, string] => {
              // The author only sees "(preview failed)" in the code pane; which
              // language and why never reaches them.
              log.warn('boilerplate preview failed', { language: lang, ...errorContext(err) });
              return [lang, '(preview failed)'];
            }),
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
