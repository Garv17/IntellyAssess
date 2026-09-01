import { Edit3, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { ApiError, api, type AdminRole, type AdminUser } from '../../api';
import AppShell from '../../components/AppShell';
import { Identity } from '../../components/Avatar';
import ConfirmDialog from '../../components/ConfirmDialog';
import DropdownMenu, { DropdownItem, DropdownSeparator } from '../../components/DropdownMenu';
import EmptyState from '../../components/EmptyState';
import { SkeletonTable } from '../../components/Skeleton';
import { useToast } from '../../components/Toast';
import { errorContext, log } from '../../logger';

const EMPTY_FORM = { email: '', name: '', password: '', role: 'admin' as AdminRole };

export default function AdminManagement() {
  const toast = useToast();
  const [me, setMe] = useState<AdminUser | null>(null);
  const [admins, setAdmins] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [editingAdmin, setEditingAdmin] = useState<AdminUser | null>(null);
  const [editForm, setEditForm] = useState({ name: '', role: 'admin' as AdminRole, is_active: true, password: '' });
  const [deletingAdmin, setDeletingAdmin] = useState<AdminUser | null>(null);
  const [deletingBusy, setDeletingBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [identity, list] = await Promise.all([api.me(), api.admins()]);
      setMe(list.find((a) => a.id === identity.id) ?? null);
      setAdmins(list);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setForbidden(true);
      } else {
        log.warn('admin management load failed', errorContext(err));
        setError(err instanceof Error ? err.message : 'Could not load admins');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await api.createAdmin(form);
      setForm(EMPTY_FORM);
      toast.success('Admin added');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not add admin');
    } finally {
      setCreating(false);
    }
  };

  const openEdit = (target: AdminUser) => {
    setEditingAdmin(target);
    setEditForm({ name: target.name, role: target.role, is_active: target.is_active, password: '' });
  };

  const saveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingAdmin) return;
    try {
      await api.updateAdmin(editingAdmin.id, {
        name: editForm.name,
        role: editForm.role,
        is_active: editForm.is_active,
        password: editForm.password || undefined,
      });
      setEditingAdmin(null);
      toast.success('Admin updated');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not update admin');
    }
  };

  const confirmDelete = async () => {
    if (!deletingAdmin) return;
    setDeletingBusy(true);
    try {
      await api.deleteAdmin(deletingAdmin.id);
      setDeletingAdmin(null);
      toast.success('Admin removed');
      await load();
    } catch (err) {
      log.warn('admin delete failed', { admin_id: deletingAdmin.id, ...errorContext(err) });
      toast.error(err instanceof Error ? err.message : 'Could not remove admin');
    } finally {
      setDeletingBusy(false);
    }
  };

  if (forbidden) {
    return (
      <AppShell title="Admin Management" adminName="Admin">
        <EmptyState
          icon={<ShieldCheck size={24} />}
          title="Super admin access required"
          description="Ask an existing super admin to promote your account before you can manage admins."
        />
      </AppShell>
    );
  }

  return (
    <AppShell title="Admin Management" adminName="Admin">
      {error && <div className="banner error">{error}</div>}

      <section className="card compact">
        <div className="card-header">
          <h2>
            <Plus size={16} style={{ verticalAlign: -2, marginRight: 4 }} />
            Add admin
          </h2>
        </div>
        <form className="stack" onSubmit={create}>
          <div className="row-form">
            <label className="grow">
              Name
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </label>
            <label className="grow">
              Email
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                required
              />
            </label>
          </div>
          <div className="row-form">
            <label className="grow">
              Password
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                minLength={8}
                placeholder="At least 8 characters"
                required
              />
            </label>
            <label>
              Role
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as AdminRole })}>
                <option value="admin">Admin</option>
                <option value="super_admin">Super admin</option>
              </select>
            </label>
          </div>
          <div className="form-actions">
            <button className="btn primary" disabled={creating}>
              {creating ? 'Adding…' : 'Add admin'}
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <div className="card-header">
          <h2>Admins</h2>
        </div>
        <p className="muted small">
          Super admins can add, remove, and update admin accounts, including other super admins.
          Regular admins can use the rest of the console but not this page.
        </p>
        {loading ? (
          <SkeletonTable rows={4} />
        ) : admins.length === 0 ? (
          <EmptyState icon={<ShieldCheck size={24} />} title="No admins yet" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Admin</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {admins.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <Identity name={a.name} sub={a.id === me?.id ? 'You' : undefined} size="sm" />
                    </td>
                    <td>{a.email}</td>
                    <td>
                      <span className={`badge ${a.role === 'super_admin' ? 'badge-info' : 'badge-neutral'}`}>
                        {a.role === 'super_admin' ? 'Super admin' : 'Admin'}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${a.is_active ? 'badge-success' : 'badge-neutral'}`}>
                        {a.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="actions">
                      <DropdownMenu>
                        <DropdownItem icon={<Edit3 size={15} />} onClick={() => openEdit(a)}>
                          Edit
                        </DropdownItem>
                        {a.id !== me?.id && (
                          <>
                            <DropdownSeparator />
                            <DropdownItem icon={<Trash2 size={15} />} danger onClick={() => setDeletingAdmin(a)}>
                              Remove
                            </DropdownItem>
                          </>
                        )}
                      </DropdownMenu>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {editingAdmin && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>Edit admin</h3>
            <p className="muted small">{editingAdmin.email}</p>
            <form onSubmit={saveEdit}>
              <div className="stack">
                <label>
                  Name
                  <input value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} required />
                </label>
                <label>
                  Role
                  <select
                    value={editForm.role}
                    onChange={(e) => setEditForm({ ...editForm, role: e.target.value as AdminRole })}
                    disabled={editingAdmin.id === me?.id}
                  >
                    <option value="admin">Admin</option>
                    <option value="super_admin">Super admin</option>
                  </select>
                </label>
                <label className="inline">
                  <input
                    type="checkbox"
                    checked={editForm.is_active}
                    disabled={editingAdmin.id === me?.id}
                    onChange={(e) => setEditForm({ ...editForm, is_active: e.target.checked })}
                  />
                  Active (can sign in)
                </label>
                <label>
                  Reset password (optional)
                  <input
                    type="password"
                    value={editForm.password}
                    onChange={(e) => setEditForm({ ...editForm, password: e.target.value })}
                    minLength={8}
                    placeholder="Leave blank to keep the current password"
                  />
                </label>
                {editingAdmin.id === me?.id && (
                  <p className="muted small">You can't change your own role or active status.</p>
                )}
              </div>
              <div className="modal-actions">
                <button type="button" className="btn" onClick={() => setEditingAdmin(null)}>
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

      {deletingAdmin && (
        <ConfirmDialog
          title="Remove admin?"
          description={
            <>
              This will permanently remove <strong>{deletingAdmin.name}</strong> ({deletingAdmin.email}
              ) from the admin console.
            </>
          }
          confirmLabel="Remove admin"
          busy={deletingBusy}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeletingAdmin(null)}
        />
      )}
    </AppShell>
  );
}
