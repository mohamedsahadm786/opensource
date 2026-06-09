import { supabase } from './supabase.js';

// Calls the server-side convert-briefs Edge Function, which uses the tenant's
// Anthropic key (Vault) to turn plain-English briefs into the JSON the pipeline
// reads. Pass any subset of { product_brief, company_brief, script_brief };
// omitted briefs are read from the DB by the function.
export async function convertBriefs(briefs) {
    const { data, error } = await supabase.functions.invoke('convert-briefs', {
        method: 'POST', body: briefs || {},
    });
    if (error) throw error;
    if (!data?.ok) throw new Error(data?.message || data?.error || 'Brief conversion failed.');
    return data.converted;
}

// Calls the server-side generate-qc-brief Edge Function: a Claude-vision pass
// over the tenant's product photo (+ packaging + mask_prompt) that writes
// products.qc_brief — the QC reviewer's ground truth. Runs once per product
// (the function skips if a brief already exists unless { force: true }).
export async function generateQcBrief(opts) {
    const { data, error } = await supabase.functions.invoke('generate-qc-brief', {
        method: 'POST', body: opts || {},
    });
    if (error) throw error;
    if (!data?.ok) throw new Error(data?.message || data?.error || 'QC brief generation failed.');
    return data;
}
