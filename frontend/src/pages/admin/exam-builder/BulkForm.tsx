import { Upload } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../../api';
import type { FormProps } from './shared';

export default function BulkForm({ sectionId, onDone, onError }: FormProps) {
  const [result, setResult] = useState<{ created: number; skipped: number; errors: string[] } | null>(
    null,
  );

  return (
    <div className="stack">
      <p className="muted small">
        CSV columns: <code>body_md, option_a, option_b, option_c, option_d, correct</code> (A–D),
        optional <code>marks</code>, <code>negative_marks</code>, and <code>explanation</code>.
      </p>
      <label className="btn" style={{ cursor: 'pointer', display: 'inline-flex', width: 'fit-content' }}>
        <Upload size={15} /> Choose CSV file
        <input
          type="file"
          accept=".csv"
          style={{ display: 'none' }}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            try {
              const res = await api.bulkQuestions(sectionId, file);
              setResult(res);
              onDone();
            } catch (err) {
              onError(err instanceof Error ? err.message : 'Upload failed');
            }
          }}
        />
      </label>
      {result && (
        <div className="banner ok">
          Created {result.created}, skipped {result.skipped}.
          {result.errors.length > 0 && (
            <ul className="errors">
              {result.errors.map((msg) => (
                <li key={msg}>{msg}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
