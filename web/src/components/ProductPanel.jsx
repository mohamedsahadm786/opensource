import { useEffect, useState } from 'react';
import { Package, RefreshCw, Save } from 'lucide-react';
import { useProduct } from '../hooks/useProduct.js';
import { useToast } from '../contexts/ToastContext.jsx';
import { productFormFromRow, buildProductPayload } from '../lib/productForm.js';
import { convertBriefs, generateQcBrief } from '../lib/briefs.js';
import { ProductFields } from './ProductFields.jsx';

// The single product per tenant — view + edit.
export function ProductPanel({ tenantId }) {
    const toast = useToast();
    const { product, photoUrl, status, error, reload, save } = useProduct(tenantId);
    const [form, setForm] = useState(productFormFromRow(null));
    const [photoFile, setPhotoFile] = useState(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => { if (status === 'ready') setForm(productFormFromRow(product)); }, [status, product]);

    const onField = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    async function handleSubmit(e) {
        e.preventDefault();
        const built = buildProductPayload(form);
        if (!built.ok) { toast.error(built.error); return; }
        const photoChanged = Boolean(photoFile);
        setSaving(true);
        try {
            await save(built.payload, photoFile);
            setPhotoFile(null);
            // Re-derive the structured product JSON (name/product_info/packaging) from the brief.
            await convertBriefs({ product_brief: built.payload.product_brief_text });
            // If the product photo was swapped, refresh the QC ground-truth brief
            // from the new photo. force=true because a brief already exists.
            // Best-effort: a failure must not fail the product save.
            if (photoChanged) {
                try {
                    await generateQcBrief({ force: true });
                } catch (qcErr) {
                    console.warn('[Alluvi] qc-brief refresh failed (non-fatal)', qcErr);
                }
            }
            await reload();
            toast.success('Product saved and re-analyzed.');
        } catch (err) {
            toast.error(err?.message || 'Could not save the product.');
        } finally {
            setSaving(false);
        }
    }

    return (
        <section className="panel">
            <header className="panel-head">
                <div>
                    <h2>Product</h2>
                    <p className="panel-sub">{product ? product.name : 'No product yet'} · one product per workspace</p>
                </div>
                <div className="filters">
                    <button type="button" className="btn btn-ghost btn-sm" onClick={reload}>
                        <RefreshCw /><span>Refresh</span>
                    </button>
                </div>
            </header>

            {status === 'loading' && (
                <div className="state state-loading"><div className="spinner" /><p>Loading product…</p></div>
            )}

            {status === 'error' && (
                <div className="state state-error">
                    <Package strokeWidth={1.5} />
                    <h3>Couldn't load product</h3>
                    <p>{error?.message || 'Please try again.'}</p>
                    <button type="button" className="btn btn-ghost" onClick={reload}>Try again</button>
                </div>
            )}

            {status === 'ready' && (
                <form className="setup-form" onSubmit={handleSubmit} style={{ padding: '20px 24px 24px' }}>
                    <div className="setup-card">
                        <div className="setup-card-head">
                            <Package />
                            <div><h3>Product details</h3><p>Used across image compositing, QC, and video scripts.</p></div>
                        </div>
                        <ProductFields
                            form={form}
                            onField={onField}
                            photoUrl={photoUrl}
                            photoFile={photoFile}
                            onPhoto={setPhotoFile}
                            onClearPhoto={() => setPhotoFile(null)}
                        />
                    </div>
                    <footer className="settings-foot">
                        <button type="submit" className="btn btn-primary" disabled={saving}>
                            <Save /><span>{saving ? 'Saving…' : 'Save product'}</span>
                        </button>
                    </footer>
                </form>
            )}
        </section>
    );
}
