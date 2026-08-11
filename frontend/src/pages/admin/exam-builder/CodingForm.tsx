import { useState } from 'react';
import { api, type Difficulty, type ParamDef } from '../../../api';
import ParamValueInput from '../../../components/ParamValueInput';
import { useToast } from '../../../components/Toast';
import {
  BoilerplatePreview,
  CODING_LANGUAGES,
  DifficultyPicker,
  FUNCTION_LANGUAGES,
  MarkdownField,
  PARAM_TYPES,
  ParametersEditor,
  SqlCaseFields,
  TagInput,
  boilerplateHint,
  codingCaseHint,
  type FormProps,
} from './shared';

export default function CodingForm({ sectionId, onDone, onError }: FormProps) {
  const toast = useToast();
  const [title, setTitle] = useState('');
  const [statement, setStatement] = useState('');
  const [constraints, setConstraints] = useState('');
  const [languages, setLanguages] = useState<string[]>(['python']);
  const [starterCode, setStarterCode] = useState<Record<string, string>>({});
  const [problemType, setProblemType] = useState<'stdio' | 'function'>('stdio');
  const [functionName, setFunctionName] = useState('');
  const [returnType, setReturnType] = useState('int');
  const [parameters, setParameters] = useState<ParamDef[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(null);
  const [sqlRefQuery, setSqlRefQuery] = useState('');
  const emptyCases = () => [
    { stdin: '', expected_stdout: '', param_values: null as unknown[] | null, expected_value: null as unknown, explanation: '', is_sample: true, weight: 1 },
    { stdin: '', expected_stdout: '', param_values: null as unknown[] | null, expected_value: null as unknown, explanation: '', is_sample: false, weight: 1 },
  ];
  const [cases, setCases] = useState(emptyCases());
  const isSqlOnly = languages.length > 0 && languages.every((l) => l === 'sql');

  const switchToFunctionMode = () => {
    setProblemType('function');
    setLanguages((prev) => {
      const kept = prev.filter((l) => FUNCTION_LANGUAGES.includes(l));
      return kept.length > 0 ? kept : ['python'];
    });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createCoding(sectionId, {
        body_md: title,
        statement_md: statement,
        constraints_md: constraints || null,
        allowed_languages: languages,
        starter_code: starterCode,
        problem_type: problemType,
        function_name: problemType === 'function' ? functionName : null,
        return_type: problemType === 'function' ? returnType : null,
        parameters: problemType === 'function' ? parameters : null,
        test_cases: cases.map((c, i) => ({ ...c, explanation: c.explanation || null, order_index: i })),
        tags,
        difficulty,
      });
      setTitle('');
      setStatement('');
      setConstraints('');
      setTags([]);
      setDifficulty(null);
      setStarterCode({});
      setProblemType('stdio');
      setFunctionName('');
      setReturnType('int');
      setParameters([]);
      setCases(emptyCases());
      setSqlRefQuery('');
      onDone();
      toast.success('Problem added');
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to add problem');
    }
  };

  return (
    <form onSubmit={submit} className="stack">
      <label>
        Problem title
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
      </label>
      <MarkdownField label="Description (Markdown)" value={statement} onChange={setStatement} required />
      <MarkdownField
        label="Constraints (Markdown, optional)"
        value={constraints}
        onChange={setConstraints}
        rows={4}
      />
      <div className="field">
        <span className="field-label">Problem type</span>
        <div className="problem-type-toggle">
          <button
            type="button"
            className={`btn ${problemType === 'stdio' ? 'primary' : ''}`}
            onClick={() => setProblemType('stdio')}
          >
            Classic (stdin/stdout)
          </button>
          <button
            type="button"
            className={`btn ${problemType === 'function' ? 'primary' : ''}`}
            onClick={switchToFunctionMode}
          >
            Function Signature
          </button>
        </div>
      </div>
      <div className="lang-row">
        {(problemType === 'function' ? FUNCTION_LANGUAGES : CODING_LANGUAGES).map((lang) => (
          <label key={lang} className="inline">
            <input
              type="checkbox"
              checked={languages.includes(lang)}
              onChange={(e) => {
                if (e.target.checked) {
                  setLanguages([...languages, lang]);
                } else {
                  setLanguages(languages.filter((l) => l !== lang));
                  setStarterCode((prev) => {
                    const next = { ...prev };
                    delete next[lang];
                    return next;
                  });
                }
              }}
            />
            {lang}
          </label>
        ))}
      </div>
      {problemType === 'function' ? (
        <>
          <label>
            Function name
            <input
              value={functionName}
              onChange={(e) => setFunctionName(e.target.value)}
              placeholder="twoSum"
              required
            />
          </label>
          <div className="field">
            <span className="field-label">Return type</span>
            <select value={returnType} onChange={(e) => setReturnType(e.target.value)}>
              {PARAM_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <span className="field-label">Parameters</span>
            <ParametersEditor parameters={parameters} onChange={setParameters} />
          </div>
          <div className="field">
            <span className="field-label">Boilerplate preview</span>
            <BoilerplatePreview
              functionName={functionName}
              returnType={returnType}
              parameters={parameters}
              languages={languages}
            />
          </div>
        </>
      ) : (
        languages.map((lang) => (
          <label key={lang}>
            {lang} boilerplate
            <textarea
              value={starterCode[lang] ?? ''}
              rows={4}
              spellCheck={false}
              className="code-textarea"
              placeholder={boilerplateHint(lang)}
              onChange={(e) => setStarterCode({ ...starterCode, [lang]: e.target.value })}
            />
          </label>
        ))
      )}
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
        samples; hidden cases decide the score.
      </p>
      {problemType === 'stdio' && <p className="muted small">{codingCaseHint(languages)}</p>}
      {problemType === 'stdio' && isSqlOnly && (
        <label>
          Reference query (used only to run the previews below — not saved, not shown to
          students)
          <textarea
            value={sqlRefQuery}
            rows={4}
            spellCheck={false}
            className="code-textarea"
            placeholder="SELECT ... FROM ... GROUP BY ... HAVING ...;"
            onChange={(e) => setSqlRefQuery(e.target.value)}
          />
        </label>
      )}

      {cases.map((c, i) => (
        <fieldset key={i} className="sub-card">
          <legend>
            Case {i + 1} {c.is_sample ? '(sample)' : '(hidden)'}
          </legend>
          {problemType === 'function' ? (
            <div className="stack">
              {parameters.map((p, pIdx) => (
                <ParamValueInput
                  key={pIdx}
                  label={p.name || `param ${pIdx + 1}`}
                  type={p.type}
                  value={c.param_values?.[pIdx]}
                  onChange={(v) => {
                    const next = [...cases];
                    const values = [...(c.param_values ?? parameters.map(() => null))];
                    values[pIdx] = v;
                    next[i] = { ...c, param_values: values };
                    setCases(next);
                  }}
                />
              ))}
              <ParamValueInput
                label="Expected output"
                type={returnType}
                value={c.expected_value}
                onChange={(v) => {
                  const next = [...cases];
                  next[i] = { ...c, expected_value: v };
                  setCases(next);
                }}
              />
            </div>
          ) : isSqlOnly ? (
            <SqlCaseFields
              stdin={c.stdin}
              expectedStdout={c.expected_stdout}
              referenceQuery={sqlRefQuery}
              onChange={(next) => {
                const nextCases = [...cases];
                nextCases[i] = { ...c, ...next };
                setCases(nextCases);
              }}
            />
          ) : (
            <div className="case-grid">
              <label>
                stdin
                <textarea
                  value={c.stdin}
                  rows={2}
                  placeholder="3 4"
                  onChange={(e) => {
                    const next = [...cases];
                    next[i] = { ...c, stdin: e.target.value };
                    setCases(next);
                  }}
                />
              </label>
              <label>
                expected stdout
                <textarea
                  value={c.expected_stdout}
                  rows={2}
                  placeholder="7"
                  onChange={(e) => {
                    const next = [...cases];
                    next[i] = { ...c, expected_stdout: e.target.value };
                    setCases(next);
                  }}
                />
              </label>
            </div>
          )}
          {c.is_sample && (
            <label>
              Explanation (optional, shown to students as part of the worked example)
              <textarea
                value={c.explanation}
                rows={2}
                placeholder="Because nums[0] + nums[1] = 9, we return their indices."
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
              {
                stdin: '',
                expected_stdout: '',
                param_values: problemType === 'function' ? parameters.map(() => null) : null,
                expected_value: null,
                explanation: '',
                is_sample: false,
                weight: 1,
              },
            ])
          }
        >
          + Test case
        </button>
        <button className="btn primary">Save problem</button>
      </div>
    </form>
  );
}
