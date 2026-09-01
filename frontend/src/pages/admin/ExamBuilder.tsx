import { BookMarked, Code2, Database, ListChecks, Plus, Rocket, Table2, Upload } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type PublishResult, type Section } from '../../api';
import AppShell from '../../components/AppShell';
import { useToast } from '../../components/Toast';
import { errorContext, log } from '../../logger';
import BulkForm from './exam-builder/BulkForm';
import CodingForm from './exam-builder/CodingForm';
import DiForm from './exam-builder/DiForm';
import ExistingQuestions from './exam-builder/ExistingQuestions';
import McqForm from './exam-builder/McqForm';
import QuestionBankPanel from './exam-builder/QuestionBankPanel';
import SqlQuestionForm from './exam-builder/SqlQuestionForm';

type Tab = 'mcq' | 'di' | 'coding' | 'bulk' | 'bank';
type CodingFormat = 'programming' | 'sql';

export default function ExamBuilder() {
  const { examId = '' } = useParams();
  const toast = useToast();
  const [sections, setSections] = useState<Section[]>([]);
  const [activeSection, setActiveSection] = useState<string>('');
  const [tab, setTab] = useState<Tab>('mcq');
  const [codingFormat, setCodingFormat] = useState<CodingFormat>('programming');
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
    void load().catch((err) => {
      log.warn('exam builder sections failed to load', { exam_id: examId, ...errorContext(err) });
      setError(err.message);
    });
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
      // A rejected publish is a content problem, not a fault — but a publish
      // that fails for any *other* reason on exam morning is worth finding.
      log.warn('exam publish failed', {
        exam_id: examId,
        problem_count: list?.length ?? 0,
        ...errorContext(err),
      });
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
            <div className="stack">
              <div className="problem-type-toggle">
                <button
                  type="button"
                  className={`btn ${codingFormat === 'programming' ? 'primary' : ''}`}
                  onClick={() => setCodingFormat('programming')}
                >
                  <Code2 size={14} /> Programming Language
                </button>
                <button
                  type="button"
                  className={`btn ${codingFormat === 'sql' ? 'primary' : ''}`}
                  onClick={() => setCodingFormat('sql')}
                >
                  <Database size={14} /> SQL / Database
                </button>
              </div>
              {codingFormat === 'programming' ? (
                <CodingForm sectionId={activeSection} onDone={() => void load()} onError={setError} />
              ) : (
                <SqlQuestionForm sectionId={activeSection} onDone={() => void load()} onError={setError} />
              )}
            </div>
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
