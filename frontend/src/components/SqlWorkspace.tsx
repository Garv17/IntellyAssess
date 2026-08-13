import { useCallback, useEffect, useRef, useState } from 'react';
import type { AnswerSave, CodingProblem, Question } from '../api';
import { useVerticalSplit } from '../hooks/useResizablePanes';
import CodeEditor from './CodeEditor';
import Collapsible from './Collapsible';
import NoExecutionNotice from './NoExecutionNotice';
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

/** The center-pane content for a SQL question: editor on top, the question's
    worked examples below, split by a draggable divider whose ratio is a
    percentage of the pane's own height (not a fixed px number) so the editor
    keeps getting the majority of the space at any zoom level or window size. */
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

  const { containerRef, chromeTopRef, ratios, mins, resize } = useVerticalSplit(ROWS_KEY, DEFAULT_EDITOR_SHARE, {
    editorFloorPx: EDITOR_FLOOR_PX,
    resultsFloorPx: RESULTS_FLOOR_PX,
    editorMaxPct: EDITOR_MAX_PCT,
    resultsMaxPct: RESULTS_MAX_PCT,
  });

  const dialectLabel = DIALECT_LABELS[problem.sql_dialect ?? 'sqlite'] ?? 'SQLite';

  return (
    <div className="cw-editor-inner" ref={containerRef}>
      <div className="editor-bar" ref={chromeTopRef}>
        <span className="sql-dialect-badge">{dialectLabel}</span>
        <NoExecutionNotice />
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
          <p className="muted small">This question has no worked examples.</p>
        ) : (
          problem.sample_test_cases.map((tc, i) => (
            <div key={tc.id} className="sql-example-block">
              <div className="sql-result-heading">Example {i + 1} — expected result</div>
              <SqlResultGrid text={tc.expected_stdout} columnNames={problem.sql_result_columns} />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
