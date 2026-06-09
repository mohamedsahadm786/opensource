import { useRef } from 'react';
import { ImagePlus, Info, Upload, X } from 'lucide-react';

// Plain-English product fields (v2). The structured product_info / packaging /
// name are derived server-side by convert-briefs — the user never types JSON.
// State is owned by the parent (TenantSetup / ProductPanel).
export function ProductFields({ form, onField, photoUrl, photoFile, onPhoto, onClearPhoto, showBrief = true }) {
    const fileRef = useRef(null);
    const previewUrl = photoFile ? URL.createObjectURL(photoFile) : photoUrl;

    return (
        <>
            {showBrief && (
                <label className="field">
                    <span className="field-label">Product brief <span className="field-opt">(plain English — everything about the product)</span></span>
                    <textarea className="field-textarea" rows={8}
                        placeholder="Describe the product in your own words: what it is, the compound/dose, what the box looks like (colours, the exact text printed on it, shape), key benefits, who it's for, and anything NOT to claim. We'll turn this into the structured data the pipeline needs — you don't type any JSON."
                        value={form.product_brief_text} onChange={(e) => onField('product_brief_text', e.target.value)} />
                </label>
            )}

            <label className="field">
                <span className="field-label">Mask prompt <strong>(required — Stage-3 box protect)</strong></span>
                <div className="field-input">
                    <input type="text" placeholder="white product box package, white rectangular box"
                        value={form.mask_prompt} onChange={(e) => onField('mask_prompt', e.target.value)} />
                </div>
                <p className="ana-empty" style={{ marginTop: 4 }}>
                    <Info size={13} style={{ verticalAlign: '-2px' }} /> A short phrase that describes JUST the product box so the
                    realism pass protects it. Include the dominant colour + shape. Example: <em>“white product box package, white rectangular box”</em>.
                </p>
            </label>

            <label className="field" style={{ maxWidth: 220 }}>
                <span className="field-label">QC retry loop count</span>
                <div className="field-input">
                    <input type="number" min="0" step="1" value={form.qc_max_retries} onChange={(e) => onField('qc_max_retries', e.target.value)} />
                </div>
                <p className="ana-empty" style={{ marginTop: 4 }}>Total attempts = 1 + this.</p>
            </label>

            <div className="field">
                <span className="field-label">Product photo <strong>(the real box Qwen composites)</strong></span>
                <button type="button" className="setup-drop" onClick={() => fileRef.current?.click()}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) onPhoto(f); }}>
                    <Upload />
                    <span>{photoFile ? photoFile.name : 'Click to choose the real product photo, or drop it here'}</span>
                </button>
                <input ref={fileRef} type="file" accept="image/*" hidden
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) onPhoto(f); e.target.value = ''; }} />
                {previewUrl ? (
                    <ul className="setup-thumbs">
                        <li className="setup-thumb">
                            <img src={previewUrl} alt="Product reference" />
                            {photoFile && onClearPhoto && (
                                <button type="button" className="setup-thumb-x" onClick={onClearPhoto} aria-label="Remove"><X /></button>
                            )}
                        </li>
                    </ul>
                ) : (
                    <p className="ana-empty"><ImagePlus size={14} /> No product photo uploaded yet.</p>
                )}
            </div>
        </>
    );
}
