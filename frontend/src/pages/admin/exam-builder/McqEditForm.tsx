import { useState } from 'react';
import { api, type Difficulty, type QuestionDetail } from '../../../api';
import { DifficultyPicker, MarkdownField, TagInput } from './shared';

export default function McqEditForm({
  question,
  onDone,
  onCancel,
  onError,
}: {
  question: QuestionDetail;
  onDone: () => void;
  onCancel: () => void;
  onError: (msg: string) => void;
}) {
  const [body, setBody] = useState(question.body_md);
  const [explanation, setExplanation] = useState(question.explanation_md ?? '');
  const [options, setOptions] = useState(question.options.map((o) => o.body));
  const [correct, setCorrect] = useState(Math.max(0, question.options.findIndex((o) => o.is_correct)));
  const [marks, setMarks] = useState<number | null>(question.marks);
  const [negative, setNegative] = useState<number | null>(question.negative_marks);
  const [tags, setTags] = useState<string[]>(question.tags);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(question.difficulty);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.updateMcq(question.id, {
        body_md: body,
        explanation_md: explanation || null,
        marks,
        negative_marks: negative,
        options: options.map((text, i) => ({ body: text, is_correct: i === correct, order_index: i })),
        tags,
        difficulty,
      });
      onDone();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update question');
    }
  };

  return (
    <form onSubmit={submit} className="stack sub-card">
      <MarkdownField label="Question" value={body} onChange={setBody} rows={3} required allowImageUpload onError={onError} />
      {options.map((option, i) => (
        <label key={i} className="option-row">
          <input
            type="radio"
            name={`edit-correct-${question.id}`}
            checked={correct === i}
            onChange={() => setCorrect(i)}
            title="Mark as correct"
          />
          <span className="option-letter">{String.fromCharCode(65 + i)}</span>
          <input
            value={option}
            onChange={(e) => {
              const next = [...options];
              next[i] = e.target.value;
              setOptions(next);
            }}
            required
          />
        </label>
      ))}
      <label>
        Explanation (optional)
        <textarea value={explanation} onChange={(e) => setExplanation(e.target.value)} rows={2} />
      </label>
      <div className="row-form">
        <label>
          Marks (blank = section default)
          <input
            type="number"
            step="0.5"
            value={marks ?? ''}
            onChange={(e) => setMarks(e.target.value === '' ? null : Number(e.target.value))}
          />
        </label>
        <label>
          Negative marks (blank = section default)
          <input
            type="number"
            step="0.25"
            value={negative ?? ''}
            onChange={(e) => setNegative(e.target.value === '' ? null : Number(e.target.value))}
          />
        </label>
      </div>
      <div className="field">
        <span className="field-label">Tags</span>
        <TagInput tags={tags} onChange={setTags} />
      </div>
      <div className="field">
        <span className="field-label">Difficulty</span>
        <DifficultyPicker value={difficulty} onChange={setDifficulty} />
      </div>
      <div className="form-actions">
        <button className="btn primary">Save changes</button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
