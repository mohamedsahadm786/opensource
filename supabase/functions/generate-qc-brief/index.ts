// generate-qc-brief — ONE-TIME per product (one product per tenant).
//
// Mirrors orchestrator/generate_qc_brief.py: a Claude VISION pass over the real
// product photo + structured packaging + mask_prompt produces a precise QC
// ground-truth brief, stored in products.qc_brief (+ qc_brief_generated_at). The
// QC reviewer injects this so product judgments are product-accurate.
//
// Flow: member JWT -> resolve tenant -> read products.reference_asset_id ->
//   download the photo from the `products` bucket -> Claude vision with the
//   tenant's Vault Anthropic key -> write products.qc_brief.
//
// Input (POST, member JWT): { force?: boolean }
//   force=false (default): skip if a qc_brief already exists.
// Returns: { ok, skipped?: boolean, qc_brief?: string }
//
// Deploy with: supabase functions deploy generate-qc-brief --no-verify-jwt

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { json, preflight } from '../_shared/cors.ts';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
// Mirror the orchestrator default (OPUS_MODEL=claude-opus-4-7); override via secret.
const BRIEF_MODEL = Deno.env.get('QC_BRIEF_MODEL') || Deno.env.get('OPUS_MODEL') || 'claude-opus-4-7';

// The QC-brief builder system prompt — kept identical to
// orchestrator/rules/qc_brief_builder.md (Deno can't read the local rules dir).
const QC_BRIEF_SYS = `You are creating a PRODUCT BRIEF for an automated image quality-control (QC) reviewer.

CONTEXT: an automated pipeline composites this product into lifestyle ad images. A QC reviewer then looks at each generated image and must judge whether the product was rendered faithfully (right text, right shape, right colours, not warped or duplicated). Your brief becomes that reviewer's GROUND TRUTH for this specific product — it is read once per QC check, so it must be precise, factual, and compact.

YOU RECEIVE:
1. The real product reference photo (image) — the authoritative source.
2. Structured packaging data (text strings, colours, graphics, dimensions).
3. The product's box-protect mask prompt (a short shape description used elsewhere).

WRITE the brief as compact labelled prose (~150–230 words), covering exactly these sections:

PRODUCT TYPE & SHAPE: what the packaging physically is (box / bottle / pouch / etc.), its shape, orientation, and rough proportions.

EXACT TEXT: every text string printed on the product, quoted verbatim ("like this"), and WHERE each one sits (front face, certification seal, dose callout, side panel, vial/pen label). Explicitly mark which 1–3 strings are the LARGEST / most important to be legible — these are what QC weights most.

COLOURS & GRAPHICS: the colour scheme and the key graphic elements (gradients, seals, badges, patterns, callouts) and where each sits on the packaging.

FIDELITY CHECKLIST: the 3–6 things that MUST be correct for this product to read as authentic (e.g., the brand name legible, the certification seal present and the right colour, the box rectangular and un-warped, the dose callout readable).

COMMON FAILURE MODES: how AI image-editing models typically corrupt THIS specific product (e.g., garbled letters on the longest text line, warped or bent box edges, the seal rendered the wrong colour, the wave graphic smeared).

RULES:
- Describe ONLY what is actually present in the reference photo and the provided data. Never invent text, logos, or features you cannot see.
- If the photo and the data disagree, trust the PHOTO and note the discrepancy briefly.
- No preamble, no markdown fences, no commentary about the task — output only the brief itself.`;

// Encode bytes -> base64 in chunks (avoids call-stack blowups on big images).
function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

Deno.serve(async (req) => {
  const pf = preflight(req);
  if (pf) return pf;

  try {
    const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });

    const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    if (!token) return json({ ok: false, error: 'missing_token' }, 401);
    const { data: userData, error: userErr } = await admin.auth.getUser(token);
    if (userErr || !userData?.user) return json({ ok: false, error: 'invalid_token' }, 401);

    const { data: member } = await admin
      .from('tenant_members').select('tenant_id').eq('user_id', userData.user.id).limit(1).maybeSingle();
    if (!member?.tenant_id) return json({ ok: false, error: 'no_tenant' }, 400);
    const tenantId = member.tenant_id as string;

    const body = await req.json().catch(() => ({}));
    const force = Boolean(body?.force);

    // The one product for this tenant.
    const { data: product } = await admin
      .from('products')
      .select('name, packaging, mask_prompt, reference_asset_id, qc_brief')
      .eq('tenant_id', tenantId)
      .maybeSingle();
    if (!product) return json({ ok: false, error: 'no_product' }, 400);
    if (product.qc_brief && !force) return json({ ok: true, skipped: true });
    if (!product.reference_asset_id) return json({ ok: false, error: 'no_reference_photo' }, 400);

    // Resolve the photo's bucket/path and download the bytes.
    const { data: asset } = await admin
      .from('media_assets').select('bucket, path, mime_type').eq('id', product.reference_asset_id).maybeSingle();
    if (!asset) return json({ ok: false, error: 'reference_asset_missing' }, 400);

    const { data: blob, error: dlErr } = await admin.storage.from(asset.bucket).download(asset.path);
    if (dlErr || !blob) return json({ ok: false, error: 'photo_download_failed', message: dlErr?.message }, 500);
    const mediaType = asset.mime_type || 'image/jpeg';
    const b64 = bytesToBase64(new Uint8Array(await blob.arrayBuffer()));

    // Tenant's Anthropic key from Vault.
    const { data: apiKey, error: keyErr } = await admin.rpc('get_tenant_anthropic_key', { p_tenant_id: tenantId });
    if (keyErr || !apiKey) return json({ ok: false, error: 'no_anthropic_key' }, 400);

    const details = {
      name: product.name,
      packaging: product.packaging,
      mask_prompt: product.mask_prompt,
    };
    const userText =
      'Here is the product. Write the QC product brief per your instructions.\n\n' +
      'Structured packaging data and mask prompt:\n' +
      JSON.stringify(details, null, 2);

    const resp = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'x-api-key': apiKey, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
      body: JSON.stringify({
        model: BRIEF_MODEL,
        max_tokens: 1200,
        system: QC_BRIEF_SYS,
        messages: [{
          role: 'user',
          content: [
            { type: 'image', source: { type: 'base64', media_type: mediaType, data: b64 } },
            { type: 'text', text: userText },
          ],
        }],
      }),
    });
    if (!resp.ok) {
      return json({ ok: false, error: 'anthropic_error', message: `${resp.status}: ${(await resp.text()).slice(0, 300)}` }, 502);
    }
    const data = await resp.json();
    const brief = (data?.content?.[0]?.text || '').trim();
    if (!brief) return json({ ok: false, error: 'empty_brief' }, 502);

    await admin.from('products').update({
      qc_brief: brief,
      qc_brief_generated_at: new Date().toISOString(),
    }).eq('tenant_id', tenantId);

    return json({ ok: true, qc_brief: brief });
  } catch (err) {
    return json({ ok: false, error: 'qc_brief_failed', message: String(err) }, 500);
  }
});
