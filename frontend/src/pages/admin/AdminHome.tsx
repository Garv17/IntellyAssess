import {
  BarChart3,
  ClipboardList,
  Download,
  Edit3,
  FileText,
  GraduationCap,
  KeyRound,
  Layers,
  Plus,
  Radio,
  Sparkles,
  Trash2,
  UploadCloud,
  Users,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ApiError,
  api,
  downloadResults,
  type BulkResult,
  type Exam,
  type OverviewStats,
  type Student,
  type StudentCredential,
} from '../../api';
import AppShell from '../../components/AppShell';
import { Identity } from '../../components/Avatar';
import ConfirmDialog from '../../components/ConfirmDialog';
import DropdownMenu, { DropdownItem, DropdownSeparator } from '../../components/DropdownMenu';
import EmptyState from '../../components/EmptyState';
import { SkeletonStatRow, SkeletonTable } from '../../components/Skeleton';
import { useToast } from '../../components/Toast';

export default function AdminHome() {
  const navigate = useNavigate();
  const toast = useToast();
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [exams, setExams] = useState<Exam[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    title: '',
    description: '',
    duration_minutes: 60,
    cohort: '',
    pass_percentage: '',
  });
  const [bulk, setBulk] = useState<BulkResult | null>(null);
  const [uploading, setUploading] = useState(false);

  const [students, setStudents] = useState<Student[]>([]);
  const [studentsLoading, setStudentsLoading] = useState(true);
  const [studentCohortFilter, setStudentCohortFilter] = useState('');
  const [editingExam, setEditingExam] = useState<Exam | null>(null);
  const [examEditForm, setExamEditForm] = useState({
    title: '',
    description: '',
    duration_minutes: 60,
    cohort: '',
    pass_percentage: '',
  });
  const [deletingExam, setDeletingExam] = useState<Exam | null>(null);
  const [deletingExamBusy, setDeletingExamBusy] = useState(false);
  const [editingStudent, setEditingStudent] = useState<Student | null>(null);
  const [studentEditForm, setStudentEditForm] = useState({
    name: '',
    email: '',
    cohort: '',
    is_active: true,
  });
  const [deletingStudent, setDeletingStudent] = useState<Student | null>(null);
  const [deletingStudentBusy, setDeletingStudentBusy] = useState(false);
  const [resetPasswordResult, setResetPasswordResult] = useState<StudentCredential | null>(null);

  const load = async () => {
    try {
      const [examList, overview] = await Promise.all([api.adminExams(), api.overviewStats()]);
      setExams(examList);
      setStats(overview);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        navigate('/admin/login', { replace: true });
        return;
      }
      setError(err instanceof Error ? err.message : 'Could not load exams');
    } finally {
      setLoading(false);
    }
  };

  const loadStudents = async (cohort?: string) => {
    setStudentsLoading(true);
    try {
      setStudents(await api.students(cohort || undefined));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load students');
    } finally {
      setStudentsLoading(false);
    }
  };

  useEffect(() => {
    void load();
    void loadStudents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openEditExam = (exam: Exam) => {
    setEditingExam(exam);
    setExamEditForm({
      title: exam.title,
      description: exam.description ?? '',
      duration_minutes: exam.duration_minutes,
      cohort: exam.cohort ?? '',
      pass_percentage: exam.pass_percentage != null ? String(exam.pass_percentage) : '',
    });
  };

  const saveExamEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingExam) return;
    setError(null);
    try {
      await api.updateExam(editingExam.id, {
        title: examEditForm.title,
        description: examEditForm.description || null,
        duration_minutes: Number(examEditForm.duration_minutes),
        cohort: examEditForm.cohort || null,
        pass_percentage: examEditForm.pass_percentage ? Number(examEditForm.pass_percentage) : null,
      });
      setEditingExam(null);
      await load();
      toast.success('Exam updated');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update the exam');
    }
  };

  const confirmDeleteExam = async () => {
    if (!deletingExam) return;
    setError(null);
    setDeletingExamBusy(true);
    try {
      await api.deleteExam(deletingExam.id);
      setDeletingExam(null);
      await load();
      toast.success('Exam deleted');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not delete the exam');
    } finally {
      setDeletingExamBusy(false);
    }
  };

  const openEditStudent = (student: Student) => {
    setEditingStudent(student);
    setStudentEditForm({
      name: student.name,
      email: student.email ?? '',
      cohort: student.cohort ?? '',
      is_active: student.is_active,
    });
  };

  const saveStudentEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingStudent) return;
    setError(null);
    try {
      await api.updateStudent(editingStudent.id, {
        name: studentEditForm.name,
        email: studentEditForm.email || null,
        cohort: studentEditForm.cohort || null,
        is_active: studentEditForm.is_active,
      });
      setEditingStudent(null);
      await loadStudents(studentCohortFilter);
      toast.success('Student updated');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update the student');
    }
  };

  const confirmDeleteStudent = async () => {
    if (!deletingStudent) return;
    setError(null);
    setDeletingStudentBusy(true);
    try {
      await api.deleteStudent(deletingStudent.id);
      setDeletingStudent(null);
      await loadStudents(studentCohortFilter);
      toast.success('Student deleted');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not delete the student');
    } finally {
      setDeletingStudentBusy(false);
    }
  };

  const resetPassword = async (student: Student) => {
    setError(null);
    try {
      setResetPasswordResult(await api.resetStudentPassword(student.id));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not reset the password');
    }
  };

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    try {
      const exam = await api.createExam({
        title: form.title,
        description: form.description || null,
        duration_minutes: Number(form.duration_minutes),
        cohort: form.cohort || null,
        pass_percentage: form.pass_percentage ? Number(form.pass_percentage) : null,
      });
      navigate(`/admin/exams/${exam.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the exam');
    } finally {
      setCreating(false);
    }
  };

  const uploadStudents = async (file: File) => {
    setError(null);
    setUploading(true);
    try {
      const result = await api.bulkStudents(file);
      setBulk(result);
      toast.success(`Created ${result.created} student${result.created === 1 ? '' : 's'}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const downloadCredentials = () => {
    if (!bulk?.credentials.length) return;
    const csv = [
      'enrollment_id,name,password',
      ...bulk.credentials.map((c) => `${c.student_id},"${c.name}",${c.password}`),
    ].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'student-credentials.csv';
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <AppShell title="Dashboard" adminName="Admin">
      {error && <div className="banner error">{error}</div>}

      {loading ? (
        <SkeletonStatRow count={4} />
      ) : (
        <div className="stat-row">
          <div className="stat">
            <div className="stat-icon">
              <Layers size={17} />
            </div>
            <strong>{stats?.exams ?? 0}</strong>
            <span>Exams</span>
          </div>
          <div className="stat">
            <div className="stat-icon">
              <Users size={17} />
            </div>
            <strong>{stats?.students ?? 0}</strong>
            <span>Students</span>
          </div>
          <div className="stat active">
            <div className="stat-icon">
              <ClipboardList size={17} />
            </div>
            <strong>{stats?.attempts ?? 0}</strong>
            <span>Attempts</span>
          </div>
          <div className="stat ok">
            <div className="stat-icon">
              <BarChart3 size={17} />
            </div>
            <strong>{stats?.average_score != null ? `${stats.average_score}%` : '—'}</strong>
            <span>Average score</span>
          </div>
        </div>
      )}

      <div className="card-grid">
        <section className="card compact">
          <div className="card-header">
            <h2>
              <Plus size={16} style={{ verticalAlign: -2, marginRight: 4 }} />
              Create exam
            </h2>
          </div>
          <form className="stack" onSubmit={create}>
            <div className="row-form">
              <label className="grow">
                Title
                <input
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                  placeholder="Aptitude Assessment 2026"
                  required
                />
              </label>
              <label>
                Duration (min)
                <input
                  type="number"
                  min={1}
                  max={600}
                  value={form.duration_minutes}
                  onChange={(e) => setForm({ ...form, duration_minutes: Number(e.target.value) })}
                />
              </label>
            </div>
            <div className="row-form">
              <label>
                Cohort
                <input
                  value={form.cohort}
                  onChange={(e) => setForm({ ...form, cohort: e.target.value })}
                  placeholder="e.g. 2026 (blank = everyone)"
                />
              </label>
              <label>
                Pass %
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={form.pass_percentage}
                  onChange={(e) => setForm({ ...form, pass_percentage: e.target.value })}
                  placeholder="optional"
                />
              </label>
            </div>
            <div className="form-actions">
              <button className="btn primary" disabled={creating}>
                {creating ? 'Creating…' : 'Create & open builder'}
              </button>
            </div>
          </form>
        </section>

        <section className="card compact">
          <div className="card-header">
            <h2>
              <UploadCloud size={16} style={{ verticalAlign: -2, marginRight: 4 }} />
              Upload students
            </h2>
          </div>
          <p className="muted small">
            CSV columns: <code>enrollment_id, name, email, cohort, dob, password</code>. Leave{' '}
            <code>password</code> blank to auto-generate. Generated passwords are shown once.
          </p>
          <label className="btn" style={{ cursor: 'pointer', display: 'inline-flex' }}>
            <UploadCloud size={15} />
            {uploading ? 'Uploading…' : 'Choose CSV file'}
            <input
              type="file"
              accept=".csv"
              style={{ display: 'none' }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void uploadStudents(file);
              }}
            />
          </label>
          {bulk && (
            <div className="banner ok" style={{ marginTop: '0.85rem' }}>
              <Sparkles size={16} />
              <div>
                Created {bulk.created}, skipped {bulk.skipped}.{' '}
                {bulk.credentials.length > 0 && (
                  <button className="btn link" onClick={downloadCredentials}>
                    Download credentials CSV
                  </button>
                )}
                {bulk.errors.length > 0 && (
                  <ul className="errors">
                    {bulk.errors.slice(0, 10).map((msg) => (
                      <li key={msg}>{msg}</li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </section>
      </div>

      <section className="card">
        <div className="card-header">
          <h2>Exams</h2>
        </div>
        {loading ? (
          <SkeletonTable rows={4} />
        ) : exams.length === 0 ? (
          <EmptyState
            icon={<FileText size={24} />}
            title="No exams yet"
            description="Create your first exam using the form above to get started."
          />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Status</th>
                  <th>Duration</th>
                  <th>Cohort</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {exams.map((exam) => (
                  <tr key={exam.id}>
                    <td>
                      <Link to={`/admin/exams/${exam.id}`} style={{ fontWeight: 600, color: 'var(--text)', textDecoration: 'none' }}>
                        {exam.title}
                      </Link>
                    </td>
                    <td>
                      <span className={`tag ${exam.status}`}>{exam.status}</span>
                    </td>
                    <td>{exam.duration_minutes} min</td>
                    <td>{exam.cohort ?? 'All'}</td>
                    <td>{new Date(exam.created_at).toLocaleDateString()}</td>
                    <td className="actions">
                      <DropdownMenu>
                        <DropdownItem icon={<Edit3 size={15} />} onClick={() => navigate(`/admin/exams/${exam.id}`)}>
                          Build paper
                        </DropdownItem>
                        <DropdownItem icon={<Radio size={15} />} onClick={() => navigate(`/admin/exams/${exam.id}/live`)}>
                          Live monitor
                        </DropdownItem>
                        <DropdownItem icon={<BarChart3 size={15} />} onClick={() => navigate(`/admin/exams/${exam.id}/analytics`)}>
                          Analytics
                        </DropdownItem>
                        <DropdownItem
                          icon={<Download size={15} />}
                          onClick={() => void downloadResults(exam.id, `${exam.title}-results.xlsx`)}
                        >
                          Export results
                        </DropdownItem>
                        <DropdownSeparator />
                        <DropdownItem icon={<Edit3 size={15} />} onClick={() => openEditExam(exam)}>
                          Edit details
                        </DropdownItem>
                        <DropdownItem icon={<Trash2 size={15} />} danger onClick={() => setDeletingExam(exam)}>
                          Delete
                        </DropdownItem>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <h2>Students</h2>
        </div>
        <div className="row-form">
          <label>
            Filter by cohort
            <input
              value={studentCohortFilter}
              onChange={(e) => setStudentCohortFilter(e.target.value)}
              placeholder="e.g. 2026"
            />
          </label>
          <button className="btn" onClick={() => void loadStudents(studentCohortFilter)}>
            Filter
          </button>
          {studentCohortFilter && (
            <button
              className="btn link"
              onClick={() => {
                setStudentCohortFilter('');
                void loadStudents();
              }}
            >
              Clear
            </button>
          )}
        </div>
        {studentsLoading ? (
          <SkeletonTable rows={4} />
        ) : students.length === 0 ? (
          <EmptyState icon={<GraduationCap size={24} />} title="No students yet" description="Upload a CSV above to add your first batch." />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Student</th>
                  <th>Enrollment ID</th>
                  <th>Email</th>
                  <th>Cohort</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {students.map((student) => (
                  <tr key={student.id}>
                    <td>
                      <Identity name={student.name} size="sm" />
                    </td>
                    <td>{student.student_id}</td>
                    <td>{student.email ?? '—'}</td>
                    <td>{student.cohort ?? '—'}</td>
                    <td>
                      <span className={`badge ${student.is_active ? 'badge-success' : 'badge-neutral'}`}>
                        {student.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="actions">
                      <DropdownMenu>
                        <DropdownItem icon={<Edit3 size={15} />} onClick={() => openEditStudent(student)}>
                          Edit
                        </DropdownItem>
                        <DropdownItem icon={<KeyRound size={15} />} onClick={() => void resetPassword(student)}>
                          Reset password
                        </DropdownItem>
                        <DropdownSeparator />
                        <DropdownItem icon={<Trash2 size={15} />} danger onClick={() => setDeletingStudent(student)}>
                          Delete
                        </DropdownItem>
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {editingExam && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>Edit exam</h3>
            <form onSubmit={saveExamEdit}>
              <div className="stack">
                <label>
                  Title
                  <input
                    value={examEditForm.title}
                    onChange={(e) => setExamEditForm({ ...examEditForm, title: e.target.value })}
                    required
                  />
                </label>
                <div className="row-form">
                  <label>
                    Duration (min)
                    <input
                      type="number"
                      min={1}
                      max={600}
                      value={examEditForm.duration_minutes}
                      onChange={(e) =>
                        setExamEditForm({
                          ...examEditForm,
                          duration_minutes: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    Cohort
                    <input
                      value={examEditForm.cohort}
                      onChange={(e) => setExamEditForm({ ...examEditForm, cohort: e.target.value })}
                      placeholder="e.g. 2026"
                    />
                  </label>
                  <label>
                    Pass %
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={examEditForm.pass_percentage}
                      onChange={(e) =>
                        setExamEditForm({ ...examEditForm, pass_percentage: e.target.value })
                      }
                      placeholder="optional"
                    />
                  </label>
                </div>
              </div>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setEditingExam(null)}>
                  Cancel
                </button>
                <button className="btn primary" type="submit">
                  Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deletingExam && (
        <ConfirmDialog
          title="Delete exam?"
          description={
            <>
              This will permanently delete <strong>{deletingExam.title}</strong>. Exams with
              student attempts cannot be deleted — close them instead.
            </>
          }
          confirmLabel="Delete exam"
          busy={deletingExamBusy}
          onConfirm={() => void confirmDeleteExam()}
          onCancel={() => setDeletingExam(null)}
        />
      )}

      {editingStudent && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>Edit student</h3>
            <p className="muted small">Enrollment ID: {editingStudent.student_id}</p>
            <form onSubmit={saveStudentEdit}>
              <div className="stack">
                <label>
                  Name
                  <input
                    value={studentEditForm.name}
                    onChange={(e) =>
                      setStudentEditForm({ ...studentEditForm, name: e.target.value })
                    }
                    required
                  />
                </label>
                <label>
                  Email
                  <input
                    type="email"
                    value={studentEditForm.email}
                    onChange={(e) =>
                      setStudentEditForm({ ...studentEditForm, email: e.target.value })
                    }
                  />
                </label>
                <label>
                  Cohort
                  <input
                    value={studentEditForm.cohort}
                    onChange={(e) =>
                      setStudentEditForm({ ...studentEditForm, cohort: e.target.value })
                    }
                    placeholder="e.g. 2026"
                  />
                </label>
                <label className="inline">
                  <input
                    type="checkbox"
                    checked={studentEditForm.is_active}
                    onChange={(e) =>
                      setStudentEditForm({ ...studentEditForm, is_active: e.target.checked })
                    }
                  />
                  Active (can sign in)
                </label>
              </div>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setEditingStudent(null)}>
                  Cancel
                </button>
                <button className="btn primary" type="submit">
                  Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deletingStudent && (
        <ConfirmDialog
          title="Delete student?"
          description={
            <>
              This will permanently delete <strong>{deletingStudent.name}</strong> (
              {deletingStudent.student_id}). Students with exam attempts cannot be deleted — set
              them inactive instead.
            </>
          }
          confirmLabel="Delete student"
          busy={deletingStudentBusy}
          onConfirm={() => void confirmDeleteStudent()}
          onCancel={() => setDeletingStudent(null)}
        />
      )}

      {resetPasswordResult && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <div className="modal-icon info">
              <KeyRound size={20} />
            </div>
            <h3>Password reset</h3>
            <p>
              New password for <strong>{resetPasswordResult.name}</strong> (
              {resetPasswordResult.student_id}):
            </p>
            <p>
              <code style={{ fontSize: '1rem' }}>{resetPasswordResult.password}</code>
            </p>
            <p className="muted small">
              This is shown once and cannot be recovered afterwards — share it with the student
              now.
            </p>
            <div className="modal-actions">
              <button className="btn primary" onClick={() => setResetPasswordResult(null)}>
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
