import { useState } from 'react';
import { Building2, Cpu, KeyRound, Package, Sparkles } from 'lucide-react';
import { useToast } from '../contexts/ToastContext.jsx';
import { useProduct } from '../hooks/useProduct.js';
import { parseJsonText } from '../lib/jsonField.js';
import { emptyProductForm, buildProductPayload } from '../lib/productForm.js';
import { PLAN_OPTIONS } from '../lib/constants.js';
import { ProductFields } from './ProductFields.jsx';

// First-run setup for a freshly provisioned tenant. Collects everything the
// pipeline reads: company/brand identity, script knowledge (the jsonb blobs),
// the Anthropic key (-> Vault), and the ONE product (+ photo). Finishing flips
// tenants.settings.onboarded = true.
export function TenantSetup({ user, tenantId, profile, saveTenantConfig, storeAnthropicKey, markOnboarded, onDone }) {
    const toast = useToast();
    const { save: saveProduct } = useProduct(tenantId);

    const [company, setCompany] = useState({
        name: profile?.name || user?.name || '',
        slug: profile?.slug || '',
        email: profile?.email || user?.email || '',
        plan: profile?.plan || 'free',
    });
    const [brandConfig, setBrandConfig] = useState('');
    const [scriptCompanyInfo, setScriptCompanyInfo] = useState('');
    const [scriptDirectives, setScriptDirectives] = useState('');
    const [anthropicKey, setAnthropicKey] = useState('');

    const [product, setProduct] = useState(emptyProductForm);
    const [photoFile, setPhotoFile] = useState(null);

    const [saving, setSaving] = useState(false);
    const setCo = (k) => (e) => setCompany((c) => ({ ...c, [k]: e.target.value }));
    const setProductField = (k, v) => setProduct((p) => ({ ...p, [k]: v }));

    async function handleSubmit(e) {
        e.preventDefault();
        if (!company.name.trim()) { toast.error('Company name is required.'); return; }

        // Parse the brand/script jsonb blobs.
        const blobs = {};
        for (const [key, raw] of [
            ['brand_config', brandConfig],
            ['script_company_info', scriptCompanyInfo],
            ['script_directives', scriptDirectives],
        ]) {
            const r = parseJsonText(raw, {});
            if (!r.ok) { toast.error(`"${key}" is not valid JSON: ${r.error}`); return; }
            blobs[key] = r.value;
        }

        const built = buildProductPayload(product);
        if (!built.ok) { toast.error(built.error); return; }

        setSaving(true);
        try {
            await saveTenantConfig({
                name: company.name, slug: company.slug, email: company.email, plan: company.plan,
                ...blobs,
            });
            if (anthropicKey.trim()) await storeAnthropicKey(anthropicKey);
            await saveProduct(built.payload, photoFile);
            await markOnboarded();
            toast.success('Setup complete — welcome aboard!');
            onDone?.();
        } catch (err) {
            console.error('[Alluvi] tenant setup failed', err);
            toast.error(err?.message || 'Could not save your setup.');
            setSaving(false);
        }
    }

    return (
        <section className="panel setup-panel">
            <header className="panel-head">
                <div>
                    <h2>Let’s set up your workspace</h2>
                    <p className="panel-sub">
                        Welcome{user?.name ? `, ${user.name}` : ''} — this configures the generation pipeline for your brand.
                    </p>
                </div>
            </header>

            <form className="setup-form" onSubmit={handleSubmit}>
                {/* Company / brand */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <Building2 />
                        <div><h3>Company &amp; brand</h3><p>Your company identity and brand creative config.</p></div>
                    </div>
                    <div className="field-row">
                        <label className="field">
                            <span className="field-label">Company name</span>
                            <div className="field-input"><input type="text" value={company.name} onChange={setCo('name')} placeholder="Alluvi" /></div>
                        </label>
                        <label className="field">
                            <span className="field-label">URL slug</span>
                            <div className="field-input"><input type="text" value={company.slug} onChange={setCo('slug')} placeholder="alluvi" /></div>
                        </label>
                    </div>
                    <div className="field-row">
                        <label className="field">
                            <span className="field-label">Billing email</span>
                            <div className="field-input"><input type="email" value={company.email} onChange={setCo('email')} placeholder="you@example.com" /></div>
                        </label>
                        <label className="field">
                            <span className="field-label">Plan</span>
                            <div className="field-input is-select">
                                <select value={company.plan} onChange={setCo('plan')}>
                                    {PLAN_OPTIONS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                                </select>
                            </div>
                        </label>
                    </div>
                    <label className="field">
                        <span className="field-label">Brand config <span className="field-opt">(JSON — voice, vocabulary, visual identity; mirrors brand.yaml)</span></span>
                        <textarea className="field-textarea" rows={6} value={brandConfig} onChange={(e) => setBrandConfig(e.target.value)}
                            placeholder='{ "voice": { "archetype": "the wellness-confident insider" }, "vocabulary": { "forbidden_words": ["miracle"] } }' />
                    </label>
                </div>

                {/* Script knowledge */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <Sparkles />
                        <div><h3>Script generation knowledge</h3><p>What the dialogue/scene generator reads (mirrors alluvi_information.json).</p></div>
                    </div>
                    <label className="field">
                        <span className="field-label">script_company_info <span className="field-opt">(JSON)</span></span>
                        <textarea className="field-textarea" rows={6} value={scriptCompanyInfo} onChange={(e) => setScriptCompanyInfo(e.target.value)}
                            placeholder='{ "system_identity": {...}, "brand_personality": {...}, "marketing_language_engine": {...}, "video_generation_preferences": {...}, "scene_generation_system": {...} }' />
                    </label>
                    <label className="field">
                        <span className="field-label">script_directives <span className="field-opt">(JSON)</span></span>
                        <textarea className="field-textarea" rows={5} value={scriptDirectives} onChange={(e) => setScriptDirectives(e.target.value)}
                            placeholder='{ "dialogue_generation_rules": {...}, "ai_generation_priorities": {...} }' />
                    </label>
                </div>

                {/* Anthropic key */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <KeyRound />
                        <div><h3>Anthropic API key</h3><p>Stored encrypted in Supabase Vault — never saved in a normal column.</p></div>
                    </div>
                    <label className="field">
                        <span className="field-label">Anthropic Claude API key</span>
                        <div className="field-input">
                            <KeyRound />
                            <input type="password" placeholder="sk-ant-…" autoComplete="off" spellCheck="false"
                                value={anthropicKey} onChange={(e) => setAnthropicKey(e.target.value)} />
                        </div>
                    </label>
                </div>

                {/* Product */}
                <div className="setup-card">
                    <div className="setup-card-head">
                        <Package />
                        <div><h3>Product</h3><p>One product per workspace. The mask prompt is required.</p></div>
                    </div>
                    <ProductFields
                        form={product}
                        onField={setProductField}
                        photoFile={photoFile}
                        onPhoto={setPhotoFile}
                        onClearPhoto={() => setPhotoFile(null)}
                    />
                </div>

                <footer className="setup-foot">
                    <button type="submit" className="btn btn-primary" disabled={saving}>
                        <Sparkles />
                        <span>{saving ? 'Setting up…' : 'Finish setup'}</span>
                    </button>
                </footer>
            </form>
        </section>
    );
}
