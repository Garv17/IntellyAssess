import { ListChecks } from 'lucide-react';
import { useState } from 'react';
import type { AnswerSave, CodingProblem, ParamDef, Question } from '../api';
import { useVerticalSplit } from '../hooks/useResizablePanes';
import CodeEditor from './CodeEditor';
import Collapsible from './Collapsible';
import Markdown from './Markdown';
import NoExecutionNotice from './NoExecutionNotice';
import Splitter from './Splitter';
import { SqlEditorPane, SqlProblemPane } from './SqlWorkspace';
import Tabs from './Tabs';

const ROWS_KEY = 'coding-editor-rows';
const DEFAULT_EDITOR_SHARE = 68;
// ~19px Monaco line height at the 14px font CodeEditor uses, so 220px is
// still ~11 visible lines even at its absolute floor — the default 68% share
// comfortably clears ~17-20 lines whenever the pane has its normal room.
const EDITOR_FLOOR_PX = 220;
// 150px, not 110 — enough to read a tab row + verdict pill + a couple of
// input/output lines without feeling cramped. Below this, .cw-editor-inner's
// own scroll (see styles.css) takes over rather than squeezing further.
const RESULTS_FLOOR_PX = 150;
// Neither pane may swallow more than 80% of the editor+results space — the
// other always keeps at least a fifth, on top of its own flat floor above.
const EDITOR_MAX_PCT = 80;
const RESULTS_MAX_PCT = 80;

type CodingViewProps = {
  question: Question;
  answer?: AnswerSave;
  onChange: (id: string, patch: Partial<AnswerSave>, immediate: boolean) => void;
};

function isSqlOnly(problem: CodingProblem): boolean {
  return problem.allowed_languages.length === 1 && problem.allowed_languages[0] === 'sql';
}

function caseSummary(parameters: ParamDef[] | null, paramValues: unknown[] | null): string {
  if (!parameters || !paramValues) return '';
  return parameters.map((p, i) => `${p.name}=${JSON.stringify(paramValues[i])}`).join(', ');
}

/** Left-pane content for a question: statement, constraints, and every
    sample case's input/output/explanation, all visible without digging
    through an accordion — there used to be a second "Sample Testcases"
    section here duplicating the same data in a terser grid; it added a
    scroll-and-hunt step for zero new information, so it's gone. */
export function CodingProblemPane({ question }: { question: Question }) {
  const problem = question.coding_problem!;
  if (isSqlOnly(problem)) return <SqlProblemPane problem={problem} />;
  return <GenericProblemPane problem={problem} />;
}

function GenericProblemPane({ problem }: { problem: CodingProblem }) {
  const isFunction = problem.problem_type === 'function';
  return (
    <div className="cw-problem-scroll">
      <Collapsible title="Description">
        <Markdown>{problem.statement_md}</Markdown>
      </Collapsible>
      {problem.constraints_md && (
        <Collapsible title="Constraints">
          <Markdown>{problem.constraints_md}</Markdown>
        </Collapsible>
      )}
      {problem.sample_test_cases.length > 0 && (
        <Collapsible title="Examples">
          {problem.sample_test_cases.map((tc, i) => (
            <div key={tc.id} className="example-card">
              <div className="example-card-title">Example {i + 1}</div>
              <div className="example-card-grid">
                <div>
                  <span className="muted">Input</span>
                  <pre>{isFunction ? caseSummary(problem.parameters, tc.param_values) : tc.stdin}</pre>
                </div>
                <div>
                  <span className="muted">Output</span>
                  <pre>{isFunction ? JSON.stringify(tc.expected_value) : tc.expected_stdout}</pre>
                </div>
              </div>
              {tc.explanation && (
                <div className="example-card-explanation">
                  <span className="muted">Explanation</span>
                  <Markdown>{tc.explanation}</Markdown>
                </div>
              )}
            </div>
          ))}
        </Collapsible>
      )}
    </div>
  );
}

/** Center-pane content for a question: language toolbar, editor, and a tabbed
    panel below it, split by a draggable divider sized as a percentage of the
    pane's own height so the editor keeps the bulk of the space at any zoom level
    or window size (see useResizablePanes). */
export function CodingEditorPane({ question, answer, onChange }: CodingViewProps) {
  const problem = question.coding_problem!;
  if (isSqlOnly(problem)) return <SqlEditorPane question={question} answer={answer} onChange={onChange} />;
  return <GenericEditorPane question={question} answer={answer} onChange={onChange} />;
}

function GenericEditorPane({ question, answer, onChange }: CodingViewProps) {
  const problem = question.coding_problem!;
  const [language, setLanguage] = useState(answer?.language ?? problem.allowed_languages[0]);
  const isFunction = problem.problem_type === 'function';

  // One remembered code string per language, so hopping between languages never
  // discards what was typed under a language that isn't currently selected.
  const [codeByLang, setCodeByLang] = useState<Record<string, string>>(() => {
    const base = { ...problem.starter_code };
    if (answer?.language) base[answer.language] = answer.code_text ?? base[answer.language] ?? '';
    return base;
  });

  const code = codeByLang[language] ?? problem.starter_code[language] ?? '';

  const { containerRef, chromeTopRef, chromeBottomRef, ratios, mins, resize } = useVerticalSplit(
    ROWS_KEY,
    DEFAULT_EDITOR_SHARE,
    {
      editorFloorPx: EDITOR_FLOOR_PX,
      resultsFloorPx: RESULTS_FLOOR_PX,
      editorMaxPct: EDITOR_MAX_PCT,
      resultsMaxPct: RESULTS_MAX_PCT,
    },
  );

  return (
    <div className="cw-editor-inner" ref={containerRef}>
      <div className="editor-bar" ref={chromeTopRef}>
        <select
          value={language}
          onChange={(e) => {
            const next = e.target.value;
            setLanguage(next);
            const nextCode = codeByLang[next] ?? problem.starter_code[next] ?? '';
            onChange(question.id, { language: next, code_text: nextCode }, true);
          }}
        >
          {problem.allowed_languages.map((lang) => (
            <option key={lang} value={lang}>
              {lang}
            </option>
          ))}
        </select>
        <NoExecutionNotice />
      </div>

      {/* Paste is allowed but the final submission is recorded, so copied code
          stays reviewable after the fact. */}
      <div className="editor-frame" style={{ flexGrow: ratios[0], flexBasis: 0, minHeight: mins[0] }}>
        <CodeEditor
          language={language}
          value={code}
          height="100%"
          onChange={(value) => {
            setCodeByLang((prev) => ({ ...prev, [language]: value }));
            onChange(question.id, { code_text: value, language }, false);
          }}
        />
      </div>

      <Splitter direction="vertical" onResize={resize} />

      <div className="cw-bottom-chrome" ref={chromeBottomRef}>
        <Tabs
          tabs={[{ key: 'examples', label: 'Examples', icon: <ListChecks size={14} /> }]}
          value="examples"
          onChange={() => {}}
        />
      </div>

      <div className="bottom-panel" style={{ flexGrow: ratios[1], flexBasis: 0, minHeight: mins[1] }}>
        <div className="target-case-list">
          {problem.sample_test_cases.length === 0 && (
            <p className="muted small">This problem has no worked examples.</p>
          )}
          {problem.sample_test_cases.map((tc, i) => (
            <div key={tc.id} className="target-case static">
              <span className="target-case-label">Example {i + 1}</span>
              <pre>{isFunction ? caseSummary(problem.parameters, tc.param_values) : tc.stdin}</pre>
              <span className="target-case-label">Expected</span>
              <pre>{isFunction ? JSON.stringify(tc.expected_value) : tc.expected_stdout}</pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
