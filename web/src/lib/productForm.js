// Shared shape + validate for the product form (v2: plain-English brief, not
// JSON). The structured product_info / packaging / name are derived server-side
// by convert-briefs — the user never types JSON here.

export function emptyProductForm() {
    return {
        product_brief_text: '',
        mask_prompt: 'white product box package, white rectangular box',
        qc_max_retries: 3,
    };
}

export function productFormFromRow(row) {
    if (!row) return emptyProductForm();
    return {
        product_brief_text: row.product_brief_text || '',
        mask_prompt: row.mask_prompt || 'white product box package, white rectangular box',
        qc_max_retries: row.qc_max_retries ?? 3,
    };
}

// Returns { ok, payload?, error? }.
export function buildProductPayload(form) {
    if (!form.product_brief_text?.trim()) return { ok: false, error: 'Product brief is required.' };
    if (!form.mask_prompt?.trim()) return { ok: false, error: 'Mask prompt is required (Stage-3 box protect).' };
    return {
        ok: true,
        payload: {
            product_brief_text: form.product_brief_text.trim(),
            mask_prompt: form.mask_prompt.trim(),
            qc_max_retries: Number(form.qc_max_retries) || 3,
        },
    };
}
