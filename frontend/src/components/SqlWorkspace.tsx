import { AlertCircle, Loader2, Play } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, type AnswerSave, type CodeRun, type Question } from '../api';
import CodeEditor from './CodeEditor';
import Collapsible from './Collapsible';
import Markdown from './Markdown';
import Splitter from './Splitter';
import SqlResultGrid from './SqlResultGrid';
import SqlSchemaExplorer from './SqlSchemaExplorer';

const PANE_SIZES_KEY = 'sql-workspace-pane-sizes';
const DEFAULT_PANE_SIZES = { schemaWidth: 380, editorHeight: 360 };

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function loadPaneSizes(): typeof DEFAULT_PANE_SIZES {
  try {
    const raw = window.localStorage.getItem(PANE_SIZES_KEY);
    if (!raw) return DEFAULT_PANE_SIZES;
    const parsed = JSON.parse(raw);
    return {
      schemaWidth: clamp(Number(parsed.schemaWidth) || DEFAULT_PANE_SIZES.schemaWidth, 280, 640),
      editorHeight: clamp(Number(parsed.editorHeight) || DEFAULT_PANE_SIZES.editorHeight, 200, 640),
    };
  } catch {
    return DEFAULT_PANE_SIZES;
  }
}

function savePaneSizes(sizes: typeof DEFAULT_PANE_SIZES) {
  try {
    window.localStorage.setItem(PANE_SIZES_KEY, JSON.stringify(sizes));
  } catch {
    // Storage can be unavailable (private mode, quota) — resizing still works
    // for the session, it just won't persist across reloads.
  }
}

const DIALECT_LABELS: Record<string, string> = {
  sqlite: 'SQLite',
  mysql: 'MySQL',
  postgresql: 'PostgreSQL',
  mssql: 'SQL Server',
};

const VERDICT_INFO: Record<string, { label: string; className: string }> = {
  accepted: { label: 'Accepted', className: 'ok' },
  wrong_answer: { label: 'Wrong Answer', className: 'bad' },
  tle: { label: 'Time Limit Exceeded', className: 'warn' },
  mle: { label: 'Memory Limit Exceeded', className: 'warn' },
  runtime_error: { label: 'Runtime Error', className: 'bad' },
};

function verdictInfo(verdict: string) {
  return VERDICT_INFO[verdict] ?? { label: verdict, className: 'bad' };
}

/** Dedicated SQL question workspace: schema explorer instead of generic
    stdin/stdout examples, no language select (a SQL question only ever has
    one), and a professional result grid instead of a plain-text console. */
export default function SqlWorkspace({
  question,
  answer,
  onChange,
}: {
  question: Question;
  answer?: AnswerSave;
  onChange: (id: string, patch: Partial<AnswerSave>, immediate: boolean) => void;
}) {
  const problem = question.coding_problem!;
  const [code, setCode] = useState(answer?.code_text ?? problem.starter_code.sql ?? '');
  const [run, setRun] = useState<CodeRun | null>(null);
  const [running, setRunning] = useState(false);
  const pollRef = useRef<number | null>(null);

  const [paneSizes, setPaneSizes] = useState(loadPaneSizes);
  const resizeSchema = (delta: number) =>
    setPaneSizes((prev) => {
      const next = { ...prev, schemaWidth: clamp(prev.schemaWidth + delta, 280, 640) };
      savePaneSizes(next);
      return next;
    });
  const resizeEditor = (delta: number) =>
    setPaneSizes((prev) => {
      const next = { ...prev, editorHeight: clamp(prev.editorHeight + delta, 200, 640) };
      savePaneSizes(next);
      return next;
    });

  const [caseId, setCaseId] = useState<string | null>(problem.sample_test_cases[0]?.id ?? null);

  useEffect(
    () => () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    },
    [],
  );

  const runCode = async () => {
    if (!caseId) return;
    setRunning(true);
    setRun(null);
    try {
      const { run_id } = await api.runCode(question.id, 'sql', code, { testCaseId: caseId });
      // Judging is queued, so poll rather than blocking a request for seconds.
      pollRef.current = window.setInterval(async () => {
        try {
          const result = await api.codeRun(run_id);
          setRun(result);
          if (result.status === 'done' || result.status === 'error') {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setRunning(false);
          }
        } catch {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setRunning(false);
        }
      }, 1200);
    } catch (err) {
      setRunning(false);
      setRun({
        run_id: '',
        status: 'error',
        passed: 0,
        total: 0,
        results: [],
        error: err instanceof Error ? err.message : 'Run failed',
      });
    }
  };

  const result = run?.results[0];
  const dialectLabel = DIALECT_LABELS[problem.sql_dialect ?? 'sqlite'] ?? 'SQLite';

  return (
    <div className="sql-workspace">
      <div
        className="sql-workspace-left"
        style={{ width: paneSizes.schemaWidth, flexBasis: paneSizes.schemaWidth }}
      >
        <Collapsible title="Description">
          <Markdown>{problem.statement_md}</Markdown>
        </Collapsible>
        {problem.constraints_md && (
          <Collapsible title="Constraints">
            <Markdown>{problem.constraints_md}</Markdown>
          </Collapsible>
        )}
        <Collapsible title="Schema">
          {problem.sql_schema_sql ? (
            <SqlSchemaExplorer sql={problem.sql_schema_sql} />
          ) : (
            <p className="muted small">No schema provided for this question.</p>
          )}
        </Collapsible>
      </div>

      <Splitter direction="horizontal" onResize={resizeSchema} />

      <div className="sql-workspace-main">
        <div className="editor-bar">
          <span className="sql-dialect-badge">{dialectLabel}</span>
          <span className="muted">{problem.time_limit_ms} ms</span>
          <button
            className="btn"
            style={{ marginLeft: 'auto' }}
            onClick={() => void runCode()}
            disabled={running || !caseId}
          >
            {running ? <Loader2 size={15} className="spinner" /> : <Play size={15} />}
            {running ? 'Running…' : 'Run'}
          </button>
        </div>

        <div className="editor-frame">
          <CodeEditor
            language="sql"
            value={code}
            height={`${paneSizes.editorHeight}px`}
            onChange={(value) => {
              setCode(value);
              onChange(question.id, { code_text: value, language: 'sql' }, false);
            }}
          />
        </div>

        <Splitter direction="vertical" onResize={resizeEditor} />

        <div className="sql-results-panel">
          {problem.sample_test_cases.length === 0 ? (
            <p className="muted small">This question has no sample test cases to run against.</p>
          ) : (
            <div className="sql-case-chip-row">
              {problem.sample_test_cases.map((tc, i) => (
                <button
                  key={tc.id}
                  type="button"
                  className={`sql-case-chip ${caseId === tc.id ? 'active' : ''}`}
                  onClick={() => {
                    setCaseId(tc.id);
                    setRun(null);
                  }}
                >
                  Case {i + 1}
                </button>
              ))}
            </div>
          )}

          {!run && <p className="muted small">Run your query to see the result here.</p>}

          {run && run.error && (
            <div className="sql-error-panel">
              <AlertCircle size={15} />
              <pre>{run.error}</pre>
            </div>
          )}

          {run && !run.error && result && (
            <>
              <span className={`verdict-pill ${verdictInfo(result.verdict).className}`}>
                {verdictInfo(result.verdict).label}
              </span>
              {result.stderr ? (
                <div className="sql-error-panel">
                  <AlertCircle size={15} />
                  <pre>{result.stderr}</pre>
                </div>
              ) : (
                <div className="sql-result-columns">
                  <div>
                    <div className="sql-result-heading">Your Result</div>
                    <SqlResultGrid
                      text={result.actual}
                      compareTo={result.expected}
                      columnNames={problem.sql_result_columns}
                      caption={result.time_ms != null ? `${result.time_ms} ms` : undefined}
                    />
                    {!result.actual && <p className="muted small">(empty result set)</p>}
                  </div>
                  {result.expected != null && (
                    <div>
                      <div className="sql-result-heading">Expected Result</div>
                      <SqlResultGrid text={result.expected} columnNames={problem.sql_result_columns} />
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
