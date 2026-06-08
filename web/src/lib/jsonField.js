// Helpers for the "freeform jsonb" form fields (brand_config, script_*,
// packaging, product_info, etc.). The form holds raw text; we parse on submit.

export function parseJsonText(text, fallback) {
    const t = (text || '').trim();
    if (!t) return { ok: true, value: fallback };
    try { return { ok: true, value: JSON.parse(t) }; }
    catch (e) { return { ok: false, error: e.message }; }
}

export function stringifyJson(value) {
    if (value == null) return '';
    try { return JSON.stringify(value, null, 2); } catch { return ''; }
}
