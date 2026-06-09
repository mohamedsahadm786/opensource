// admin-data — service-role data plane for the super-admin console.
//
// Every table is RLS-scoped to the caller's own tenant, so the anon key can't
// see cross-tenant data. The super-admin (hard-coded creds, no Supabase session)
// routes all of its reads/writes through this function, gated by a shared secret
// (ADMIN_SECRET env == the admin password). The service key never reaches the
// browser.
//
// Actions:
//   overview     -> { profiles, accounts, personas, outputs, videos } (client aggregates)
//   set_status   -> { tenant_id, status }  flips tenants.status
//   log_event    -> { action, tenant_id, tenant_name, tenant_email }  audit insert
//   list_events  -> { limit }  recent impersonation_events
//
// Deploy with: supabase functions deploy admin-data --no-verify-jwt
// Set the secret:  supabase secrets set ADMIN_SECRET=<the admin password>

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { json, preflight } from '../_shared/cors.ts';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const ADMIN_SECRET = Deno.env.get('ADMIN_SECRET') || '';

Deno.serve(async (req) => {
  const pf = preflight(req);
  if (pf) return pf;

  try {
    const body = await req.json().catch(() => ({}));
    const { action, secret } = body as { action?: string; secret?: string };

    if (!ADMIN_SECRET || secret !== ADMIN_SECRET) {
      return json({ ok: false, error: 'forbidden' }, 403);
    }

    const admin = createClient(SUPABASE_URL, SERVICE_KEY, { auth: { persistSession: false } });

    if (action === 'overview') {
      const [tenantsRes, accountsRes, personasRes, outputsRes, videosRes] = await Promise.all([
        admin.from('tenants').select('id, name, email, slug, plan, status, settings, created_at'),
        admin.from('tiktok_accounts').select('id, tenant_id, tiktok_id, name, created_at'),
        admin.from('personas').select('id, tiktok_account_id, created_at'),
        admin.from('outputs').select('id, persona_id, qc_status, created_at'),
        admin.from('videos').select('id, output_id, created_at'),
      ]);
      const err = tenantsRes.error || accountsRes.error || personasRes.error
        || outputsRes.error || videosRes.error;
      if (err) return json({ ok: false, error: err.message }, 500);

      // Map tenants -> the profile shape the console expects.
      const profiles = (tenantsRes.data || []).map((t) => ({
        tenant_id: t.id,
        name: t.name,
        email: t.email,
        slug: t.slug,
        plan: t.plan,
        status: t.status || 'active',
        onboarded: Boolean((t.settings || {}).onboarded),
        created_at: t.created_at,
      }));

      return json({
        ok: true,
        profiles,
        accounts: accountsRes.data || [],
        personas: personasRes.data || [],
        outputs: outputsRes.data || [],
        videos: videosRes.data || [],
      });
    }

    if (action === 'set_status') {
      const { tenant_id, status } = body as { tenant_id?: string; status?: string };
      if (!tenant_id || !status) return json({ ok: false, error: 'bad_request' }, 400);
      const { error } = await admin
        .from('tenants')
        .update({ status, updated_at: new Date().toISOString() })
        .eq('id', tenant_id);
      if (error) return json({ ok: false, error: error.message }, 500);
      return json({ ok: true });
    }

    if (action === 'set_profile') {
      const { tenant_id, name, email, plan, onboarded } = body as Record<string, unknown>;
      if (!tenant_id) return json({ ok: false, error: 'bad_request' }, 400);
      // read current settings to merge the onboarded flag
      const { data: cur } = await admin.from('tenants').select('settings').eq('id', tenant_id).maybeSingle();
      const settings = { ...((cur?.settings as Record<string, unknown>) || {}) };
      if (onboarded !== undefined) settings.onboarded = Boolean(onboarded);
      const patch: Record<string, unknown> = { settings, updated_at: new Date().toISOString() };
      if (name !== undefined) patch.name = (name as string)?.trim() || null;
      if (email !== undefined) patch.email = (email as string)?.trim() || null;
      if (plan !== undefined) patch.plan = plan || null;
      const { error } = await admin.from('tenants').update(patch).eq('id', tenant_id);
      if (error) return json({ ok: false, error: error.message }, 500);
      return json({ ok: true });
    }

    if (action === 'log_event') {
      const { action: act, tenant_id, tenant_name, tenant_email } = body as Record<string, string>;
      const { error } = await admin.from('impersonation_events').insert({
        actor: 'super_admin',
        action: act,
        tenant_id: tenant_id || null,
        tenant_name: tenant_name || null,
        tenant_email: tenant_email || null,
      });
      if (error) return json({ ok: false, error: error.message }, 500);
      return json({ ok: true });
    }

    if (action === 'impersonate') {
      // Mint a real session for the tenant's owner so the super-admin sees the
      // exact tenant workspace (RLS passes because the browser gets a JWT that
      // carries this tenant_id). Returns a magic-link token the client verifies.
      const { tenant_id } = body as { tenant_id?: string };
      if (!tenant_id) return json({ ok: false, error: 'bad_request' }, 400);

      let { data: m } = await admin.from('tenant_members')
        .select('user_id').eq('tenant_id', tenant_id).eq('role', 'owner').limit(1).maybeSingle();
      if (!m) {
        const any = await admin.from('tenant_members')
          .select('user_id').eq('tenant_id', tenant_id).limit(1).maybeSingle();
        m = any.data;
      }
      if (!m?.user_id) return json({ ok: false, error: 'no_member_for_tenant' }, 400);

      const { data: u, error: uErr } = await admin.auth.admin.getUserById(m.user_id as string);
      const email = u?.user?.email;
      if (uErr || !email) return json({ ok: false, error: 'no_user_email' }, 400);

      const { data: link, error: lErr } = await admin.auth.admin.generateLink({ type: 'magiclink', email });
      if (lErr) return json({ ok: false, error: lErr.message }, 500);
      const token_hash = (link as { properties?: { hashed_token?: string } })?.properties?.hashed_token;
      if (!token_hash) return json({ ok: false, error: 'no_token' }, 500);

      return json({ ok: true, email, token_hash });
    }

    if (action === 'list_events') {
      const limit = Number((body as { limit?: number }).limit) || 100;
      const { data, error } = await admin
        .from('impersonation_events')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(limit);
      if (error) return json({ ok: false, error: error.message }, 500);
      return json({ ok: true, events: data || [] });
    }

    return json({ ok: false, error: 'unknown_action' }, 400);
  } catch (err) {
    return json({ ok: false, error: 'unknown', message: String(err) }, 500);
  }
});
