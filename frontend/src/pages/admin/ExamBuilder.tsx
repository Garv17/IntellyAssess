import {
  BookMarked,
  ChevronRight,
  Code2,
  Copy,
  GripVertical,
  ImagePlus,
  ListChecks,
  Plus,
  Rocket,
  Search,
  Table2,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  api,
  type DIGroupDetail,
  type Difficulty,
  type PublishResult,
  type QuestionBankItem,
  type QuestionDetail,
  type Section,
} from '../../api';
import AppShell from '../../components/AppShell';
import ConfirmDialog from '../../components/ConfirmDialog';
import { SkeletonText } from '../../components/Skeleton';
import { useToast } from '../../components/Toast';

type Tab = 'mcq' | 'di' | 'coding' | 'bulk' | 'bank';

// Must match the keys in backend/app/services/sandbox.py's LANGUAGES dict — that's
// what actually knows how to compile/run each one.
const CODING_LANGUAGES = ['python', 'javascript', 'c', 'cpp', 'java', 'sql'];

// Test cases run through a real program (or, for SQL, a real database) — not a
// structured LeetCode-style function call. This is the single most common way a
// coding question ends up broken: someone pastes a problem statement's example
// table directly into stdin/expected output instead of writing what the program
// actually reads and prints.
function codingCaseHint(languages: string[]): string {
  if (languages.length > 0 && languages.every((l) => l === 'sql')) {
    return (
      "For SQL questions, stdin is the schema setup that runs before the student's " +
      'query (CREATE TABLE + INSERT statements) — not a copy of the problem\'s ' +
      'example table. Expected stdout is the query result: one row per line, columns ' +
      "separated by |, no header row. E.g. stdin: \"CREATE TABLE t(x INT); INSERT INTO " +
      't VALUES (1),(2);", expected stdout for `SELECT x FROM t;`: "1\\n2".'
    );
  }
  return (
    'stdin is exactly what a correct submission reads from standard input; expected ' +
    "stdout is exactly what it prints (trailing whitespace per line is ignored). Write " +
    "the literal input/output text — don't paste an example table from the problem " +
    'statement.'
  );
}

export default function ExamBuilder() {
  const { examId = '' } = useParams();
  const toast = useToast();
  const [sections, setSections] = useState<Section[]>([]);
  const [activeSection, setActiveSection] = useState<string>('');
  const [tab, setTab] = useState<Tab>('mcq');
  const [error, setError] = useState<string | null>(null);
  const [publishResult, setPublishResult] = useState<PublishResult | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [listVersion, setListVersion] = useState(0);
  const [publishing, setPublishing] = useState(false);

  const load = async () => {
    const list = await api.sections(examId);
    setSections(list);
    if (!activeSection && list.length > 0) setActiveSection(list[0].id);
    setListVersion((v) => v + 1);
  };

  useEffect(() => {
    void load().catch((err) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [examId]);

  const addSection = async (e: React.FormEvent) => {
    e.preventDefault();
    const form = new FormData(e.target as HTMLFormElement);
    try {
      await api.createSection(examId, {
        title: String(form.get('title')),
        instructions: String(form.get('instructions') || '') || null,
        order_index: sections.length,
        marks_per_question: Number(form.get('marks') || 1),
        negative_marks: Number(form.get('negative') || 0),
      });
      (e.target as HTMLFormElement).reset();
      await load();
      toast.success('Section added');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed');
    }
  };

  const publish = async () => {
    setError(null);
    setProblems([]);
    setPublishing(true);
    try {
      const result = await api.publish(examId);
      setPublishResult(result);
      toast.success('Exam published');
    } catch (err) {
      const payload = (err as { payload?: { detail?: { problems?: string[] } } }).payload;
      const list = payload?.detail?.problems;
      if (list) setProblems(list);
      else setError(err instanceof Error ? err.message : 'Publish failed');
    } finally {
      setPublishing(false);
    }
  };

  return (
    <AppShell
      title={
        <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Link to="/admin" className="btn ghost small">
            ← Exams
          </Link>
          Paper builder
        </span>
      }
      actions={
        <button className="btn primary" onClick={() => void publish()} disabled={publishing}>
          <Rocket size={15} /> {publishing ? 'Publishing…' : 'Publish exam'}
        </button>
      }
      adminName="Admin"
    >
      {error && <div className="banner error">{error}</div>}
      {problems.length > 0 && (
        <div className="banner error">
          <div>
            <strong>Not ready to publish:</strong>
            <ul className="errors">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {publishResult && (
        <div className="banner ok">
          Published · {publishResult.total_questions} questions · {publishResult.total_marks} marks
          {publishResult.warnings.length > 0 && (
            <ul className="errors">
              {publishResult.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <section className="card">
        <div className="card-header">
          <h2>Sections</h2>
        </div>
        <form className="row-form" onSubmit={addSection}>
          <label>
            Title
            <input name="title" required placeholder="Aptitude" />
          </label>
          <label>
            Marks / question
            <input name="marks" type="number" step="0.5" defaultValue={1} />
          </label>
          <label>
            Negative marks
            <input name="negative" type="number" step="0.25" defaultValue={0} />
          </label>
          <label className="grow">
            Instructions
            <input name="instructions" placeholder="Optional" />
          </label>
          <button className="btn">
            <Plus size={15} /> Add section
          </button>
        </form>

        <div className="chip-row">
          {sections.map((section) => (
            <button
              key={section.id}
              className={`chip ${activeSection === section.id ? 'active' : ''}`}
              onClick={() => setActiveSection(section.id)}
            >
              {section.title} · {section.question_count ?? 0}
            </button>
          ))}
        </div>
      </section>

      {activeSection && (
        <section className="card">
          <div className="tabs">
            {(
              [
                { key: 'mcq', label: 'MCQ', icon: <ListChecks size={15} /> },
                { key: 'di', label: 'Grouped Questions', icon: <Table2 size={15} /> },
                { key: 'coding', label: 'Coding', icon: <Code2 size={15} /> },
                { key: 'bulk', label: 'Bulk upload', icon: <Upload size={15} /> },
                { key: 'bank', label: 'Question Bank', icon: <BookMarked size={15} /> },
              ] as { key: Tab; label: string; icon: React.ReactNode }[]
            ).map((t) => (
              <button
                key={t.key}
                className={`tab ${tab === t.key ? 'active' : ''}`}
                onClick={() => setTab(t.key)}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>

          {tab === 'mcq' && (
            <McqForm sectionId={activeSection} onDone={() => void load()} onError={setError} />
          )}
          {tab === 'di' && (
            <DiForm sectionId={activeSection} onDone={() => void load()} onError={setError} />
          )}
          {tab === 'coding' && (
            <CodingForm sectionId={activeSection} onDone={() => void load()} onError={setError} />
          )}
          {tab === 'bulk' && (
            <BulkForm sectionId={activeSection} onDone={() => void load()} onError={setError} />
          )}
          {tab === 'bank' && (
            <QuestionBankPanel
              targetSectionId={activeSection}
              onCopied={() => void load()}
              onError={setError}
            />
          )}
        </section>
      )}

      {activeSection && (
        <section className="card">
          <div className="card-header">
            <h2>Existing questions</h2>
          </div>
          <p className="muted small">
            Drag the handle to reorder. Edit a question to change its wording, options, or
            correct answer, or delete it outright. Changes are blocked once students have
            started the exam.
          </p>
          <ExistingQuestions
            key={`${activeSection}-${listVersion}`}
            sectionId={activeSection}
            onChanged={() => void load()}
            onError={setError}
          />
        </section>
      )}
    </AppShell>
  );
}

// ------------------------------------------------------------ shared bits

function TagInput({ tags, onChange }: { tags: string[]; onChange: (tags: string[]) => void }) {
  const [draft, setDraft] = useState('');
  const commit = () => {
    const t = draft.trim();
    if (t && !tags.includes(t) && tags.length < 10) onChange([...tags, t]);
    setDraft('');
  };
  return (
    <div className="tag-input-row">
      {tags.map((t) => (
        <span key={t} className="tag-chip">
          {t}
          <button type="button" onClick={() => onChange(tags.filter((x) => x !== t))}>
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        placeholder={tags.length === 0 ? 'Add tags (press Enter)…' : ''}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commit();
          } else if (e.key === 'Backspace' && !draft && tags.length) {
            onChange(tags.slice(0, -1));
          }
        }}
        onBlur={commit}
      />
    </div>
  );
}

function DifficultyPicker({
  value,
  onChange,
}: {
  value: Difficulty | null;
  onChange: (d: Difficulty | null) => void;
}) {
  const options: Difficulty[] = ['easy', 'medium', 'hard'];
  return (
    <div className="difficulty-picker">
      {options.map((d) => (
        <button
          key={d}
          type="button"
          className={`difficulty-option ${value === d ? `selected ${d}` : ''}`}
          onClick={() => onChange(value === d ? null : d)}
        >
          {d}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------- question bank

function QuestionBankPanel({
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
  const [items, setItems] = useState<QuestionBankItem[] | null>(null);
  const [copying, setCopying] = useState<string | null>(null);

  const runSearch = async () => {
    try {
      const page = await api.questionBank({
        search: search || undefined,
        difficulty: difficulty || undefined,
        page_size: 25,
      });
      setItems(page.items);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Search failed');
    }
  };

  useEffect(() => {
    void runSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        here — copy the whole set from its own section.
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
              <div className="body-preview">{item.body_preview}</div>
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

// ---------------------------------------------------------- existing list

type Block =
  | { kind: 'question'; order: number; question: QuestionDetail }
  | { kind: 'di_group'; order: number; group: DIGroupDetail };

function ExistingQuestions({
  sectionId,
  onChanged,
  onError,
}: {
  sectionId: string;
  onChanged: () => void;
  onError: (msg: string) => void;
}) {
  const toast = useToast();
  const [questions, setQuestions] = useState<QuestionDetail[]>([]);
  const [diGroups, setDiGroups] = useState<DIGroupDetail[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  const [pendingDeleteQuestion, setPendingDeleteQuestion] = useState<string | null>(null);
  const [pendingDeleteGroup, setPendingDeleteGroup] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const reload = async () => {
    try {
      const [qs, groups] = await Promise.all([
        api.sectionQuestions(sectionId),
        api.sectionDiGroups(sectionId),
      ]);
      setQuestions(qs);
      setDiGroups(groups);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to load questions');
    } finally {
      setLoaded(true);
    }
  };

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionId]);

  const blocks = useMemo<Block[]>(() => {
    const qBlocks: Block[] = questions.map((q) => ({ kind: 'question', order: q.order_index, question: q }));
    const gBlocks: Block[] = diGroups.map((g) => ({ kind: 'di_group', order: g.order_index, group: g }));
    return [...qBlocks, ...gBlocks].sort((a, b) => a.order - b.order);
  }, [questions, diGroups]);

  const deleteQuestion = async (id: string) => {
    setDeleteBusy(true);
    try {
      await api.deleteQuestion(id);
      await reload();
      onChanged();
      toast.success('Question deleted');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setDeleteBusy(false);
      setPendingDeleteQuestion(null);
    }
  };

  const deleteGroup = async (id: string) => {
    setDeleteBusy(true);
    try {
      await api.deleteDiGroup(id);
      await reload();
      onChanged();
      toast.success('Question group deleted');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setDeleteBusy(false);
      setPendingDeleteGroup(null);
    }
  };

  const commitReorder = async (fromIndex: number, toIndex: number) => {
    if (fromIndex === toIndex) return;
    const next = [...blocks];
    const [moved] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, moved);
    const items = next.map((b) => ({
      kind: b.kind,
      id: b.kind === 'question' ? b.question.id : b.group.id,
    }));
    try {
      await api.reorderSection(sectionId, items);
      await reload();
      toast.success('Order updated');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Reorder failed');
      await reload();
    }
  };

  if (!loaded) return <SkeletonText width="100%" />;
  if (blocks.length === 0) {
    return <p className="muted small">No questions in this section yet.</p>;
  }

  return (
    <div className="stack">
      {blocks.map((block, index) => {
        const key = block.kind === 'question' ? block.question.id : block.group.id;
        const dragProps = {
          draggable: true,
          onDragStart: () => setDragIndex(index),
          onDragOver: (e: React.DragEvent) => {
            e.preventDefault();
            setOverIndex(index);
          },
          onDragEnd: () => {
            setDragIndex(null);
            setOverIndex(null);
          },
          onDrop: (e: React.DragEvent) => {
            e.preventDefault();
            if (dragIndex !== null) void commitReorder(dragIndex, index);
            setDragIndex(null);
            setOverIndex(null);
          },
        };
        const wrapClass = `${dragIndex === index ? 'dragging' : ''} ${overIndex === index && dragIndex !== null && dragIndex !== index ? 'drop-target' : ''}`;

        if (block.kind === 'question') {
          return (
            <QuestionRow
              key={key}
              question={block.question}
              dragProps={dragProps}
              wrapClass={wrapClass}
              onSaved={() => void reload()}
              onDelete={() => setPendingDeleteQuestion(block.question.id)}
              onError={onError}
            />
          );
        }
        return (
          <DiGroupRow
            key={key}
            group={block.group}
            dragProps={dragProps}
            wrapClass={wrapClass}
            onSaved={() => void reload()}
            onDeleteGroup={() => setPendingDeleteGroup(block.group.id)}
            onDeleteQuestion={(id) => setPendingDeleteQuestion(id)}
            onError={onError}
          />
        );
      })}

      {pendingDeleteQuestion && (
        <ConfirmDialog
          title="Delete this question?"
          description="This cannot be undone."
          confirmLabel="Delete"
          busy={deleteBusy}
          onConfirm={() => void deleteQuestion(pendingDeleteQuestion)}
          onCancel={() => setPendingDeleteQuestion(null)}
        />
      )}
      {pendingDeleteGroup && (
        <ConfirmDialog
          title="Delete this question group?"
          description="This deletes the stimulus and all its questions. This cannot be undone."
          confirmLabel="Delete group"
          busy={deleteBusy}
          onConfirm={() => void deleteGroup(pendingDeleteGroup)}
          onCancel={() => setPendingDeleteGroup(null)}
        />
      )}
    </div>
  );
}

interface DragProps {
  draggable: boolean;
  onDragStart: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragEnd: () => void;
  onDrop: (e: React.DragEvent) => void;
}

interface RowProps {
  question: QuestionDetail;
  dragProps: DragProps;
  wrapClass: string;
  onSaved: () => void;
  onDelete: () => void;
  onError: (msg: string) => void;
}

function QuestionRow({ question, dragProps, wrapClass, onSaved, onDelete, onError }: RowProps) {
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);

  if (editing) {
    const done = () => {
      setEditing(false);
      onSaved();
    };
    return question.type === 'coding' ? (
      <CodingEditForm question={question} onDone={done} onCancel={() => setEditing(false)} onError={onError} />
    ) : (
      <McqEditForm question={question} onDone={done} onCancel={() => setEditing(false)} onError={onError} />
    );
  }

  const preview = question.body_md.length > 140 ? `${question.body_md.slice(0, 140)}…` : question.body_md;

  return (
    <div className={`question-card ${wrapClass}`} {...dragProps}>
      <div className="question-card-head" onClick={() => setOpen((v) => !v)}>
        <span className="drag-handle" onClick={(e) => e.stopPropagation()}>
          <GripVertical size={16} />
        </span>
        <ChevronRight size={15} className={`chevron ${open ? 'open' : ''}`} />
        <span className="tag">{question.type}</span>
        {question.difficulty && <span className={`badge difficulty-${question.difficulty}`}>{question.difficulty}</span>}
        <span className="grow body-preview">{preview}</span>
        <span className="muted small">{question.marks ?? '—'} marks</span>
        <button
          className="btn small"
          onClick={(e) => {
            e.stopPropagation();
            setEditing(true);
          }}
        >
          Edit
        </button>
        <button
          className="btn small ghost danger"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <Trash2 size={13} />
        </button>
      </div>
      {open && (
        <div className="question-card-body">
          {question.tags.length > 0 && (
            <div className="bank-meta" style={{ marginBottom: '0.5rem' }}>
              {question.tags.map((t) => (
                <span key={t} className="tag-chip">
                  {t}
                </span>
              ))}
            </div>
          )}
          <p className="question-body">{question.body_md}</p>
          {question.type !== 'coding' && (
            <div className="options">
              {question.options.map((o, i) => (
                <div key={o.id} className={`option ${o.is_correct ? 'selected' : ''}`}>
                  <span className="option-letter">{String.fromCharCode(65 + i)}</span>
                  <span>{o.body}</span>
                </div>
              ))}
            </div>
          )}
          {question.explanation_md && (
            <div className="explanation-block">
              <strong>Explanation:</strong> {question.explanation_md}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface McqEditProps {
  question: QuestionDetail;
  onDone: () => void;
  onCancel: () => void;
  onError: (msg: string) => void;
}

function McqEditForm({ question, onDone, onCancel, onError }: McqEditProps) {
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
      <label>
        Question
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} required />
      </label>
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

interface CodingEditProps {
  question: QuestionDetail;
  onDone: () => void;
  onCancel: () => void;
  onError: (msg: string) => void;
}

function CodingEditForm({ question, onDone, onCancel, onError }: CodingEditProps) {
  const problem = question.coding_problem;
  const [title, setTitle] = useState(question.body_md);
  const [statement, setStatement] = useState(problem?.statement_md ?? '');
  const [languages, setLanguages] = useState<string[]>(problem?.allowed_languages ?? ['python']);
  const [marks, setMarks] = useState<number | null>(question.marks);
  const [tags, setTags] = useState<string[]>(question.tags);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(question.difficulty);
  const [cases, setCases] = useState(
    (problem?.test_cases ?? []).map((c) => ({
      stdin: c.stdin,
      expected_stdout: c.expected_stdout,
      is_sample: c.is_sample,
      weight: c.weight,
    })),
  );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.updateCoding(question.id, {
        body_md: title,
        statement_md: statement,
        marks,
        allowed_languages: languages,
        time_limit_ms: problem?.time_limit_ms ?? 2000,
        memory_limit_mb: problem?.memory_limit_mb ?? 128,
        starter_code: problem?.starter_code ?? {},
        test_cases: cases.map((c, i) => ({ ...c, order_index: i })),
        tags,
        difficulty,
      });
      onDone();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update problem');
    }
  };

  return (
    <form onSubmit={submit} className="stack sub-card">
      <label>
        Problem title
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
      </label>
      <label>
        Statement (Markdown)
        <textarea value={statement} onChange={(e) => setStatement(e.target.value)} rows={6} required />
      </label>
      <label>
        Marks (blank = section default)
        <input
          type="number"
          step="0.5"
          value={marks ?? ''}
          onChange={(e) => setMarks(e.target.value === '' ? null : Number(e.target.value))}
        />
      </label>
      <div className="lang-row">
        {CODING_LANGUAGES.map((lang) => (
          <label key={lang} className="inline">
            <input
              type="checkbox"
              checked={languages.includes(lang)}
              onChange={(e) =>
                setLanguages(
                  e.target.checked ? [...languages, lang] : languages.filter((l) => l !== lang),
                )
              }
            />
            {lang}
          </label>
        ))}
      </div>
      <div className="field">
        <span className="field-label">Tags</span>
        <TagInput tags={tags} onChange={setTags} />
      </div>
      <div className="field">
        <span className="field-label">Difficulty</span>
        <DifficultyPicker value={difficulty} onChange={setDifficulty} />
      </div>

      <p className="muted small">{codingCaseHint(languages)}</p>

      {cases.map((c, i) => (
        <fieldset key={i} className="sub-card">
          <legend>
            Case {i + 1} {c.is_sample ? '(sample)' : '(hidden)'}
          </legend>
          <div className="case-grid">
            <label>
              stdin
              <textarea
                value={c.stdin}
                rows={2}
                placeholder={
                  languages.every((l) => l === 'sql')
                    ? "CREATE TABLE t(x INT);\nINSERT INTO t VALUES (1),(2);"
                    : '3 4'
                }
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
                placeholder={languages.every((l) => l === 'sql') ? '1\n2' : '7'}
                onChange={(e) => {
                  const next = [...cases];
                  next[i] = { ...c, expected_stdout: e.target.value };
                  setCases(next);
                }}
              />
            </label>
          </div>
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
          <button
            type="button"
            className="btn link danger"
            onClick={() => setCases(cases.filter((_, idx) => idx !== i))}
          >
            Remove case
          </button>
        </fieldset>
      ))}

      <div className="form-actions">
        <button
          type="button"
          className="btn"
          onClick={() =>
            setCases([...cases, { stdin: '', expected_stdout: '', is_sample: false, weight: 1 }])
          }
        >
          + Test case
        </button>
        <button className="btn primary">Save changes</button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface DiGroupRowProps {
  group: DIGroupDetail;
  dragProps: DragProps;
  wrapClass: string;
  onSaved: () => void;
  onDeleteGroup: () => void;
  onDeleteQuestion: (id: string) => void;
  onError: (msg: string) => void;
}

function DiGroupRow({ group, dragProps, wrapClass, onSaved, onDeleteGroup, onDeleteQuestion, onError }: DiGroupRowProps) {
  const [open, setOpen] = useState(false);
  const [editingMeta, setEditingMeta] = useState(false);
  const [title, setTitle] = useState(group.title);
  const [passage, setPassage] = useState(group.passage_md ?? '');
  const [imageUrl, setImageUrl] = useState(group.image_url ?? '');

  const upload = async (file: File) => {
    try {
      const { image_url } = await api.uploadImage(file);
      setImageUrl(image_url);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Image upload failed');
    }
  };

  const saveMeta = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.updateDiGroup(group.id, {
        title,
        passage_md: passage || null,
        image_url: imageUrl || null,
      });
      setEditingMeta(false);
      onSaved();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to update question group');
    }
  };

  return (
    <div className={`question-card ${wrapClass}`} {...dragProps}>
      <div className="question-card-head" onClick={() => setOpen((v) => !v)}>
        <span className="drag-handle" onClick={(e) => e.stopPropagation()}>
          <GripVertical size={16} />
        </span>
        <ChevronRight size={15} className={`chevron ${open ? 'open' : ''}`} />
        <span className="tag badge-info">group</span>
        <strong className="grow">{group.title}</strong>
        <span className="muted small">{group.questions.length} question{group.questions.length === 1 ? '' : 's'}</span>
        <button
          className="btn small"
          onClick={(e) => {
            e.stopPropagation();
            setEditingMeta((v) => !v);
            setOpen(true);
          }}
        >
          {editingMeta ? 'Close' : 'Edit set'}
        </button>
        <button
          className="btn small ghost danger"
          onClick={(e) => {
            e.stopPropagation();
            onDeleteGroup();
          }}
        >
          <Trash2 size={13} />
        </button>
      </div>

      {open && (
        <div className="question-card-body">
          {editingMeta && (
            <form onSubmit={saveMeta} className="stack">
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
              <div className="form-actions">
                <button className="btn primary">Save group</button>
              </div>
            </form>
          )}

          <div className="stack">
            {group.questions.map((q) => (
              <QuestionRow
                key={q.id}
                question={q}
                dragProps={{ draggable: false, onDragStart: () => {}, onDragOver: () => {}, onDragEnd: () => {}, onDrop: () => {} }}
                wrapClass=""
                onSaved={onSaved}
                onDelete={() => onDeleteQuestion(q.id)}
                onError={onError}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

interface FormProps {
  sectionId: string;
  onDone: () => void;
  onError: (msg: string) => void;
}

function McqForm({ sectionId, onDone, onError }: FormProps) {
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
      <label>
        Question
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} required />
      </label>
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

function DiForm({ sectionId, onDone, onError }: FormProps) {
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

function CodingForm({ sectionId, onDone, onError }: FormProps) {
  const toast = useToast();
  const [title, setTitle] = useState('');
  const [statement, setStatement] = useState('');
  const [languages, setLanguages] = useState<string[]>(['python']);
  const [tags, setTags] = useState<string[]>([]);
  const [difficulty, setDifficulty] = useState<Difficulty | null>(null);
  const [cases, setCases] = useState([
    { stdin: '', expected_stdout: '', is_sample: true, weight: 1 },
    { stdin: '', expected_stdout: '', is_sample: false, weight: 1 },
  ]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createCoding(sectionId, {
        body_md: title,
        statement_md: statement,
        allowed_languages: languages,
        test_cases: cases.map((c, i) => ({ ...c, order_index: i })),
        tags,
        difficulty,
      });
      setTitle('');
      setStatement('');
      setTags([]);
      setDifficulty(null);
      setCases([
        { stdin: '', expected_stdout: '', is_sample: true, weight: 1 },
        { stdin: '', expected_stdout: '', is_sample: false, weight: 1 },
      ]);
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
      <label>
        Statement (Markdown)
        <textarea
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
          rows={6}
          required
        />
      </label>
      <div className="lang-row">
        {CODING_LANGUAGES.map((lang) => (
          <label key={lang} className="inline">
            <input
              type="checkbox"
              checked={languages.includes(lang)}
              onChange={(e) =>
                setLanguages(
                  e.target.checked
                    ? [...languages, lang]
                    : languages.filter((l) => l !== lang),
                )
              }
            />
            {lang}
          </label>
        ))}
      </div>
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
      <p className="muted small">{codingCaseHint(languages)}</p>

      {cases.map((c, i) => (
        <fieldset key={i} className="sub-card">
          <legend>
            Case {i + 1} {c.is_sample ? '(sample)' : '(hidden)'}
          </legend>
          <div className="case-grid">
            <label>
              stdin
              <textarea
                value={c.stdin}
                rows={2}
                placeholder={
                  languages.every((l) => l === 'sql')
                    ? "CREATE TABLE t(x INT);\nINSERT INTO t VALUES (1),(2);"
                    : '3 4'
                }
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
                placeholder={languages.every((l) => l === 'sql') ? '1\n2' : '7'}
                onChange={(e) => {
                  const next = [...cases];
                  next[i] = { ...c, expected_stdout: e.target.value };
                  setCases(next);
                }}
              />
            </label>
          </div>
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
            setCases([...cases, { stdin: '', expected_stdout: '', is_sample: false, weight: 1 }])
          }
        >
          + Test case
        </button>
        <button className="btn primary">Save problem</button>
      </div>
    </form>
  );
}

function BulkForm({ sectionId, onDone, onError }: FormProps) {
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
