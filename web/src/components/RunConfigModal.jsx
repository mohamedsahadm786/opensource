import { useEffect, useState } from 'react';
import { Clapperboard, Clock, Cpu, Hash, RotateCcw, Sliders, Target, X } from 'lucide-react';
import { Modal } from './Modal.jsx';
import { supabase } from '../lib/supabase.js';
import { useToast } from '../contexts/ToastContext.jsx';
import { CREATION_MODE_OPTIONS, VIDEO_MODE_OPTIONS } from '../lib/constants.js';

// Per-tenant run controls -> tenant_pipeline_config, plus the QC retry count which
// lives on the product (v2). Run stays disabled until the config is valid.
export function RunConfigModal({ open, config, accounts = [], tenantId, onClose, onSave }) {
    const toast = useToast();
    const [form, setForm] = useState(config);
    const [qcRetries, setQcRetries] = useState(3);
    const [error, setError] = useState(null);
    const [saving, setSaving] = useState(false);
    const [showAdvanced, setShowAdvanced] = useState(false);

    useEffect(() => {
        if (!open) return;
        setForm({ ...config, target_account_ids: Array.isArray(config.target_account_ids) ? config.target_account_ids : [] });
        setError(null);
        // load the product's QC retry count
        if (tenantId) {
            supabase.from('products').select('qc_max_retries').eq('tenant_id', tenantId).maybeSingle()
                .then(({ data }) => setQcRetries(data?.qc_max_retries ?? 3));
        }
    }, [open, config, tenantId]);

    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const toggleAccount = (id) => setForm((f) => {
        const cur = Array.isArray(f.target_account_ids) ? f.target_account_ids : [];
        return { ...f, target_account_ids: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] };
    });

    async function handleSubmit(e) {
        e.preventDefault();
        setError(null);
        if (Number(form.num_videos_per_account) < 1 || !form.num_videos_per_account)
            return setError('Set how many videos per account (at least 1).');
        if (Number(form.video_duration_seconds) < 1 || !form.video_duration_seconds)
            return setError('Set the video duration in seconds.');
        if (form.creation_mode === 'specific' && (form.target_account_ids || []).length === 0)
            return setError('Pick at least one account to run.');

        setSaving(true);
        try {
            await onSave(form);
            // QC retry count lives on the product.
            if (tenantId) {
                await supabase.from('products')
                    .update({ qc_max_retries: Number(qcRetries) || 3, updated_at: new Date().toISOString() })
                    .eq('tenant_id', tenantId);
            }
            toast.success('Run settings saved.');
            onClose();
        } catch (err) {
            setError(err.message || 'Could not save run settings.');
        } finally {
            setSaving(false);
        }
    }

    const selected = Array.isArray(form.target_account_ids) ? form.target_account_ids : [];

    return (
        <Modal open={open} onClose={onClose} labelledBy="run-config-title">
            <div className="modal-card">
                <div className="modal-head">
                    <div>
                        <p className="modal-eyebrow">Pipeline</p>
                        <h2 id="run-config-title">Run settings</h2>
                    </div>
                    <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X /></button>
                </div>

                <form className="modal-form" onSubmit={handleSubmit}>
                    <div className="modal-body">
                        <div className="field-row">
                            <label className="field">
                                <span className="field-label">Production type</span>
                                <div className="field-input is-select">
                                    <Target />
                                    <select value={form.creation_mode} onChange={(e) => set('creation_mode', e.target.value)}>
                                        {CREATION_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                    </select>
                                </div>
                            </label>
                            <label className="field">
                                <span className="field-label">Videos per account</span>
                                <div className="field-input">
                                    <Hash />
                                    <input type="number" min="1" step="1" value={form.num_videos_per_account ?? ''}
                                        onChange={(e) => set('num_videos_per_account', e.target.value)} />
                                </div>
                            </label>
                        </div>

                        {form.creation_mode === 'specific' && (
                            <div className="field">
                                <span className="field-label">Accounts to run <span className="field-opt">({selected.length} selected)</span></span>
                                <div style={{ maxHeight: 180, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 10, padding: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    {accounts.length === 0 && <p className="ana-empty">No accounts yet — onboard some first.</p>}
                                    {accounts.map((a) => (
                                        <label key={a.id} className="toggle-row" style={{ margin: 0 }}>
                                            <input type="checkbox" checked={selected.includes(a.id)} onChange={() => toggleAccount(a.id)} />
                                            <span>@{a.tiktok_id} · {a.name}</span>
                                        </label>
                                    ))}
                                </div>
                            </div>
                        )}

                        <div className="field-row">
                            <label className="field">
                                <span className="field-label">Video mode</span>
                                <div className="field-input is-select">
                                    <Clapperboard />
                                    <select value={form.video_mode} onChange={(e) => set('video_mode', e.target.value)}>
                                        {VIDEO_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                                    </select>
                                </div>
                            </label>
                            <label className="field">
                                <span className="field-label">Video duration (s)</span>
                                <div className="field-input">
                                    <Clock />
                                    <input type="number" min="1" step="1" value={form.video_duration_seconds ?? ''}
                                        onChange={(e) => set('video_duration_seconds', e.target.value)} />
                                </div>
                            </label>
                        </div>

                        <div className="field-row">
                            <label className="field">
                                <span className="field-label">Per-shot length (s)</span>
                                <div className="field-input">
                                    <Clock />
                                    <input type="number" min="1" step="1" value={form.shot_seconds ?? ''}
                                        onChange={(e) => set('shot_seconds', e.target.value)} />
                                </div>
                            </label>
                            <label className="field">
                                <span className="field-label">QC retry loop count <span className="field-opt">(on product)</span></span>
                                <div className="field-input">
                                    <RotateCcw />
                                    <input type="number" min="0" step="1" value={qcRetries}
                                        onChange={(e) => setQcRetries(e.target.value)} />
                                </div>
                            </label>
                        </div>

                        <div className="toggle-row-group">
                            <label className="toggle-row">
                                <input type="checkbox" checked={Boolean(form.step_3_enabled)} onChange={(e) => set('step_3_enabled', e.target.checked)} />
                                <span>Stage 3 realism pass</span>
                            </label>
                            <label className="toggle-row">
                                <input type="checkbox" checked={Boolean(form.qc_enabled)} onChange={(e) => set('qc_enabled', e.target.checked)} />
                                <span>QC validation + retry</span>
                            </label>
                        </div>

                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowAdvanced((s) => !s)}>
                            <Sliders /><span>{showAdvanced ? 'Hide' : 'Show'} advanced (silent-first knobs)</span>
                        </button>
                        {showAdvanced && (
                            <div className="field-row" style={{ flexWrap: 'wrap' }}>
                                {[
                                    ['intro_seconds', 'Intro (s)'], ['outro_seconds', 'Outro (s)'], ['tail_seconds', 'Tail (s)'],
                                    ['lips_expression', 'Lips expression'], ['inference_steps', 'Inference steps'],
                                    ['punch_in', 'Punch-in'], ['threshold', 'Threshold'], ['seed', 'Seed'],
                                ].map(([k, label]) => (
                                    <label className="field" key={k} style={{ minWidth: 120, flex: '1 1 30%' }}>
                                        <span className="field-label">{label}</span>
                                        <div className="field-input">
                                            <input type="number" step="any" value={form[k] ?? ''} onChange={(e) => set(k, e.target.value)} />
                                        </div>
                                    </label>
                                ))}
                            </div>
                        )}

                        {error && <div className="auth-error">{error}</div>}
                    </div>

                    <div className="modal-foot">
                        <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={saving}>
                            {saving ? 'Saving…' : 'Save settings'}
                        </button>
                    </div>
                </form>
            </div>
        </Modal>
    );
}
