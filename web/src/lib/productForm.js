import { parseJsonText, stringifyJson } from './jsonField.js';

// Shared shape + build/validate for the product form (used by TenantSetup and
// ProductPanel). The jsonb fields are held as raw text and parsed on submit.

export function emptyProductForm() {
    return {
        product_key: '', name: '', peptide_compound: '', dose: '', category: '',
        mask_prompt: 'white product box package, white rectangular box',
        qc_max_retries: 3,
        packaging: '', key_benefits: '', target_audience: '', do_not_claim: '', product_info: '',
    };
}

export function productFormFromRow(row) {
    if (!row) return emptyProductForm();
    return {
        product_key: row.product_key || '',
        name: row.name || '',
        peptide_compound: row.peptide_compound || '',
        dose: row.dose || '',
        category: row.category || '',
        mask_prompt: row.mask_prompt || 'white product box package, white rectangular box',
        qc_max_retries: row.qc_max_retries ?? 3,
        packaging: stringifyJson(row.packaging),
        key_benefits: stringifyJson(row.key_benefits),
        target_audience: stringifyJson(row.target_audience),
        do_not_claim: stringifyJson(row.do_not_claim),
        product_info: stringifyJson(row.product_info),
    };
}

// Returns { ok, payload?, error? }.
export function buildProductPayload(form) {
    if (!form.name?.trim()) return { ok: false, error: 'Product name is required.' };
    if (!form.product_key?.trim()) return { ok: false, error: 'Product slug (product_key) is required.' };
    if (!form.mask_prompt?.trim()) return { ok: false, error: 'Mask prompt is required (Stage-3 box protect).' };

    const fields = [
        ['packaging', {}], ['key_benefits', []], ['target_audience', {}],
        ['do_not_claim', []], ['product_info', {}],
    ];
    const parsed = {};
    for (const [key, fallback] of fields) {
        const r = parseJsonText(form[key], fallback);
        if (!r.ok) return { ok: false, error: `"${key}" is not valid JSON: ${r.error}` };
        parsed[key] = r.value;
    }

    return {
        ok: true,
        payload: {
            product_key: form.product_key.trim(),
            name: form.name.trim(),
            peptide_compound: form.peptide_compound?.trim() || null,
            dose: form.dose?.trim() || null,
            category: form.category?.trim() || null,
            mask_prompt: form.mask_prompt.trim(),
            qc_max_retries: Number(form.qc_max_retries) || 3,
            ...parsed,
        },
    };
}
