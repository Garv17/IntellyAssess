import { Suspense, lazy } from 'react';

/**
 * Monaco is ~2 MB. Code-splitting it here keeps it out of the initial bundle so
 * login, the dashboard, and an MCQ-only exam load fast; the editor is fetched only
 * when a student actually opens a coding question.
 */
const Editor = lazy(async () => {
  await import('../monaco'); // registers the local Monaco before the component mounts
  return import('@monaco-editor/react');
});

interface Props {
  language: string;
  value: string;
  onChange: (value: string) => void;
  height?: string;
}

export default function CodeEditor({ language, value, onChange, height = '420px' }: Props) {
  return (
    <Suspense
      fallback={
        <div className="editor-loading" style={{ height }}>
          Loading editor…
        </div>
      }
    >
      <Editor
        height={height}
        language={language}
        theme="vs-dark"
        value={value}
        onChange={(next) => onChange(next ?? '')}
        options={{
          minimap: { enabled: false },
          fontSize: 14,
          tabSize: 2,
          scrollBeyondLastLine: false,
          automaticLayout: true,
        }}
      />
    </Suspense>
  );
}
