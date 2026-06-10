import { useState } from 'react';
import { Building2, Cpu, KeyRound, Package, Sparkles } from 'lucide-react';
import { useToast } from '../contexts/ToastContext.jsx';
import { useProduct } from '../hooks/useProduct.js';
import { convertBriefs, generateQcBrief } from '../lib/briefs.js';
import { emptyProductForm, buildProductPayload } from '../lib/productForm.js';
import { ProductFields } from './ProductFields.jsx';

// v2 first-run setup: 7 plain-English fields. No JSON, no company/slug/plan.
// On Save we store raw briefs + the Vault key + the product photo, then run the
// server-side convert-briefs Claude pass to derive the JSON the pipeline reads,
// then flip onboarded. The Save button is gated until every field is filled.
export function TenantSetup({ user, tenantId, saveTenantConfig, storeAnthropicKey, markOnboarded, onDone }) {
    const toast = useToast();
    const { save: saveProduct } = useProduct(tenantId);

    const [gpuHost, setGpuHost] = useState('');
    const [anthropicKey, setAnthropicKey] = useState('');
    const [companyBrief, setCompanyBrief] = useState('');
    const [scriptBrief, setScriptBrief] = useState('');
    const [product, setProduct] = useState(emptyProductForm);
    const [photoFile, setPhotoFile] = useState(null);
    const [anglePhotoFile, setAnglePhotoFile] = useState(null);   // optional 3/4 view — NOT in canSave

    const [saving, setSaving] = useState(false);
    const [phase, setPhase] = useState('');     // status text while saving
    const setProductField = (k, v) => setProduct((p) => ({ ...p, [k]: v }));

    const canSave =
        gpuHost.trim() && anthropicKey.trim() && companyBrief.trim() && scriptBrief.trim()
        && product.product_brief_text.trim() && product.mask_prompt.trim() && photoFile;

    async function handleSubmit(e) {
        e.preventDefault();
        if (!canSave) { toast.info('Fill in every field (and upload the product photo) to finish setup.'); return; }
        const built = buildProductPayload(product);
        if (!built.ok) { toast.error(built.error); return; }

        setSaving(true);
        try {
            setPhase('Storing your Anthropic key…');
            await storeAnthropicKey(anthropicKey);

            setPhase('Saving your briefs…');
            await saveTenantConfig({ gpu_host: gpuHost, company_brief_text: companyBrief, script_brief_text: scriptBrief });
            await saveProduct(built.payload, photoFile, anglePhotoFile);

            setPhase('Converting your briefs with Claude…');
            await convertBriefs({
                product_brief: product.product_brief_text,
                company_brief: companyBrief,
                script_brief: scriptBrief,
            });

            // QC ground-truth brief from the product photo. Best-effort: a failure
            // here must NOT block onboarding (the pipeline still runs without it,
            // QC just lacks product-specific ground truth — re-run from Product page).
            setPhase('Analyzing your product photo…');
            try {
                await generateQcBrief();
            } catch (qcErr) {
                console.warn('[Alluvi] qc-brief generation failed (non-fatal)', qcErr);
            }

            setPhase('Finishing…');
            await markOnboarded();
            toast.success('Setup complete — welcome aboard!');
            onDone?.();
        } catch (err) {
            console.error('[Alluvi] tenant setup failed', err);
            toast.error(err?.message || 'Could not save your setup.');
            setSaving(false);
            setPhase('');
        }
    }

    return (
        <section className="panel setup-panel">
            <header className="panel-head">
                <div>
                    <h2>Let’s set up your workspace</h2>
                    <p className="panel-sub">
                        Welcome{user?.name ? `, ${user.name}` : ''} — write everything in plain English. We convert it into the
                        pipeline’s structured data for you (no JSON). Every field is required.
                    </p>
                </div>
            </header>

            <form className="setup-form" onSubmit={handleSubmit}>
                {/* GPU + key */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <Cpu />
                        <div><h3>Connection</h3><p>The GPU pod-id (from your ops channel) and your Anthropic key.</p></div>
                    </div>
                    <label className="field">
                        <span className="field-label">GPU host (RunPod pod-id)</span>
                        <div className="field-input">
                            <Cpu />
                            <input type="text" placeholder="paste the latest pod-id from your ops channel"
                                value={gpuHost} onChange={(e) => setGpuHost(e.target.value)} />
                        </div>
                    </label>
                    <label className="field">
                        <span className="field-label">Anthropic Claude API key <span className="field-opt">(stored in Vault)</span></span>
                        <div className="field-input">
                            <KeyRound />
                            <input type="password" placeholder="sk-ant-…" autoComplete="off" spellCheck="false"
                                value={anthropicKey} onChange={(e) => setAnthropicKey(e.target.value)} />
                        </div>
                    </label>
                </div>

                {/* Company brief */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <Building2 />
                        <div><h3>Company / brand brief</h3><p>Everything about the brand — voice, positioning, audience, style.</p></div>
                    </div>
                    <label className="field">
                        <span className="field-label">Company brief <span className="field-opt">(plain English)</span></span>
                        <textarea className="field-textarea" rows={8} value={companyBrief} onChange={(e) => setCompanyBrief(e.target.value)}
                            placeholder="Describe the brand: who you are, how you sound, your positioning, your audience, the visual/cinematic style, the moods and camera motion you like, the hooks and phrases that work for you…" />
                    </label>
                </div>

                {/* Script brief */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <Sparkles />
                        <div><h3>Script / dialogue brief</h3><p>How the spoken dialogue should sound — tone, what to say, what to avoid.</p></div>
                    </div>
                    <label className="field">
                        <span className="field-label">Script brief <span className="field-opt">(plain English)</span></span>
                        <textarea className="field-textarea" rows={6} value={scriptBrief} onChange={(e) => setScriptBrief(e.target.value)}
                            placeholder="How should the on-camera dialogue feel? Tone and style, things it MUST do, example lines you love, and what it must NEVER say or do (claims to avoid, artifacts to avoid)…" />
                    </label>
                </div>

                {/* Product */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <Package />
                        <div><h3>Product</h3><p>One product per workspace. Describe it in plain English; upload the real photo.</p></div>
                    </div>
                    <ProductFields
                        form={product}
                        onField={setProductField}
                        photoFile={photoFile}
                        onPhoto={setPhotoFile}
                        onClearPhoto={() => setPhotoFile(null)}
                        anglePhotoFile={anglePhotoFile}
                        onAnglePhoto={setAnglePhotoFile}
                        onClearAnglePhoto={() => setAnglePhotoFile(null)}
                    />
                </div>

                <footer className="setup-foot">
                    {saving && phase && <span className="panel-sub" style={{ alignSelf: 'center' }}>{phase}</span>}
                    <button type="submit" className="btn btn-primary" disabled={saving || !canSave}>
                        <Sparkles />
                        <span>{saving ? 'Setting up…' : 'Finish setup'}</span>
                    </button>
                </footer>
            </form>
        </section>
    );
}
