import { Plus } from 'lucide-react';
import { useState } from 'react';
import { api, type Difficulty } from '../../../api';
import { useToast } from '../../../components/Toast';
import { DifficultyPicker, MarkdownField, TagInput, type FormProps } from './shared';

export default function McqForm({ sectionId, onDone, onError }: FormProps) {
  const toast = useToast();
  const [body, setBody] = useState('');
  const [explanation, setExplanation] = useState('');
  const [options, setOptions] = useState(['', '', '', '']);
  const [correct, setCorrect] = useState(0);
  const [tags, setTags] = useState<string[]>([]);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(null);
  const [saved, setSaved] = useState(0);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createMcq(sectionId, {
        body_md: body,
        explanation_md: explanation || null,
        options: options.map((text, i) => ({
          body: text,
          is_correct: i === correct,
          order_index: i,
        })),
        tags,
        difficulty,
      });
      setBody('');
      setExplanation('');
      setOptions(['', '', '', '']);
      setCorrect(0);
      setTags([]);
      setDifficulty(null);
      setSaved((n) => n + 1);
      onDone();
      toast.success('Question added');
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to add question');
    }
  };

  return (
    <form onSubmit={submit} className="stack">
      <MarkdownField label="Question" value={body} onChange={setBody} rows={3} required allowImageUpload onError={onError} />
      {options.map((option, i) => (
        <label key={i} className="option-row">
          <input
            type="radio"
            name="correct"
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
        Explanation (optional, shown to admins reviewing a student's answer)
        <textarea
          value={explanation}
          onChange={(e) => setExplanation(e.target.value)}
          rows={2}
        />
      </label>
      <div className="field">
        <span className="field-label">Tags</span>
        <TagInput tags={tags} onChange={setTags} />
      </div>
      <div className="field">
        <span className="field-label">Difficulty</span>
        <DifficultyPicker value={difficulty} onChange={setDifficulty} />
      </div>
      <div className="form-actions">
        <button className="btn primary">
          <Plus size={15} /> Add question
        </button>
        {saved > 0 && <span className="muted">{saved} added this session</span>}
      </div>
    </form>
  );
}
