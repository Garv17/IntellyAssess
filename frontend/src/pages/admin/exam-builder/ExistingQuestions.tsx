import { ChevronRight, GripVertical, ImagePlus, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, type DIGroupDetail, type QuestionDetail } from '../../../api';
import ConfirmDialog from '../../../components/ConfirmDialog';
import { SkeletonText } from '../../../components/Skeleton';
import { useToast } from '../../../components/Toast';
import CodingEditForm from './CodingEditForm';
import McqEditForm from './McqEditForm';
import SqlQuestionEditForm from './SqlQuestionEditForm';

type Block =
  | { kind: 'question'; order: number; question: QuestionDetail }
  | { kind: 'di_group'; order: number; group: DIGroupDetail };

interface DragProps {
  draggable: boolean;
  onDragStart: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragEnd: () => void;
  onDrop: (e: React.DragEvent) => void;
}

const NO_DRAG: DragProps = {
  draggable: false,
  onDragStart: () => {},
  onDragOver: () => {},
  onDragEnd: () => {},
  onDrop: () => {},
};

export default function ExistingQuestions({
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
        const dragProps: DragProps = {
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
    const isSqlQuestion =
      question.type === 'coding' &&
      question.coding_problem?.allowed_languages.length === 1 &&
      question.coding_problem.allowed_languages[0] === 'sql';
    if (isSqlQuestion) {
      return (
        <SqlQuestionEditForm question={question} onDone={done} onCancel={() => setEditing(false)} onError={onError} />
      );
    }
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

function DiGroupRow({
  group,
  dragProps,
  wrapClass,
  onSaved,
  onDeleteGroup,
  onDeleteQuestion,
  onError,
}: {
  group: DIGroupDetail;
  dragProps: DragProps;
  wrapClass: string;
  onSaved: () => void;
  onDeleteGroup: () => void;
  onDeleteQuestion: (id: string) => void;
  onError: (msg: string) => void;
}) {
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
                dragProps={NO_DRAG}
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
