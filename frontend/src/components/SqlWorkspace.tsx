import { AlertCircle, Loader2, Play } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type AnswerSave, type CodeRun, type CodingProblem, type Question } from '../api';
import { useVerticalSplit } from '../hooks/useResizablePanes';
import CodeEditor from './CodeEditor';
import Collapsible from './Collapsible';
import Markdown from './Markdown';
import Splitter from './Splitter';
import SqlResultGrid from './SqlResultGrid';
import SqlSchemaExplorer from './SqlSchemaExplorer';

const ROWS_KEY = 'sql-editor-rows';
const DEFAULT_EDITOR_SHARE = 68;
// See CodingView.tsx's identical constants for the reasoning.
const EDITOR_FLOOR_PX = 220;
const RESULTS_FLOOR_PX = 150;
const EDITOR_MAX_PCT = 80;
const RESULTS_MAX_PCT = 80;

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

/** The left-pane content for a SQL question: description/constraints in one
    capped, independently-scrolling region, and the schema explorer — also
    collapsible, so it can be tucked away for more editor/results room —
    filling the rest of the pane in its own scroll region below when open, so
    a long description can never push the schema (the thing students
    reference over and over while writing a query) out of view, and neither
    one's scroll touches the page or the editor. */
export function SqlProblemPane({ problem }: { problem: CodingProblem }) {
  const schemaScrollRef = useRef<HTMLDivElement | null>(null);
  const schemaContentRef = useRef<HTMLDivElement | null>(null);
  const [schemaShadow, setSchemaShadow] = useState({ top: false, bottom: false });

  const updateSchemaShadow = useCallback(() => {
    const el = schemaScrollRef.current;
    if (!el) return;
    setSchemaShadow({
      top: el.scrollTop > 2,
      bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 2,
    });
  }, []);

  useEffect(() => {
    updateSchemaShadow();
    const scrollEl = schemaScrollRef.current;
    const contentEl = schemaContentRef.current;
    if (!scrollEl || !contentEl) return;
    // Table cards expand/collapse without the scroll container itself resizing,
    // so watch the content's box size (not just scroll events) to catch that.
    const ro = new ResizeObserver(updateSchemaShadow);
    ro.observe(scrollEl);
    ro.observe(contentEl);
    return () => ro.disconnect();
  }, [problem.sql_schema_sql, updateSchemaShadow]);

  return (
    <div className="sql-problem-pane">
      <div className="sql-problem-top">
        <Collapsible title="Description">
          <Markdown>{problem.statement_md}</Markdown>
        </Collapsible>
        {problem.constraints_md && (
          <Collapsible title="Constraints">
            <Markdown>{problem.constraints_md}</Markdown>
          </Collapsible>
        )}
      </div>

      <Collapsible className="sql-schema-collapsible" title="Database Schema">
        {problem.sql_schema_sql ? (
          <div className="sql-schema-scroll-wrap">
            <div className="sql-schema-scroll" ref={schemaScrollRef} onScroll={updateSchemaShadow}>
              <div ref={schemaContentRef}>
                <SqlSchemaExplorer sql={problem.sql_schema_sql} />
              </div>
            </div>
            <div className={`sql-scroll-shadow top ${schemaShadow.top ? 'visible' : ''}`} />
            <div className={`sql-scroll-shadow bottom ${schemaShadow.bottom ? 'visible' : ''}`} />
          </div>
        ) : (
          <p className="muted small sql-schema-empty">No schema provided for this question.</p>
        )}
      </Collapsible>
    </div>
  );
}

/** The center-pane content for a SQL question: editor on top, results below,
    split by a draggable divider whose ratio is a percentage of the pane's own
    height (not a fixed px number) so the editor keeps getting the majority of
    the space at any zoom level or window size. */
export function SqlEditorPane({
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

  const { containerRef, chromeTopRef, ratios, mins, resize } = useVerticalSplit(ROWS_KEY, DEFAULT_EDITOR_SHARE, {
    editorFloorPx: EDITOR_FLOOR_PX,
    resultsFloorPx: RESULTS_FLOOR_PX,
    editorMaxPct: EDITOR_MAX_PCT,
    resultsMaxPct: RESULTS_MAX_PCT,
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
    <div className="cw-editor-inner" ref={containerRef}>
      <div className="editor-bar" ref={chromeTopRef}>
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

      <div className="editor-frame" style={{ flexGrow: ratios[0], flexBasis: 0, minHeight: mins[0] }}>
        <CodeEditor
          language="sql"
          value={code}
          height="100%"
          onChange={(value) => {
            setCode(value);
            onChange(question.id, { code_text: value, language: 'sql' }, false);
          }}
        />
      </div>

      <Splitter direction="vertical" onResize={resize} />

      <div className="sql-results-panel" style={{ flexGrow: ratios[1], flexBasis: 0, minHeight: mins[1] }}>
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
  );
}
