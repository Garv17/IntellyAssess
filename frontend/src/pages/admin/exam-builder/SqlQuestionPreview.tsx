import { X } from 'lucide-react';
import Markdown from '../../../components/Markdown';
import SqlResultGrid from '../../../components/SqlResultGrid';
import SqlSchemaExplorer from '../../../components/SqlSchemaExplorer';

const DIALECT_LABELS: Record<string, string> = {
  sqlite: 'SQLite',
  mysql: 'MySQL',
  postgresql: 'PostgreSQL',
  mssql: 'SQL Server',
};

interface PreviewSampleCase {
  referenceQuery: string;
  expected_stdout: string;
  explanation: string;
}

/** Read-only "what will the student see" preview for a SQL question draft —
    rendered from in-memory form state, no save required. Deliberately doesn't
    reuse the real student SqlWorkspace/run pipeline (that's wired to a real
    question id and live judge state); this is a lightweight static mirror of
    its layout instead. */
export default function SqlQuestionPreview({
  statementMd,
  constraintsMd,
  schemaSql,
  dialect,
  resultColumns,
  sampleCase,
  onClose,
}: {
  statementMd: string;
  constraintsMd: string | null;
  schemaSql: string;
  dialect: string;
  resultColumns: string[];
  sampleCase: PreviewSampleCase | null;
  onClose: () => void;
}) {
  const dialectLabel = DIALECT_LABELS[dialect] ?? dialect;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Preview student view">
      <div className="modal xlarge sql-preview-modal">
        <div className="sql-preview-header">
          <h3>Preview — student view</h3>
          <button type="button" className="btn icon" onClick={onClose} aria-label="Close preview">
            <X size={16} />
          </button>
        </div>

        <div className="sql-preview-body">
          <div className="sql-preview-left">
            <div className="field-label">Description</div>
            <Markdown>{statementMd || '*Nothing written yet.*'}</Markdown>
            {constraintsMd && (
              <>
                <div className="field-label" style={{ marginTop: '0.85rem' }}>
                  Constraints
                </div>
                <Markdown>{constraintsMd}</Markdown>
              </>
            )}
            <div className="field-label" style={{ marginTop: '0.85rem' }}>
              Schema
            </div>
            {schemaSql.trim() ? (
              <SqlSchemaExplorer sql={schemaSql} />
            ) : (
              <p className="muted small">No schema written yet.</p>
            )}
          </div>

          <div className="sql-preview-right">
            <div className="editor-bar">
              <span className="sql-dialect-badge">{dialectLabel}</span>
            </div>
            <pre className="code-textarea sql-preview-editor">
              {sampleCase?.referenceQuery || '-- Students write their SQL query here'}
            </pre>
            {sampleCase?.explanation && (
              <div className="sub-card">
                <div className="field-label">Explanation</div>
                <Markdown>{sampleCase.explanation}</Markdown>
              </div>
            )}
            <div className="field-label">Expected result</div>
            {sampleCase?.expected_stdout.trim() ? (
              <SqlResultGrid text={sampleCase.expected_stdout} columnNames={resultColumns} />
            ) : (
              <p className="muted small">No sample test case with an expected result yet.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
