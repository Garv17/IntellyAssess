import { Eye } from 'lucide-react';
import { useState } from 'react';
import { api, type Difficulty, type QuestionDetail } from '../../../api';
import SqlQuestionPreview from './SqlQuestionPreview';
import {
  DifficultyPicker,
  MarkdownField,
  SqlDialectPicker,
  SqlSchemaEditor,
  SqlTestCaseFields,
  TagInput,
} from './shared';

type DraftCase = {
  stdin: string;
  expected_stdout: string;
  explanation: string;
  is_sample: boolean;
  weight: number;
  referenceQuery: string;
};

export default function SqlQuestionEditForm({
  question,
  onDone,
  onCancel,
  onError,
}: {
  question: QuestionDetail;
  onDone: () => void;
  onCancel: () => void;
  onError: (msg: string) => void;
}) {
  const problem = question.coding_problem;
  const [title, setTitle] = useState(question.body_md);
  const [statement, setStatement] = useState(problem?.statement_md ?? '');
  const [constraints, setConstraints] = useState(problem?.constraints_md ?? '');
  const [dialect, setDialect] = useState(problem?.sql_dialect ?? 'sqlite');
  const [schemaSql, setSchemaSql] = useState(problem?.sql_schema_sql ?? '');
  const [resultColumns, setResultColumns] = useState((problem?.sql_result_columns ?? []).join(', '));
  const [timeLimitMs, setTimeLimitMs] = useState(problem?.time_limit_ms ?? 2000);
  const [marks, setMarks] = useState<number | null>(question.marks);
  const [tags, setTags] = useState<string[]>(question.tags);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(question.difficulty);
  const [cases, setCases] = useState<DraftCase[]>(
    (problem?.test_cases ?? []).map((c) => ({
      stdin: c.stdin,
      expected_stdout: c.expected_stdout,
      explanation: c.explanation ?? '',
      is_sample: c.is_sample,
      weight: c.weight,
      referenceQuery: '',
    })),
  );
  const [showPreview, setShowPreview] = useState(false);

  const parsedResultColumns = resultColumns
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.updateCoding(question.id, {
        body_md: title,
        statement_md: statement,
        constraints_md: constraints || null,
        marks,
        allowed_languages: ['sql'],
        time_limit_ms: timeLimitMs,
        memory_limit_mb: problem?.memory_limit_mb ?? 256,
        starter_code: {},
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
      onDone();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update SQL question');
    }
  };

  return (
    <>
      <form onSubmit={submit} className="stack sub-card">
        <label>
          Problem title
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </label>
        <MarkdownField label="Description (Markdown)" value={statement} onChange={setStatement} required allowImageUpload onError={onError} />
        <MarkdownField
          label="Constraints / instructions (Markdown, optional)"
          value={constraints}
          onChange={setConstraints}
          rows={4}
        />
        <label>
          Marks (blank = section default)
          <input
            type="number"
            step="0.5"
            value={marks ?? ''}
            onChange={(e) => setMarks(e.target.value === '' ? null : Number(e.target.value))}
          />
        </label>

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

        {cases.map((c, i) => (
          <fieldset key={i} className="sub-card">
            <legend>
              Case {i + 1} {c.is_sample ? '(sample)' : '(hidden)'}
            </legend>
            <SqlTestCaseFields
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
            <button type="button" className="btn link danger" onClick={() => setCases(cases.filter((_, idx) => idx !== i))}>
              Remove case
            </button>
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
          <button className="btn primary">Save changes</button>
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
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
