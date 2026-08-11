import { Eye } from 'lucide-react';
import { useState } from 'react';
import { api, type Difficulty } from '../../../api';
import SqlQuestionPreview from './SqlQuestionPreview';
import {
  DifficultyPicker,
  MarkdownField,
  SqlDialectPicker,
  SqlSchemaEditor,
  SqlTestCaseFields,
  TagInput,
  type FormProps,
} from './shared';

type DraftCase = {
  stdin: string;
  expected_stdout: string;
  explanation: string;
  is_sample: boolean;
  weight: number;
  // Scratch-only, never sent to the backend — kept here so "Preview student
  // view" can show the query that produced this case's expected result.
  referenceQuery: string;
};

function emptyCases(): DraftCase[] {
  return [
    { stdin: '', expected_stdout: '', explanation: '', is_sample: true, weight: 1, referenceQuery: '' },
    { stdin: '', expected_stdout: '', explanation: '', is_sample: false, weight: 1, referenceQuery: '' },
  ];
}

export default function SqlQuestionForm({ sectionId, onDone, onError }: FormProps) {
  const [title, setTitle] = useState('');
  const [statement, setStatement] = useState('');
  const [constraints, setConstraints] = useState('');
  const [dialect, setDialect] = useState('sqlite');
  const [schemaSql, setSchemaSql] = useState('');
  const [resultColumns, setResultColumns] = useState('');
  const [timeLimitMs, setTimeLimitMs] = useState(2000);
  const [tags, setTags] = useState<string[]>([]);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(null);
  const [cases, setCases] = useState<DraftCase[]>(emptyCases());
  const [showPreview, setShowPreview] = useState(false);

  const parsedResultColumns = resultColumns
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createCoding(sectionId, {
        body_md: title,
        statement_md: statement,
        constraints_md: constraints || null,
        allowed_languages: ['sql'],
        starter_code: {},
        time_limit_ms: timeLimitMs,
        problem_type: 'stdio',
        function_name: null,
        return_type: null,
        parameters: null,
        sql_dialect: dialect,
        sql_schema_sql: schemaSql,
        sql_result_columns: parsedResultColumns.length > 0 ? parsedResultColumns : null,
        test_cases: cases.map((c, i) => ({
          stdin: c.stdin,
          expected_stdout: c.expected_stdout,
          explanation: c.explanation || null,
          is_sample: c.is_sample,
          weight: c.weight,
          order_index: i,
        })),
        tags,
        difficulty,
      });
      setTitle('');
      setStatement('');
      setConstraints('');
      setDialect('sqlite');
      setSchemaSql('');
      setResultColumns('');
      setTimeLimitMs(2000);
      setTags([]);
      setDifficulty(null);
      setCases(emptyCases());
      onDone();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to add SQL question');
    }
  };

  return (
    <>
      <form onSubmit={submit} className="stack">
        <label>
          Problem title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </label>
        <MarkdownField label="Description (Markdown)" value={statement} onChange={setStatement} required />
        <MarkdownField
          label="Constraints / instructions (Markdown, optional)"
          value={constraints}
          onChange={setConstraints}
          rows={4}
        />

        <SqlDialectPicker value={dialect} onChange={setDialect} />
        <SqlSchemaEditor value={schemaSql} onChange={setSchemaSql} />

        <label>
          Expected result column names (optional, comma-separated — for display only)
          <input
            value={resultColumns}
            onChange={(e) => setResultColumns(e.target.value)}
            placeholder="employee_name, total_orders"
          />
        </label>

        <label>
          Query execution time limit (ms)
          <input
            type="number"
            min={100}
            max={15000}
            value={timeLimitMs}
            onChange={(e) => setTimeLimitMs(Number(e.target.value))}
          />
        </label>

        <div className="field">
          <span className="field-label">Tags</span>
          <TagInput tags={tags} onChange={setTags} />
        </div>
        <div className="field">
          <span className="field-label">Difficulty</span>
          <DifficultyPicker value={difficulty} onChange={setDifficulty} />
        </div>

        <p className="muted small">
          At least one sample and one hidden case are required. Students can only run against
          samples; hidden cases decide the score. Every case's query runs against the schema
          defined above.
        </p>

        {cases.map((c, i) => (
          <fieldset key={i} className="sub-card">
            <legend>
              Case {i + 1} {c.is_sample ? '(sample)' : '(hidden)'}
            </legend>
            <SqlTestCaseFields
              schemaSql={schemaSql}
              extraSetup={c.stdin}
              expectedStdout={c.expected_stdout}
              referenceQuery={c.referenceQuery}
              onReferenceQueryChange={(v) => {
                const next = [...cases];
                next[i] = { ...c, referenceQuery: v };
                setCases(next);
              }}
              onChange={(next) => {
                const nextCases = [...cases];
                nextCases[i] = { ...c, ...next };
                setCases(nextCases);
              }}
            />
            {c.is_sample && (
              <label>
                Explanation (optional, shown to students as part of the worked example)
                <textarea
                  value={c.explanation}
                  rows={2}
                  placeholder="Only orders placed in 2024 are counted."
                  onChange={(e) => {
                    const next = [...cases];
                    next[i] = { ...c, explanation: e.target.value };
                    setCases(next);
                  }}
                />
              </label>
            )}
            <label className="inline">
              <input
                type="checkbox"
                checked={c.is_sample}
                onChange={(e) => {
                  const next = [...cases];
                  next[i] = { ...c, is_sample: e.target.checked };
                  setCases(next);
                }}
              />
              Visible to students
            </label>
            <label className="inline">
              Weight
              <input
                type="number"
                min={1}
                value={c.weight}
                onChange={(e) => {
                  const next = [...cases];
                  next[i] = { ...c, weight: Number(e.target.value) };
                  setCases(next);
                }}
              />
            </label>
          </fieldset>
        ))}

        <div className="form-actions">
          <button
            type="button"
            className="btn"
            onClick={() =>
              setCases([
                ...cases,
                { stdin: '', expected_stdout: '', explanation: '', is_sample: false, weight: 1, referenceQuery: '' },
              ])
            }
          >
            + Test case
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setShowPreview(true)}
            disabled={!schemaSql.trim() || !statement.trim()}
          >
            <Eye size={14} /> Preview student view
          </button>
          <button className="btn primary">Save problem</button>
        </div>
      </form>

      {showPreview && (
        <SqlQuestionPreview
          statementMd={statement}
          constraintsMd={constraints || null}
          schemaSql={schemaSql}
          dialect={dialect}
          resultColumns={parsedResultColumns}
          sampleCase={cases.find((c) => c.is_sample) ?? null}
          onClose={() => setShowPreview(false)}
        />
      )}
    </>
  );
}
