import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { Modal } from './Modal.jsx';
import { adminInvoke } from '../lib/adminApi.js';
import { PLAN_OPTIONS } from '../lib/constants.js';

// Super-admin edit control over a tenant's profile. Routes through the
// service-role admin-data Edge Function (RLS hides other tenants from the anon
// key). API keys live in Vault and briefings are now jsonb config the tenant
// edits in their own setup — so the platform owner edits only name/email/plan
// and the onboarded flag here.
export function TenantConfigModal({ open, tenant, onClose, onSaved }) {
    const [form, setForm] = useState({});
    const [error, setError] = useState(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (open && tenant) {
            setForm({
                name: tenant.name || '',
                email: tenant.email || '',
                plan: tenant.plan || 'free',
                onboarded: Boolean(tenant.onboarded),
            });
            setError(null);
        }
    }, [open, tenant]);

    const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

    async function handleSubmit(e) {
        e.preventDefault();
        setError(null);
        setSaving(true);
        try {
            await adminInvoke('set_profile', {
                tenant_id: tenant.tenant_id,
                name: form.name,
                email: form.email,
                plan: form.plan,
                onboarded: form.onboarded,
            });
            onSaved?.();
            onClose();
        } catch (err) {
            setError(err.message || 'Could not save changes.');
        } finally {
            setSaving(false);
        }
    }

    if (!tenant) return null;

    return (
        <Modal open={open} onClose={onClose} labelledBy="tenant-config-title">
            <div className="modal-card">
                <div className="modal-head">
                    <div>
                        <p className="modal-eyebrow">{tenant.email || 'tenant'}</p>
                        <h2 id="tenant-config-title">Edit tenant configuration</h2>
                    </div>
                    <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X /></button>
                </div>

                <form className="modal-form" onSubmit={handleSubmit}>
                    <div className="modal-body">
                        <label className="field">
                            <span className="field-label">Name</span>
                            <div className="field-input">
                                <input value={form.name} onChange={e => set('name', e.target.value)} placeholder="Tenant name" />
                            </div>
                        </label>

                        <label className="field">
                            <span className="field-label">Billing email</span>
                            <div className="field-input">
                                <input value={form.email} onChange={e => set('email', e.target.value)} placeholder="you@example.com" />
                            </div>
                        </label>

                        <label className="field">
                            <span className="field-label">Plan</span>
                            <div className="field-input is-select">
                                <select value={form.plan} onChange={e => set('plan', e.target.value)}>
                                    {PLAN_OPTIONS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                                </select>
                            </div>
                        </label>

                        <label className="toggle-row">
                            <input type="checkbox" checked={form.onboarded} onChange={e => set('onboarded', e.target.checked)} />
                            <span>Onboarded (unchecked = tenant sees the setup page on next login)</span>
                        </label>

                        <p className="panel-sub" style={{ marginTop: 4 }}>
                            API keys are stored in Vault and brand/script config is edited by the tenant in their own setup.
                        </p>

                        {error && <div className="auth-error">{error}</div>}
                    </div>

                    <div className="modal-foot">
                        <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={saving}>
                            {saving ? 'Saving…' : 'Save changes'}
                        </button>
                    </div>
                </form>
            </div>
        </Modal>
    );
}
