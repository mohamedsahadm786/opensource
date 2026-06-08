import { useRef } from 'react';
import { ImagePlus, Upload, X } from 'lucide-react';

// Pure field block for the product form. State is owned by the parent
// (TenantSetup / ProductPanel) which also handles submit via buildProductPayload.
export function ProductFields({ form, onField, photoUrl, photoFile, onPhoto, onClearPhoto }) {
    const fileRef = useRef(null);
    const set = (k) => (e) => onField(k, e.target.value);
    const previewUrl = photoFile ? URL.createObjectURL(photoFile) : photoUrl;

    return (
        <>
            <div className="field-row">
                <label className="field">
                    <span className="field-label">Product slug (product_key)</span>
                    <div className="field-input">
                        <input type="text" placeholder="tirzepatide_40mg" value={form.product_key} onChange={set('product_key')} />
                    </div>
                </label>
                <label className="field">
                    <span className="field-label">Product name</span>
                    <div className="field-input">
                        <input type="text" placeholder="Tirzepatide" value={form.name} onChange={set('name')} />
                    </div>
                </label>
            </div>

            <div className="field-row">
                <label className="field">
                    <span className="field-label">Compound</span>
                    <div className="field-input">
                        <input type="text" placeholder="Tirzepatide" value={form.peptide_compound} onChange={set('peptide_compound')} />
                    </div>
                </label>
                <label className="field">
                    <span className="field-label">Dose</span>
                    <div className="field-input">
                        <input type="text" placeholder="40mg" value={form.dose} onChange={set('dose')} />
                    </div>
                </label>
            </div>

            <div className="field-row">
                <label className="field">
                    <span className="field-label">Category</span>
                    <div className="field-input">
                        <input type="text" placeholder="weight_management" value={form.category} onChange={set('category')} />
                    </div>
                </label>
                <label className="field">
                    <span className="field-label">QC max retries</span>
                    <div className="field-input">
                        <input type="number" min="0" step="1" value={form.qc_max_retries} onChange={set('qc_max_retries')} />
                    </div>
                </label>
            </div>

            <label className="field">
                <span className="field-label">Mask prompt <strong>(required — Stage-3 box protect)</strong></span>
                <div className="field-input">
                    <input type="text" placeholder="white product box package, white rectangular box"
                        value={form.mask_prompt} onChange={set('mask_prompt')} />
                </div>
            </label>

            <label className="field">
                <span className="field-label">Packaging <span className="field-opt">(JSON)</span></span>
                <textarea className="field-textarea" rows={4}
                    placeholder='{ "type": "cardboard_box", "primary_colors": ["#FFFFFF"], "text_on_packaging": ["ALLUVI"] }'
                    value={form.packaging} onChange={set('packaging')} />
            </label>

            <div className="field-row">
                <label className="field">
                    <span className="field-label">Key benefits <span className="field-opt">(JSON array)</span></span>
                    <textarea className="field-textarea" rows={4} placeholder='["supports weight management goals"]'
                        value={form.key_benefits} onChange={set('key_benefits')} />
                </label>
                <label className="field">
                    <span className="field-label">Do-not-claim <span className="field-opt">(JSON array)</span></span>
                    <textarea className="field-textarea" rows={4} placeholder='["guaranteed results", "diabetes treatment claims"]'
                        value={form.do_not_claim} onChange={set('do_not_claim')} />
                </label>
            </div>

            <label className="field">
                <span className="field-label">Target audience <span className="field-opt">(JSON)</span></span>
                <textarea className="field-textarea" rows={3}
                    placeholder='{ "primary_demographic": "women 28-45 ..." }'
                    value={form.target_audience} onChange={set('target_audience')} />
            </label>

            <label className="field">
                <span className="field-label">Product knowledge for scripts (product_info) <span className="field-opt">(JSON)</span></span>
                <textarea className="field-textarea" rows={5}
                    placeholder='{ "product_name": "Tirzepatide", "wellness_associations": ["consistency"], "positive_lifestyle_language": ["feeling more focused"] }'
                    value={form.product_info} onChange={set('product_info')} />
            </label>

            <div className="field">
                <span className="field-label">Product photo</span>
                <button type="button" className="setup-drop" onClick={() => fileRef.current?.click()}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) onPhoto(f); }}>
                    <Upload />
                    <span>{photoFile ? photoFile.name : 'Click to choose the real product photo, or drop it here'}</span>
                </button>
                <input ref={fileRef} type="file" accept="image/*" hidden
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) onPhoto(f); e.target.value = ''; }} />
                {previewUrl && (
                    <ul className="setup-thumbs">
                        <li className="setup-thumb">
                            <img src={previewUrl} alt="Product reference" />
                            {photoFile && onClearPhoto && (
                                <button type="button" className="setup-thumb-x" onClick={onClearPhoto} aria-label="Remove">
                                    <X />
                                </button>
                            )}
                        </li>
                    </ul>
                )}
                {!previewUrl && <p className="ana-empty"><ImagePlus size={14} /> No product photo uploaded yet.</p>}
            </div>
        </>
    );
}
