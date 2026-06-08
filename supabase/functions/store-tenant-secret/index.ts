// store-tenant-secret — stores a tenant's Anthropic API key in Supabase Vault.
//
// Vault is service-role only, so the browser can never write it. The caller's
// JWT identifies the tenant (via tenant_members); we then call the security-
// definer RPC public.set_tenant_anthropic_key(tenant_id, key) which creates or
// rotates the Vault secret and stamps tenants.anthropic_secret_name. The key is
// never returned to the client.
//
// Deploy with: supabase functions deploy store-tenant-secret --no-verify-jwt

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { json, preflight } from '../_shared/cors.ts';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;

Deno.serve(async (req) => {
  const pf = preflight(req);
  if (pf) return pf;

  try {
    const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });

    const token = (req.headers.get('Authorization') || '').replace(/^Bearer\s+/i, '');
    if (!token) return json({ ok: false, error: 'missing_token' }, 401);
    const { data: userData, error: userErr } = await admin.auth.getUser(token);
    if (userErr || !userData?.user) return json({ ok: false, error: 'invalid_token' }, 401);

    const { key } = await req.json().catch(() => ({ key: '' }));
    const trimmed = (key as string)?.trim();
    if (!trimmed) return json({ ok: false, error: 'missing_key' }, 400);

    // resolve the caller's tenant
    const { data: member } = await admin
      .from('tenant_members')
      .select('tenant_id')
      .eq('user_id', userData.user.id)
      .limit(1)
      .maybeSingle();
    if (!member?.tenant_id) return json({ ok: false, error: 'no_tenant' }, 400);

    const { error: rpcErr } = await admin.rpc('set_tenant_anthropic_key', {
      p_tenant_id: member.tenant_id,
      p_key: trimmed,
    });
    if (rpcErr) return json({ ok: false, error: 'vault_write_failed', message: rpcErr.message }, 500);

    return json({ ok: true });
  } catch (err) {
    return json({ ok: false, error: 'unknown', message: String(err) }, 500);
  }
});
