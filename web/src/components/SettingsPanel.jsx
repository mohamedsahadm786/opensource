import { useEffect, useState } from 'react';
import { CheckCircle2, Cpu, Eye, EyeOff, KeyRound, RefreshCw, Save, ShieldAlert } from 'lucide-react';
import { useSettings } from '../hooks/useSettings.js';
import { useToast } from '../contexts/ToastContext.jsx';

export function SettingsPanel({ tenantId }) {
    const toast = useToast();
    const { gpu, hasAnthropicKey, status, error, reload, saveGpu, saveAnthropicKey } = useSettings(tenantId);

    const [gpuForm, setGpuForm] = useState(gpu);
    const [anthropicKey, setAnthropicKey] = useState('');
    const [reveal, setReveal] = useState(false);
    const [savingKey, setSavingKey] = useState(false);
    const [savingGpu, setSavingGpu] = useState(false);

    useEffect(() => { if (status === 'ready') setGpuForm(gpu); }, [status, gpu]);

    const setG = (k) => (e) => setGpuForm((f) => ({ ...f, [k]: e.target.value }));

    async function submitKey(e) {
        e.preventDefault();
        if (!anthropicKey.trim()) { toast.info('Enter a key first.'); return; }
        setSavingKey(true);
        try {
            await saveAnthropicKey(anthropicKey);
            setAnthropicKey('');
            toast.success('Anthropic key stored in Vault.');
        } catch (err) {
            toast.error(err?.message || 'Could not store the key.');
        } finally {
            setSavingKey(false);
        }
    }

    async function submitGpu(e) {
        e.preventDefault();
        setSavingGpu(true);
        try {
            await saveGpu(gpuForm);
            toast.success('GPU connection saved.');
        } catch (err) {
            toast.error(err?.message || 'Could not save GPU settings.');
        } finally {
            setSavingGpu(false);
        }
    }

    return (
        <section className="panel">
            <header className="panel-head">
                <div>
                    <h2>Settings</h2>
                    <p className="panel-sub">Anthropic key (Vault) and GPU connection.</p>
                </div>
                <div className="filters">
                    <button type="button" className="btn btn-ghost btn-sm" onClick={reload}>
                        <RefreshCw /><span>Refresh</span>
                    </button>
                </div>
            </header>

            {status === 'loading' && (
                <div className="state state-loading"><div className="spinner" /><p>Loading settings…</p></div>
            )}

            {status === 'error' && (
                <div className="state state-error">
                    <ShieldAlert strokeWidth={1.5} />
                    <h3>Couldn't load settings</h3>
                    <p>{error?.raw?.message || 'Please try again.'}</p>
                    <button type="button" className="btn btn-ghost" onClick={reload}>Try again</button>
                </div>
            )}

            {status === 'ready' && (
                <div className="settings-form">
                    {/* Anthropic key — write only */}
                    <form className="setup-card" onSubmit={submitKey}>
                        <div className="setup-card-head">
                            <KeyRound />
                            <div><h3>Anthropic API key</h3><p>Stored encrypted in Supabase Vault. The key is never read back into the browser.</p></div>
                        </div>
                        {hasAnthropicKey && (
                            <div className="settings-warn" style={{ borderColor: 'var(--success)', background: 'var(--success-soft)' }}>
                                <CheckCircle2 />
                                <p>A key is set for this workspace. Submitting a new one rotates it.</p>
                            </div>
                        )}
                        <label className="field">
                            <span className="field-label">{hasAnthropicKey ? 'Replace key' : 'Set key'}</span>
                            <div className="field-input">
                                <KeyRound />
                                <input type={reveal ? 'text' : 'password'} placeholder="sk-ant-…" autoComplete="off" spellCheck="false"
                                    value={anthropicKey} onChange={(e) => setAnthropicKey(e.target.value)} />
                                <button type="button" className="reveal" onClick={() => setReveal((r) => !r)} aria-label="Toggle">
                                    {reveal ? <EyeOff /> : <Eye />}
                                </button>
                            </div>
                        </label>
                        <footer className="settings-foot">
                            <button type="submit" className="btn btn-primary" disabled={savingKey}>
                                <Save /><span>{savingKey ? 'Storing…' : 'Store key'}</span>
                            </button>
                        </footer>
                    </form>

                    {/* GPU connection */}
                    <form className="setup-card" onSubmit={submitGpu}>
                        <div className="setup-card-head">
                            <Cpu />
                            <div><h3>GPU connection</h3><p>The RunPod pod-id changes on restart — update gpu_host here, ports stay on the services.</p></div>
                        </div>
                        <div className="field-row">
                            <label className="field">
                                <span className="field-label">Provider</span>
                                <div className="field-input is-select">
                                    <select value={gpuForm.gpu_provider} onChange={setG('gpu_provider')}>
                                        <option value="runpod">RunPod</option>
                                        <option value="own_gpu">Own GPU</option>
                                    </select>
                                </div>
                            </label>
                            <label className="field">
                                <span className="field-label">GPU host (pod-id)</span>
                                <div className="field-input"><input type="text" placeholder="abc123pod" value={gpuForm.gpu_host} onChange={setG('gpu_host')} /></div>
                            </label>
                        </div>
                        <label className="field">
                            <span className="field-label">URL template</span>
                            <div className="field-input">
                                <input type="text" placeholder="https://{host}-{port}.proxy.runpod.net"
                                    value={gpuForm.gpu_url_template} onChange={setG('gpu_url_template')} />
                            </div>
                        </label>
                        <footer className="settings-foot">
                            <button type="submit" className="btn btn-primary" disabled={savingGpu}>
                                <Save /><span>{savingGpu ? 'Saving…' : 'Save GPU connection'}</span>
                            </button>
                        </footer>
                    </form>
                </div>
            )}
        </section>
    );
}
