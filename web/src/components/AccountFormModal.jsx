import { useEffect, useRef, useState } from 'react';
import { Mic, Upload, X } from 'lucide-react';
import { Modal } from './Modal.jsx';
import { supabase } from '../lib/supabase.js';
import {
    COUNTRY_SUGGESTIONS,
    GENDER_OPTIONS,
    LANGUAGE_SUGGESTIONS,
} from '../lib/constants.js';

const EMPTY = { tiktok_id: '', name: '', gender: '', age: '', country: '', language: '' };

export function AccountFormModal({ open, mode, initial, tenantId, onClose, onSubmit }) {
    const [form, setForm] = useState(EMPTY);
    const [identityFactors, setIdentityFactors] = useState('');
    const [voiceText, setVoiceText] = useState('');
    const [voiceFile, setVoiceFile] = useState(null);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [error, setError] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const firstFieldRef = useRef(null);
    const voiceRef = useRef(null);

    const isEdit = mode === 'edit';

    useEffect(() => {
        if (!open) return;
        setError(null);
        setSubmitting(false);
        setVoiceFile(null);
        if (isEdit && initial) {
            setForm({
                tiktok_id: initial.tiktok_id || '',
                name: initial.name || '',
                gender: initial.gender || '',
                age: initial.age ?? '',
                country: initial.country || '',
                language: initial.language || '',
            });
            const appearanceText = typeof initial.identity_factors === 'string'
                ? initial.identity_factors : (initial.identity_factors?.appearance || '');
            setIdentityFactors(appearanceText);
            setVoiceText(initial.voice_reference_text || '');
            setShowAdvanced(Boolean(appearanceText || initial.voice_reference_text));
        } else {
            setForm(EMPTY);
            setIdentityFactors('');
            setVoiceText('');
            setShowAdvanced(false);
        }
        const id = setTimeout(() => firstFieldRef.current?.focus(), 80);
        return () => clearTimeout(id);
    }, [open, isEdit, initial]);

    function update(key, value) {
        setForm(prev => ({ ...prev, [key]: value }));
    }

    // Upload an optional voice sample -> media_assets row -> id.
    async function uploadVoice() {
        const ext = (voiceFile.name.split('.').pop() || 'wav').toLowerCase();
        const path = `${tenantId}/audio/voice-${Date.now()}.${ext}`;
        const { error: upErr } = await supabase.storage
            .from('audio').upload(path, voiceFile, { contentType: voiceFile.type || 'audio/wav', upsert: true });
        if (upErr) throw upErr;
        const { data: asset, error: maErr } = await supabase
            .from('media_assets')
            .insert({ tenant_id: tenantId, kind: 'audio', bucket: 'audio', path, mime_type: voiceFile.type || 'audio/wav', bytes: voiceFile.size })
            .select('id').single();
        if (maErr) throw maErr;
        return asset.id;
    }

    async function handleSubmit(e) {
        e.preventDefault();
        setError(null);

        const base = {
            tiktok_id: form.tiktok_id.trim().replace(/^@+/, ''),
            name: form.name.trim(),
            gender: form.gender,
            age: parseInt(form.age, 10),
            country: form.country.trim(),
            language: form.language.trim(),
        };

        const missing = Object.entries(base).find(([, v]) => v === '' || v == null || Number.isNaN(v));
        if (missing) { setError(`Please fill in the ${missing[0].replace('_', ' ')} field.`); return; }
        if (base.age < 13 || base.age > 120) { setError('Age must be between 13 and 120.'); return; }

        setSubmitting(true);
        try {
            const appearance = identityFactors.trim();
            const payload = {
                ...base,
                identity_factors: appearance ? { appearance } : {},
                voice_reference_text: voiceText.trim() || null,
            };
            if (voiceFile) payload.voice_reference_asset_id = await uploadVoice();
            await onSubmit(payload);
        } catch (err) {
            setError(err.message || 'Could not save the account.');
        } finally {
            setSubmitting(false);
        }
    }

    return (
        <Modal open={open} onClose={onClose} labelledBy="form-modal-title">
            <div className="modal-card">
                <header className="modal-head">
                    <div>
                        <p className="modal-eyebrow">{isEdit ? `@${initial?.tiktok_id ?? ''}` : 'New account'}</p>
                        <h2 id="form-modal-title">{isEdit ? 'Edit account' : 'Onboard a TikTok account'}</h2>
                    </div>
                    <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X /></button>
                </header>

                <form className="modal-form" onSubmit={handleSubmit} noValidate>
                    <label className="field">
                        <span className="field-label">TikTok ID</span>
                        <div className="field-input">
                            <span className="field-prefix">@</span>
                            <input ref={firstFieldRef} type="text" placeholder="creator.handle" autoComplete="off"
                                value={form.tiktok_id} onChange={e => update('tiktok_id', e.target.value)} required />
                        </div>
                    </label>

                    <label className="field">
                        <span className="field-label">Name</span>
                        <div className="field-input">
                            <input type="text" placeholder="Display name" autoComplete="off"
                                value={form.name} onChange={e => update('name', e.target.value)} required />
                        </div>
                    </label>

                    <div className="field-row">
                        <label className="field">
                            <span className="field-label">Gender</span>
                            <div className="field-input is-select">
                                <select value={form.gender} onChange={e => update('gender', e.target.value)} required>
                                    <option value="" disabled>Select gender</option>
                                    {GENDER_OPTIONS.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
                                </select>
                            </div>
                        </label>
                        <label className="field">
                            <span className="field-label">Age</span>
                            <div className="field-input">
                                <input type="number" min="13" max="120" placeholder="24" inputMode="numeric"
                                    value={form.age} onChange={e => update('age', e.target.value)} required />
                            </div>
                        </label>
                    </div>

                    <div className="field-row">
                        <label className="field">
                            <span className="field-label">Country</span>
                            <div className="field-input">
                                <input type="text" list="country-options" placeholder="United States" autoComplete="off"
                                    value={form.country} onChange={e => update('country', e.target.value)} required />
                            </div>
                        </label>
                        <label className="field">
                            <span className="field-label">Language</span>
                            <div className="field-input">
                                <input type="text" list="language-options" placeholder="English" autoComplete="off"
                                    value={form.language} onChange={e => update('language', e.target.value)} required />
                            </div>
                        </label>
                    </div>

                    <datalist id="country-options">{COUNTRY_SUGGESTIONS.map(c => <option key={c} value={c} />)}</datalist>
                    <datalist id="language-options">{LANGUAGE_SUGGESTIONS.map(l => <option key={l} value={l} />)}</datalist>

                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowAdvanced(s => !s)}>
                        <Mic /><span>{showAdvanced ? 'Hide' : 'Add'} appearance factors &amp; voice (optional)</span>
                    </button>

                    {showAdvanced && (
                        <>
                            <label className="field">
                                <span className="field-label">Appearance / body type <span className="field-opt">(optional, plain English)</span></span>
                                <textarea className="field-textarea" rows={3}
                                    placeholder="Describe the look in plain English, e.g. 'heavier / fat build, round face, short curly hair, warm tan skin'. Leave blank to auto-generate from gender, age and country."
                                    value={identityFactors} onChange={e => setIdentityFactors(e.target.value)} />
                            </label>
                            <label className="field">
                                <span className="field-label">Voice reference transcript <span className="field-opt">(F5-TTS needs the sample text)</span></span>
                                <div className="field-input">
                                    <input type="text" placeholder="Exact words spoken in the voice sample"
                                        value={voiceText} onChange={e => setVoiceText(e.target.value)} />
                                </div>
                            </label>
                            <div className="field">
                                <span className="field-label">Voice sample <span className="field-opt">(optional)</span></span>
                                <button type="button" className="setup-drop" onClick={() => voiceRef.current?.click()}>
                                    <Upload /><span>{voiceFile ? voiceFile.name : 'Choose an audio file (wav/mp3)'}</span>
                                </button>
                                <input ref={voiceRef} type="file" accept="audio/*" hidden
                                    onChange={e => { const f = e.target.files?.[0]; if (f) setVoiceFile(f); e.target.value = ''; }} />
                            </div>
                        </>
                    )}

                    {error && <div className="auth-error">{error}</div>}

                    <footer className="modal-foot">
                        <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
                        <button type="submit" className="btn btn-primary" disabled={submitting}>
                            {submitting ? (isEdit ? 'Saving…' : 'Onboarding…') : (isEdit ? 'Save changes' : 'Save account')}
                        </button>
                    </footer>
                </form>
            </div>
        </Modal>
    );
}
