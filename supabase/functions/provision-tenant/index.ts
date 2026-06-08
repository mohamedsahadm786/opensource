// provision-tenant — turns a freshly-signed-up auth user into a tenant.
//
// Required because:
//   * `tenants` has only a SELECT policy for `authenticated` (no INSERT), and
//   * the user's JWT has no app_metadata.tenant_id yet (chicken-and-egg with RLS).
// So provisioning must run server-side with the service role.
//
// Flow (idempotent): verify caller JWT -> if they already have a tenant, ensure
// app_metadata.tenant_id is set and return it; else create tenants + an owner
// tenant_members row, then stamp app_metadata.tenant_id on the auth user.
// The CLIENT must call supabase.auth.refreshSession() afterwards so the new
// tenant_id lands in the live token (RLS reads depend on it).
//
// Deploy with: supabase functions deploy provision-tenant --no-verify-jwt
// (we verify the bearer token ourselves below).

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { corsHeaders, json, preflight } from '../_shared/cors.ts';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;

function slugify(base: string): string {
  const s = (base || 'tenant')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32) || 'tenant';
  return `${s}-${crypto.randomUUID().slice(0, 6)}`;
}

Deno.serve(async (req) => {
  const pf = preflight(req);
  if (pf) return pf;

  try {
    const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });

    // 1) identify the caller from the bearer token
    const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    if (!token) return json({ ok: false, error: 'missing_token' }, 401);
    const { data: userData, error: userErr } = await admin.auth.getUser(token);
    if (userErr || !userData?.user) return json({ ok: false, error: 'invalid_token' }, 401);
    const user = userData.user;

    let body: Record<string, unknown> = {};
    try { body = await req.json(); } catch { /* optional body */ }
    const companyName =
      (body.company_name as string)?.trim() ||
      (user.user_metadata?.name as string)?.trim() ||
      user.email?.split('@')[0] ||
      'My Company';

    // 2) already provisioned? (member row exists)
    const { data: existing } = await admin
      .from('tenant_members')
      .select('tenant_id')
      .eq('user_id', user.id)
      .limit(1)
      .maybeSingle();

    let tenantId = existing?.tenant_id as string | undefined;

    // 3) create tenant + owner membership if needed
    if (!tenantId) {
      const { data: tenant, error: tErr } = await admin
        .from('tenants')
        .insert({ name: companyName, slug: slugify(companyName), email: user.email, plan: 'free' })
        .select('id')
        .single();
      if (tErr) return json({ ok: false, error: 'tenant_create_failed', message: tErr.message }, 500);
      tenantId = tenant.id;

      const { error: mErr } = await admin
        .from('tenant_members')
        .insert({ tenant_id: tenantId, user_id: user.id, role: 'owner' });
      if (mErr) return json({ ok: false, error: 'member_create_failed', message: mErr.message }, 500);
    }

    // 4) stamp tenant_id into app_metadata (this is what RLS reads)
    const { error: updErr } = await admin.auth.admin.updateUserById(user.id, {
      app_metadata: { ...(user.app_metadata || {}), tenant_id: tenantId },
    });
    if (updErr) return json({ ok: false, error: 'metadata_update_failed', message: updErr.message }, 500);

    return json({ ok: true, tenant_id: tenantId });
  } catch (err) {
    return json({ ok: false, error: 'unknown', message: String(err) }, 500);
  }
});
