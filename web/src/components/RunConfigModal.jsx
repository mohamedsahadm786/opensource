import { useEffect, useState } from 'react';
import { Clapperboard, Clock, Cpu, Hash, Sliders, Target, X } from 'lucide-react';
import { Modal } from './Modal.jsx';
import { useToast } from '../contexts/ToastContext.jsx';
import { CREATION_MODE_OPTIONS, VIDEO_MODE_OPTIONS } from '../lib/constants.js';

// Per-tenant run controls -> tenant_pipeline_config. The orchestrator reads this.
export function RunConfigModal({ open, config, accounts = [], onClose, onSave }) {
    const toast = useToast();
    const [form, setForm] = useState(config);
    const [error, setError] = useState(null);
    const [saving, setSaving] = useState(false);
    const [showAdvanced, setShowAdvanced] = useState(false);

    useEffect(() => { if (open) { setForm(config); setError(null); } }, [open, config]);

    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const isSilentfirst = form.video_mode === 'silentfirst';

    async function handleSubmit(e) {
        e.preventDefault();
        setError(null);
        if (Number(form.num_videos_per_account) < 1 || !form.num_videos_per_account)
            return setError('Set how many videos per account (at least 1).');
        if (form.creation_mode === 'specific' && !form.target_account_id)
            return setError('Choose the specific account to run.');

        setSaving(true);
        try {
            await onSave(form);
            toast.success('Run settings saved.');
            onClose();
        } catch (err) {
            setError(err.message || 'Could not save run settings.');
        } finally {
            setSaving(false);
        }
    }

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
                                <span className="field-label">Which accounts</span>
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
                            <label className="field">
                                <span className="field-label">Target account</span>
                                <div className="field-input is-select">
                                    <select value={form.target_account_id ?? ''} onChange={(e) => set('target_account_id', e.target.value || null)}>
                                        <option value="">Select an account…</option>
                                        {accounts.map((a) => <option key={a.id} value={a.id}>@{a.tiktok_id} · {a.name}</option>)}
                                    </select>
                                </div>
                            </label>
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
                                <span className="field-label">Seed <span className="field-opt">(optional)</span></span>
                                <div className="field-input">
                                    <Cpu />
                                    <input type="number" step="1" placeholder="random" value={form.seed ?? ''}
                                        onChange={(e) => set('seed', e.target.value)} />
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
                                    ['intro_seconds', 'Intro (s)'], ['outro_seconds', 'Outro (s)'],
                                    ['tail_seconds', 'Tail (s)'], ['lips_expression', 'Lips expression'],
                                    ['inference_steps', 'Inference steps'], ['punch_in', 'Punch-in'],
                                    ['threshold', 'Threshold'],
                                ].map(([k, label]) => (
                                    <label className="field" key={k} style={{ minWidth: 120, flex: '1 1 30%' }}>
                                        <span className="field-label">{label}{isSilentfirst ? '' : ''}</span>
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
