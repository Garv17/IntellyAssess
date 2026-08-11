import { AlertCircle, CheckCircle2, ListChecks, Loader2, Play } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api, type AnswerSave, type CodeRun, type ParamDef, type Question } from '../api';
import CodeEditor from './CodeEditor';
import Collapsible from './Collapsible';
import Markdown from './Markdown';
import ParamValueInput from './ParamValueInput';
import Splitter from './Splitter';
import Tabs from './Tabs';

const PANE_SIZES_KEY = 'coding-pane-sizes';
const DEFAULT_PANE_SIZES = { statementWidth: 360, editorHeight: 420 };

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function loadPaneSizes(): typeof DEFAULT_PANE_SIZES {
  try {
    const raw = window.localStorage.getItem(PANE_SIZES_KEY);
    if (!raw) return DEFAULT_PANE_SIZES;
    const parsed = JSON.parse(raw);
    return {
      statementWidth: clamp(Number(parsed.statementWidth) || DEFAULT_PANE_SIZES.statementWidth, 260, 640),
      editorHeight: clamp(Number(parsed.editorHeight) || DEFAULT_PANE_SIZES.editorHeight, 240, 720),
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

const VERDICT_INFO: Record<string, { label: string; className: string }> = {
  accepted: { label: 'Accepted', className: 'ok' },
  // Custom-input runs have no expected output to grade against, so a clean exit
  // is reported as "Ran successfully" rather than implying correctness.
  ok: { label: 'Ran successfully', className: 'ok' },
  wrong_answer: { label: 'Wrong Answer', className: 'bad' },
  tle: { label: 'Time Limit Exceeded', className: 'warn' },
  mle: { label: 'Memory Limit Exceeded', className: 'warn' },
  runtime_error: { label: 'Runtime Error', className: 'bad' },
};

function verdictInfo(verdict: string) {
  return VERDICT_INFO[verdict] ?? { label: verdict, className: 'bad' };
}

type RunTarget = { type: 'case'; caseId: string } | { type: 'custom' };
type BottomTab = 'testcases' | 'custom' | 'output' | 'console';

function defaultParamValue(type: string): unknown {
  if (type === 'int' || type === 'float') return 0;
  if (type === 'bool') return false;
  if (type === 'string' || type === 'char') return '';
  return []; // int[] | float[] | bool[] | string[] | int[][]
}

// Function-mode results carry structured values JSON-stringified (see
// judge_tasks._evaluate_function) so they fit the same str|null shape the
// stdio path already uses — this pairs them back up with parameter names for
// a readable "name = value" display instead of a bare JSON array.
function formatParamValues(parameters: ParamDef[] | null, jsonText: string | null | undefined): string {
  if (!parameters || jsonText == null) return jsonText ?? '';
  try {
    const values = JSON.parse(jsonText) as unknown[];
    return parameters.map((p, i) => `${p.name} = ${JSON.stringify(values[i])}`).join('\n');
  } catch {
    return jsonText;
  }
}

function caseSummary(parameters: ParamDef[] | null, paramValues: unknown[] | null): string {
  if (!parameters || !paramValues) return '';
  return parameters.map((p, i) => `${p.name}=${JSON.stringify(paramValues[i])}`).join(', ');
}

export default function CodingView({
  question,
  answer,
  onChange,
}: {
  question: Question;
  answer?: AnswerSave;
  onChange: (id: string, patch: Partial<AnswerSave>, immediate: boolean) => void;
}) {
  const problem = question.coding_problem!;
  const [language, setLanguage] = useState(answer?.language ?? problem.allowed_languages[0]);
  const [run, setRun] = useState<CodeRun | null>(null);
  const [running, setRunning] = useState(false);
  const pollRef = useRef<number | null>(null);

  // One remembered code string per language, so hopping between languages never
  // discards what was typed under a language that isn't currently selected.
  const [codeByLang, setCodeByLang] = useState<Record<string, string>>(() => {
    const base = { ...problem.starter_code };
    if (answer?.language) base[answer.language] = answer.code_text ?? base[answer.language] ?? '';
    return base;
  });

  const code = codeByLang[language] ?? problem.starter_code[language] ?? '';

  const [paneSizes, setPaneSizes] = useState(loadPaneSizes);
  const resizeStatement = (delta: number) =>
    setPaneSizes((prev) => {
      const next = { ...prev, statementWidth: clamp(prev.statementWidth + delta, 260, 640) };
      savePaneSizes(next);
      return next;
    });
  const resizeEditor = (delta: number) =>
    setPaneSizes((prev) => {
      const next = { ...prev, editorHeight: clamp(prev.editorHeight + delta, 240, 720) };
      savePaneSizes(next);
      return next;
    });

  // Run always targets exactly one thing — a selected sample case, or ad-hoc custom
  // input — never every sample at once. Defaults to the first sample case if there
  // is one, else the custom-input box.
  const [target, setTarget] = useState<RunTarget>(() =>
    problem.sample_test_cases.length > 0
      ? { type: 'case', caseId: problem.sample_test_cases[0].id }
      : { type: 'custom' },
  );
  const isFunction = problem.problem_type === 'function';
  const [customInput, setCustomInput] = useState('');
  const [customParams, setCustomParams] = useState<unknown[]>(() =>
    (problem.parameters ?? []).map((p) => defaultParamValue(p.type)),
  );
  const [bottomTab, setBottomTab] = useState<BottomTab>(
    problem.sample_test_cases.length > 0 ? 'testcases' : 'custom',
  );

  const selectCase = (caseId: string) => {
    setRun(null);
    setTarget({ type: 'case', caseId });
  };
  const onCustomInputChange = (value: string) => {
    setCustomInput(value);
    if (target.type !== 'custom') {
      setRun(null);
      setTarget({ type: 'custom' });
    }
  };
  const onCustomParamChange = (index: number, value: unknown) => {
    setCustomParams((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
    if (target.type !== 'custom') {
      setRun(null);
      setTarget({ type: 'custom' });
    }
  };

  useEffect(() => () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
  }, []);

  const runCode = async () => {
    setRunning(true);
    setRun(null);
    try {
      const { run_id } = await api.runCode(
        question.id,
        language,
        code,
        target.type === 'case'
          ? { testCaseId: target.caseId }
          : isFunction
            ? { customParams }
            : { customStdin: customInput },
      );
      // Judging is queued, so poll rather than blocking a request for seconds.
      pollRef.current = window.setInterval(async () => {
        try {
          const result = await api.codeRun(run_id);
          setRun(result);
          if (result.status === 'done' || result.status === 'error') {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setRunning(false);
            setBottomTab(result.error ? 'console' : 'output');
          }
        } catch {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setRunning(false);
        }
      }, 1200);
    } catch (err) {
      setRunning(false);
      setBottomTab('console');
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

  const activeCase =
    target.type === 'case' ? problem.sample_test_cases.find((c) => c.id === target.caseId) : undefined;
  const result = run?.results[0];

  return (
    <div className="coding">
      <div
        className="coding-statement"
        style={{ width: paneSizes.statementWidth, flexBasis: paneSizes.statementWidth }}
      >
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
        {problem.sample_test_cases.length > 0 && (
          <Collapsible title="Sample Testcases" defaultOpen={false}>
            {problem.sample_test_cases.map((tc, i) => (
              <div key={tc.id} className="sample">
                <div>
                  <span className="muted">Input {i + 1}</span>
                  <pre>{isFunction ? caseSummary(problem.parameters, tc.param_values) : tc.stdin}</pre>
                </div>
                <div>
                  <span className="muted">Expected</span>
                  <pre>{isFunction ? JSON.stringify(tc.expected_value) : tc.expected_stdout}</pre>
                </div>
              </div>
            ))}
          </Collapsible>
        )}
      </div>

      <Splitter direction="horizontal" onResize={resizeStatement} />

      <div className="coding-editor">
        <div className="editor-bar">
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
          <span className="muted">
            {problem.time_limit_ms} ms · {problem.memory_limit_mb} MB
          </span>
          <span className="run-target muted">
            Target:{' '}
            {target.type === 'case'
              ? `Case ${problem.sample_test_cases.findIndex((c) => c.id === target.caseId) + 1}`
              : 'Custom input'}
          </span>
          <button className="btn" onClick={() => void runCode()} disabled={running}>
            {running ? <Loader2 size={15} className="spinner" /> : <Play size={15} />}
            {running ? 'Running…' : 'Run'}
          </button>
        </div>

        {/* Paste is allowed but every run and the final submission are recorded,
            so copied code stays reviewable after the fact. */}
        <div className="editor-frame">
          <CodeEditor
            language={language}
            value={code}
            height={`${paneSizes.editorHeight}px`}
            onChange={(value) => {
              setCodeByLang((prev) => ({ ...prev, [language]: value }));
              onChange(question.id, { code_text: value, language }, false);
            }}
          />
        </div>

        <Splitter direction="vertical" onResize={resizeEditor} />

        <Tabs
          tabs={[
            { key: 'testcases', label: 'Testcases', icon: <ListChecks size={14} /> },
            { key: 'custom', label: 'Custom Input' },
            { key: 'output', label: 'Output' },
            { key: 'console', label: 'Console' },
          ]}
          value={bottomTab}
          onChange={setBottomTab}
        />

        <div className="bottom-panel">
          {bottomTab === 'testcases' && (
            <div className="target-case-list">
              {problem.sample_test_cases.length === 0 && (
                <p className="muted small">
                  This problem has no sample cases. Use Custom Input instead.
                </p>
              )}
              {problem.sample_test_cases.map((tc, i) => (
                <button
                  key={tc.id}
                  type="button"
                  className={`target-case ${
                    target.type === 'case' && target.caseId === tc.id ? 'active' : ''
                  }`}
                  onClick={() => selectCase(tc.id)}
                >
                  <span className="target-case-label">Case {i + 1}</span>
                  <pre>{isFunction ? caseSummary(problem.parameters, tc.param_values) : tc.stdin}</pre>
                </button>
              ))}
            </div>
          )}

          {bottomTab === 'custom' && (
            <div className="custom-input-panel">
              {isFunction ? (
                (problem.parameters ?? []).map((p, i) => (
                  <ParamValueInput
                    key={p.name}
                    label={p.name}
                    type={p.type}
                    value={customParams[i]}
                    onChange={(v) => onCustomParamChange(i, v)}
                  />
                ))
              ) : (
                <>
                  <label className="muted small" htmlFor="custom-stdin">
                    Standard input for this run
                  </label>
                  <textarea
                    id="custom-stdin"
                    value={customInput}
                    onChange={(e) => onCustomInputChange(e.target.value)}
                    rows={6}
                    placeholder="Type the input your program should read from stdin…"
                  />
                </>
              )}
            </div>
          )}

          {bottomTab === 'output' && (
            <div className="run-output">
              {!run && <p className="muted small">Run your code to see output here.</p>}
              {run && !run.error && result && (
                <>
                  <span className={`verdict-pill ${verdictInfo(result.verdict).className}`}>
                    {verdictInfo(result.verdict).label}
                  </span>
                  <div className="case-detail">
                    <div>
                      <span className="muted">Input</span>
                      <pre>
                        {isFunction
                          ? formatParamValues(problem.parameters, result.stdin)
                          : result.stdin ?? (activeCase ? activeCase.stdin : customInput)}
                      </pre>
                    </div>
                    <div>
                      <span className="muted">Output</span>
                      <pre>{result.actual || '(empty)'}</pre>
                    </div>
                    {result.expected != null && (
                      <div>
                        <span className="muted">Expected</span>
                        <pre>{result.expected}</pre>
                      </div>
                    )}
                    {result.stderr && (
                      <div>
                        <span className="muted">Stderr</span>
                        <pre>{result.stderr}</pre>
                      </div>
                    )}
                  </div>
                  {result.time_ms != null && <p className="muted small">Runtime: {result.time_ms} ms</p>}
                </>
              )}
              {run && run.error && (
                <p className="muted small">This run failed to build. See the Console tab.</p>
              )}
            </div>
          )}

          {bottomTab === 'console' && (
            <div className="console-panel">
              {!run && <p className="muted small">Nothing built yet. Click Run.</p>}
              {run && !run.error && (
                <div className="verdict pass">
                  <CheckCircle2 size={16} /> Build succeeded
                </div>
              )}
              {run && run.error && (
                <>
                  <div className="verdict fail">
                    <AlertCircle size={16} /> Build Error
                  </div>
                  <pre className="error">{run.error}</pre>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
