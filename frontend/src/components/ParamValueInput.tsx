import { useEffect, useState } from 'react';

const ARRAY_TYPES = new Set(['int[]', 'float[]', 'bool[]', 'string[]', 'int[][]']);

const PLACEHOLDER: Record<string, string> = {
  'int[]': '[2, 7, 11, 15]',
  'float[]': '[1.5, 2.0, 3.25]',
  'bool[]': '[true, false, true]',
  'string[]': '["ab", "cd"]',
  'int[][]': '[[1, 2], [3, 4]]',
};

interface Props {
  label: string;
  type: string;
  value: unknown;
  onChange: (value: unknown) => void;
}

/** Renders the right input for one registry type (see backend/app/services/harness.py
    PARAM_TYPES). Shared by the student's Custom Testcase tab, the admin test case
    editor's per-parameter fields, and the admin's expected-output field. */
export default function ParamValueInput({ label, type, value, onChange }: Props) {
  if (ARRAY_TYPES.has(type)) {
    return <JsonValueInput label={label} type={type} value={value} onChange={onChange} />;
  }

  if (type === 'bool') {
    return (
      <label>
        {label}
        <select
          value={value === true ? 'true' : 'false'}
          onChange={(e) => onChange(e.target.value === 'true')}
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
    );
  }

  if (type === 'int' || type === 'float') {
    return (
      <label>
        {label}
        <input
          type="number"
          step={type === 'float' ? 'any' : 1}
          value={typeof value === 'number' ? value : ''}
          onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))}
        />
      </label>
    );
  }

  if (type === 'char') {
    return (
      <label>
        {label}
        <input
          type="text"
          maxLength={1}
          value={typeof value === 'string' ? value : ''}
          onChange={(e) => onChange(e.target.value.slice(0, 1))}
        />
      </label>
    );
  }

  // string
  return (
    <label>
      {label}
      <input
        type="text"
        value={typeof value === 'string' ? value : ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function JsonValueInput({ label, type, value, onChange }: Props) {
  // Kept as raw text locally so a partially-typed JSON array doesn't get
  // clobbered mid-edit; onChange only fires once it actually parses.
  const [text, setText] = useState(() => (value === undefined ? '' : JSON.stringify(value)));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(value === undefined ? '' : JSON.stringify(value));
    setError(null);
  }, [value]);

  return (
    <label>
      {label}
      <textarea
        value={text}
        rows={2}
        placeholder={PLACEHOLDER[type] ?? '[]'}
        className="code-textarea"
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          try {
            onChange(JSON.parse(next));
            setError(null);
          } catch {
            // Deliberately not logged: this fires on every keystroke of a
            // partially-typed value, and "the JSON isn't finished yet" is
            // already shown to the author inline. Nothing to diagnose later.
            setError('Not valid JSON yet');
          }
        }}
      />
      {error && <span className="param-input-error">{error}</span>}
    </label>
  );
}
