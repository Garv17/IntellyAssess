import { Copy, Search } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, type Difficulty, type Exam, type QuestionBankItem, type Section } from '../../../api';
import Markdown from '../../../components/Markdown';
import { SkeletonText } from '../../../components/Skeleton';
import { useToast } from '../../../components/Toast';

export default function QuestionBankPanel({
  targetSectionId,
  onCopied,
  onError,
}: {
  targetSectionId: string;
  onCopied: () => void;
  onError: (msg: string) => void;
}) {
  const toast = useToast();
  const [search, setSearch] = useState('');
  const [difficulty, setDifficulty] = useState<Difficulty | ''>('');
  const [exams, setExams] = useState<Exam[]>([]);
  const [examFilter, setExamFilter] = useState('');
  const [sections, setSections] = useState<Section[]>([]);
  const [sectionFilter, setSectionFilter] = useState('');
  const [items, setItems] = useState<QuestionBankItem[] | null>(null);
  const [copying, setCopying] = useState<string | null>(null);

  const runSearch = async () => {
    try {
      const page = await api.questionBank({
        search: search || undefined,
        difficulty: difficulty || undefined,
        exam_id: examFilter || undefined,
        section_id: sectionFilter || undefined,
        page_size: 25,
      });
      setItems(page.items);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Search failed');
    }
  };

  useEffect(() => {
    void api.adminExams().then(setExams).catch(() => undefined);
    void runSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setSectionFilter('');
    if (!examFilter) {
      setSections([]);
      return;
    }
    void api
      .sections(examFilter)
      .then(setSections)
      .catch(() => setSections([]));
  }, [examFilter]);

  const copy = async (item: QuestionBankItem) => {
    setCopying(item.question_id);
    try {
      await api.copyQuestion(item.question_id, targetSectionId);
      toast.success('Question copied into this section');
      onCopied();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Copy failed');
    } finally {
      setCopying(null);
    }
  };

  return (
    <div className="bank-panel">
      <p className="muted small">
        Reuse a question from any exam instead of retyping it. DI-set questions aren't shown
        here. Copy the whole set from its own section.
      </p>
      <div className="row-form">
        <label className="grow">
          Search
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search question text…"
            onKeyDown={(e) => e.key === 'Enter' && void runSearch()}
          />
        </label>
        <label>
          Exam
          <select value={examFilter} onChange={(e) => setExamFilter(e.target.value)}>
            <option value="">Any</option>
            {exams.map((exam) => (
              <option key={exam.id} value={exam.id}>
                {exam.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Section
          <select
            value={sectionFilter}
            onChange={(e) => setSectionFilter(e.target.value)}
            disabled={!examFilter}
          >
            <option value="">Any</option>
            {sections.map((section) => (
              <option key={section.id} value={section.id}>
                {section.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Difficulty
          <select value={difficulty} onChange={(e) => setDifficulty(e.target.value as Difficulty | '')}>
            <option value="">Any</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
        </label>
        <button type="button" className="btn" onClick={() => void runSearch()}>
          <Search size={15} /> Search
        </button>
      </div>

      {items === null ? (
        <SkeletonText width="100%" />
      ) : items.length === 0 ? (
        <p className="muted small">No matching questions found.</p>
      ) : (
        items.map((item) => (
          <div key={item.question_id} className="bank-item">
            <div className="grow">
              <div className="body-preview">
                <Markdown>{item.body_preview}</Markdown>
              </div>
              <div className="bank-meta">
                <span className="badge badge-neutral">{item.type}</span>
                {item.difficulty && <span className={`badge difficulty-${item.difficulty}`}>{item.difficulty}</span>}
                {item.tags.map((t) => (
                  <span key={t} className="tag-chip">
                    {t}
                  </span>
                ))}
                <span>
                  {item.exam_title} · {item.section_title}
                </span>
              </div>
            </div>
            <button
              type="button"
              className="btn small"
              disabled={copying === item.question_id}
              onClick={() => void copy(item)}
            >
              <Copy size={13} /> {copying === item.question_id ? 'Copying…' : 'Copy here'}
            </button>
          </div>
        ))
      )}
    </div>
  );
}
