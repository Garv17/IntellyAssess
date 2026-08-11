import { ImagePlus } from 'lucide-react';
import { useState } from 'react';
import { api, type Difficulty } from '../../../api';
import { useToast } from '../../../components/Toast';
import { DifficultyPicker, TagInput, type FormProps } from './shared';

export default function DiForm({ sectionId, onDone, onError }: FormProps) {
  const toast = useToast();
  const [title, setTitle] = useState('');
  const [passage, setPassage] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  const [questions, setQuestions] = useState([
    { body: '', explanation: '', options: ['', '', '', ''], correct: 0, tags: [] as string[], difficulty: null as Difficulty | null },
  ]);

  const upload = async (file: File) => {
    try {
      const { image_url } = await api.uploadImage(file);
      setImageUrl(image_url);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Image upload failed');
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createDiGroup(sectionId, {
        title,
        passage_md: passage || null,
        image_url: imageUrl || null,
        questions: questions.map((q, qi) => ({
          body_md: q.body,
          explanation_md: q.explanation || null,
          order_index: qi,
          options: q.options.map((text, i) => ({
            body: text,
            is_correct: i === q.correct,
            order_index: i,
          })),
          tags: q.tags,
          difficulty: q.difficulty,
        })),
      });
      setTitle('');
      setPassage('');
      setImageUrl('');
      setQuestions([{ body: '', explanation: '', options: ['', '', '', ''], correct: 0, tags: [], difficulty: null }]);
      onDone();
      toast.success('Question group added');
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to add question group');
    }
  };

  return (
    <form onSubmit={submit} className="stack">
      <p className="muted small">
        A question group is one stimulus plus its questions. They are created together and stay
        grouped for every student, even when question order is randomized.
      </p>
      <label>
        Set title
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
      </label>
      <label className="btn" style={{ cursor: 'pointer', display: 'inline-flex', width: 'fit-content' }}>
        <ImagePlus size={15} /> Choose image
        <input
          type="file"
          accept="image/*"
          style={{ display: 'none' }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
          }}
        />
      </label>
      {imageUrl && (
        <div className="preview">
          <img src={imageUrl} alt="Group stimulus preview" />
        </div>
      )}
      <label>
        Passage / table (optional, Markdown)
        <textarea value={passage} onChange={(e) => setPassage(e.target.value)} rows={4} />
      </label>

      {questions.map((q, qi) => (
        <fieldset key={qi} className="sub-card">
          <legend>Question {qi + 1}</legend>
          <textarea
            value={q.body}
            onChange={(e) => {
              const next = [...questions];
              next[qi] = { ...q, body: e.target.value };
              setQuestions(next);
            }}
            rows={2}
            required
          />
          {q.options.map((option, oi) => (
            <label key={oi} className="option-row">
              <input
                type="radio"
                name={`di-correct-${qi}`}
                checked={q.correct === oi}
                onChange={() => {
                  const next = [...questions];
                  next[qi] = { ...q, correct: oi };
                  setQuestions(next);
                }}
              />
              <span className="option-letter">{String.fromCharCode(65 + oi)}</span>
              <input
                value={option}
                onChange={(e) => {
                  const next = [...questions];
                  const opts = [...q.options];
                  opts[oi] = e.target.value;
                  next[qi] = { ...q, options: opts };
                  setQuestions(next);
                }}
                required
              />
            </label>
          ))}
          <label>
            Explanation (optional)
            <textarea
              value={q.explanation}
              onChange={(e) => {
                const next = [...questions];
                next[qi] = { ...q, explanation: e.target.value };
                setQuestions(next);
              }}
              rows={2}
            />
          </label>
          <div className="field">
            <span className="field-label">Tags</span>
            <TagInput
              tags={q.tags}
              onChange={(tags) => {
                const next = [...questions];
                next[qi] = { ...q, tags };
                setQuestions(next);
              }}
            />
          </div>
          <div className="field">
            <span className="field-label">Difficulty</span>
            <DifficultyPicker
              value={q.difficulty}
              onChange={(difficulty) => {
                const next = [...questions];
                next[qi] = { ...q, difficulty };
                setQuestions(next);
              }}
            />
          </div>
        </fieldset>
      ))}

      <div className="form-actions">
        <button
          type="button"
          className="btn"
          onClick={() =>
            setQuestions([
              ...questions,
              { body: '', explanation: '', options: ['', '', '', ''], correct: 0, tags: [], difficulty: null },
            ])
          }
        >
          + Another question
        </button>
        <button className="btn primary">Save DI set</button>
      </div>
    </form>
  );
}
