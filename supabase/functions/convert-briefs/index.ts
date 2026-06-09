// convert-briefs — turns the tenant's 3 plain-English briefs into the exact JSON
// the pipeline reads (Brief Brief v2 §5). Runs server-side with the tenant's
// Anthropic key (from Vault) + the service role; the browser never types JSON.
//
// Input (POST, member JWT): { product_brief?, company_brief?, script_brief? }
//   (any omitted brief is read from the DB; an empty brief is skipped.)
// Writes:
//   products.name, products.product_info, products.packaging       (from product brief)
//   tenants.script_company_info                                    (from company brief)
//   tenants.script_directives                                      (from script brief)
// Returns: { ok, converted: { product?, company?, script? } }
//
// Deploy with: supabase functions deploy convert-briefs --no-verify-jwt

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { json, preflight } from '../_shared/cors.ts';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const CONVERT_MODEL = Deno.env.get('CONVERT_MODEL') || 'claude-sonnet-4-6';

// ---- §5 system prompts (one per brief type; target JSON shapes UNCHANGED — the
//      pipeline reads these exact keys, do not rename them) ----------------------

// A. PRODUCT brief -> products.name + product_info + packaging
const PRODUCT_SYS = `You are a brand product analyst for an AI ad-generation pipeline. Convert a plain-English product
brief into ONE JSON object. Output ONLY valid JSON — no markdown, no commentary.

This JSON feeds TWO different models, so put the right content in the right place:
- product_info  -> used to WRITE SPOKEN AD SCRIPTS. Narrative/lifestyle content only.
- packaging     -> used by an IMAGE model to RENDER THE PHYSICAL BOX. Visual facts only.

Field meanings:
- name: the product's display name.
- product_info.product_name: short product name; product_info.product_type: what kind of product it is.
- product_info.wellness_associations: lifestyle/wellness themes to evoke in ads (e.g. "daily energy",
  "steady routine"). NOT medical or efficacy claims.
- product_info.positive_lifestyle_language: upbeat adjectives/phrases for framing (e.g. "sustainable",
  "premium", "calm").
- packaging.type / packaging.shape: physical form (e.g. "carton box", "rectangular").
- packaging.colors: dominant colors of the box.
- packaging.text_on_packaging: the EXACT words printed on the box, each as a SEPARATE string, copied
  VERBATIM from the brief (these are rendered literally onto the product — do not paraphrase, reorder,
  or invent text that isn't in the brief).
- packaging.graphics: logos/motifs/illustrations on the box.
- packaging.dims: approximate dimensions if given.

Rules: keep ALL medical/mechanism/efficacy claims OUT of product_info. If the brief omits a packaging
detail, omit that sub-key rather than guessing the box's appearance. Expand product_info arrays with
several items; keep packaging strictly to what the brief states.

Match this exact shape:
{
  "name": "string",
  "product_info": { "product_name": "", "product_type": "",
                    "wellness_associations": [], "positive_lifestyle_language": [] },
  "packaging": { "type": "", "shape": "", "colors": [],
                 "text_on_packaging": [], "graphics": "", "dims": "" }
}

Example —
Brief: "LUMA Magnesium is a calming nightly wellness supplement for busy adults who want better rest
and steadier energy. Premium, clean, science-aware vibe. The box is a small matte navy carton, gold
'LUMA' wordmark on top, 'MAGNESIUM GLYCINATE' under it, and small text 'nightly calm'. A thin gold
crescent-moon line on the front. About 10cm tall, 5cm wide, 4cm deep."
Output:
{
  "name": "LUMA Magnesium",
  "product_info": {
    "product_name": "LUMA Magnesium",
    "product_type": "nightly wellness supplement",
    "wellness_associations": ["better rest", "steadier energy", "evening calm", "daily routine", "recovery"],
    "positive_lifestyle_language": ["premium", "clean", "calming", "science-aware", "restorative"]
  },
  "packaging": {
    "type": "carton box", "shape": "small rectangular carton",
    "colors": ["matte navy", "gold"],
    "text_on_packaging": ["LUMA", "MAGNESIUM GLYCINATE", "nightly calm"],
    "graphics": "thin gold crescent-moon line on the front", "dims": "approx 10cm x 5cm x 4cm"
  }
}`;

// B. COMPANY / brand brief -> tenants.script_company_info
const COMPANY_SYS = `You are a brand strategist for an AI ad-generation pipeline. Convert a plain-English company/brand
brief into ONE JSON object that guides both scene generation and script writing. Output ONLY valid
JSON — no markdown, no commentary.

Field meanings:
- system_identity.brand_name / product_name: the brand and its product.
- system_identity.brand_positioning: a one-line positioning statement.
- brand_personality.brand_voice: tone words for how the brand speaks (e.g. "warm", "confident").
- brand_personality.core_traits: brand character traits (e.g. "trustworthy", "premium").
- marketing_language_engine.high_performing_phrases: short phrases/taglines that convert for this brand.
- marketing_language_engine.hook_styles: opening-hook patterns that grab attention (e.g. "relatable
  everyday moment", "before-the-mirror reflection").
- marketing_language_engine.conversation_styles: how spoken dialogue should feel (e.g. "casual
  first-person", "friend giving advice").
- video_generation_preferences.camera_motion: preferred camera moves (e.g. "slow push-in", "subtle pan").
- video_generation_preferences.motion_behavior: how the subject/scene should move (e.g. "relaxed,
  natural movement").
- video_generation_preferences.visual_style: overall look (e.g. "bright, clean, warm tones, natural light").
- scene_generation_system.scene_moods: moods scenes should convey (e.g. "calm", "confident", "cozy").

Expand each array with several concrete items inferred from the brief. Keep the exact keys.

Match this exact shape:
{
  "system_identity": { "brand_name": "", "product_name": "", "brand_positioning": "" },
  "brand_personality": { "brand_voice": [], "core_traits": [] },
  "marketing_language_engine": { "high_performing_phrases": [], "hook_styles": [], "conversation_styles": [] },
  "video_generation_preferences": { "camera_motion": [], "motion_behavior": [], "visual_style": [] },
  "scene_generation_system": { "scene_moods": [] }
}

Example —
Brief: "NOVA is a premium daytime focus drink for young professionals. Calm, confident, modern — not
hypey. Voice is like a sharp friend. Lines like 'locked in by 9am' and 'no jitters, just clarity' do
well. Hooks that work: someone starting their workday, a quiet desk flex. Keep talking casual and
first-person. Videos should feel smooth and clean — slow push-ins, gentle handheld, the person calm
and natural, bright modern workspaces. Moods: focused, calm, aspirational."
Output:
{
  "system_identity": { "brand_name": "NOVA", "product_name": "NOVA focus drink",
    "brand_positioning": "a premium daytime focus drink for young professionals" },
  "brand_personality": { "brand_voice": ["calm", "confident", "modern", "sharp", "non-hypey"],
    "core_traits": ["premium", "trustworthy", "focused", "approachable"] },
  "marketing_language_engine": {
    "high_performing_phrases": ["locked in by 9am", "no jitters, just clarity", "clean focus, all day"],
    "hook_styles": ["starting the workday", "a quiet desk flex", "morning routine moment"],
    "conversation_styles": ["casual first-person", "a sharp friend giving a tip"] },
  "video_generation_preferences": {
    "camera_motion": ["slow push-in", "gentle handheld", "subtle pan"],
    "motion_behavior": ["calm, natural movement", "relaxed and unhurried"],
    "visual_style": ["bright", "clean", "modern workspaces", "natural light"] },
  "scene_generation_system": { "scene_moods": ["focused", "calm", "aspirational", "confident"] }
}`;

// C. SCRIPT / dialogue brief -> tenants.script_directives
const SCRIPT_SYS = `You are a short-form video script director for an AI ad-generation pipeline. Convert a plain-English
script/dialogue brief into ONE JSON object of directives the script writer must follow. Output ONLY
valid JSON — no markdown, no commentary.

Field meanings:
- dialogue_generation_rules.dialogue_style: how lines should sound (tone, length, person).
- dialogue_generation_rules.dialogue_requirements: must-haves for every script (e.g. "hook in the
  first line", "mention the brand once naturally", "end with a soft nudge").
- dialogue_generation_rules.example_dialogues: a few example spoken lines, taken from or written in
  the spirit of the brief.
- ai_generation_priorities.highest_priority: what matters most when writing (e.g. "authenticity",
  "scroll-stopping first line").
- ai_generation_priorities.negative_generation_controls: hard "never do" rules (e.g. "no medical
  claims", "no guaranteed results", "no fear/shame language", "no dosages").

Expand arrays with several concrete items. Put every "avoid / never" instruction into
negative_generation_controls. Keep the exact keys.

Match this exact shape:
{
  "dialogue_generation_rules": { "dialogue_style": [], "dialogue_requirements": [], "example_dialogues": [] },
  "ai_generation_priorities": { "highest_priority": [], "negative_generation_controls": [] }
}

Example —
Brief: "Talk like a real person, first-person, short natural sentences, never salesy. Always open with
a relatable hook, give one honest point, end with a gentle nudge (no hard sell). Mention the brand
once. Example: 'honestly this just made the mornings easier.' Most important thing is authenticity and
a strong first line. Never make medical claims, never promise results or numbers, no shame or fear,
no dosage talk."
Output:
{
  "dialogue_generation_rules": {
    "dialogue_style": ["first-person", "short natural sentences", "conversational", "never salesy"],
    "dialogue_requirements": ["open with a relatable hook", "make one honest point",
      "mention the brand once naturally", "end with a gentle, low-pressure nudge"],
    "example_dialogues": ["honestly this just made the mornings easier",
      "I just wanted something I could actually keep up with"] },
  "ai_generation_priorities": {
    "highest_priority": ["authenticity", "a strong scroll-stopping first line"],
    "negative_generation_controls": ["no medical or disease claims", "no promised results or numbers",
      "no fear or shame language", "no dosage or medical instructions", "no hard sell"] }
}`;

function parseJson(text: string): any {
  let t = (text || '').trim();
  if (t.startsWith('```')) t = t.replace(/^```[a-zA-Z]*\n?/, '').replace(/```\s*$/, '');
  const i = t.indexOf('{'), j = t.lastIndexOf('}');
  if (i === -1 || j === -1) throw new Error('no JSON object in model output');
  return JSON.parse(t.slice(i, j + 1));
}

async function claudeToJson(apiKey: string, system: string, brief: string): Promise<any> {
  const resp = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'x-api-key': apiKey, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
    body: JSON.stringify({
      model: CONVERT_MODEL,
      max_tokens: 4096,
      system,
      messages: [{ role: 'user', content: `Brief:\n\n${brief}` }],
    }),
  });
  if (!resp.ok) throw new Error(`Anthropic ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  const data = await resp.json();
  return parseJson(data?.content?.[0]?.text || '');
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

    // Pull the tenant's Anthropic key from Vault.
    const { data: apiKey, error: keyErr } = await admin.rpc('get_tenant_anthropic_key', { p_tenant_id: tenantId });
    if (keyErr || !apiKey) return json({ ok: false, error: 'no_anthropic_key' }, 400);

    // Resolve each brief (body first, else DB).
    const [tRow, pRow] = await Promise.all([
      admin.from('tenants').select('company_brief_text, script_brief_text').eq('id', tenantId).maybeSingle(),
      admin.from('products').select('id, product_brief_text').eq('tenant_id', tenantId).maybeSingle(),
    ]);
    const productBrief = (body.product_brief ?? pRow.data?.product_brief_text ?? '').trim();
    const companyBrief = (body.company_brief ?? tRow.data?.company_brief_text ?? '').trim();
    const scriptBrief = (body.script_brief ?? tRow.data?.script_brief_text ?? '').trim();

    const converted: Record<string, unknown> = {};

    // A — product brief -> name + product_info + packaging
    if (productBrief) {
      const out = await claudeToJson(apiKey, PRODUCT_SYS, productBrief);
      const patch: Record<string, unknown> = {
        product_info: out.product_info ?? {},
        packaging: out.packaging ?? {},
        updated_at: new Date().toISOString(),
      };
      if (out.name) patch.name = out.name;
      await admin.from('products').update(patch).eq('tenant_id', tenantId);
      converted.product = out;
    }

    // B — company brief -> script_company_info
    if (companyBrief) {
      const out = await claudeToJson(apiKey, COMPANY_SYS, companyBrief);
      await admin.from('tenants').update({ script_company_info: out, updated_at: new Date().toISOString() }).eq('id', tenantId);
      converted.company = out;
    }

    // C — script brief -> script_directives
    if (scriptBrief) {
      const out = await claudeToJson(apiKey, SCRIPT_SYS, scriptBrief);
      await admin.from('tenants').update({ script_directives: out, updated_at: new Date().toISOString() }).eq('id', tenantId);
      converted.script = out;
    }

    return json({ ok: true, converted });
  } catch (err) {
    return json({ ok: false, error: 'conversion_failed', message: String(err) }, 500);
  }
});
