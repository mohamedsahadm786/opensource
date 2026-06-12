// trigger-pipeline — enqueues a pipeline run on the durable job queue.
//
// Enterprise pattern: instead of poking the GPU, we INSERT a row into
// public.jobs (job_type='pipeline_run'). The orchestrator's claim_next_job RPC
// is the consumer. The web never talks to the GPU.
//
// NOTE (backend dependency): a 'pipeline_run' consumer must exist orchestrator-
// side (a small daemon that claims the job and runs run_pipeline.py --tenant).
// Until that exists, the job is enqueued correctly but nothing actions it.
//
// Deploy with: supabase functions deploy trigger-pipeline --no-verify-jwt

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

    const { data: member } = await admin
      .from('tenant_members')
      .select('tenant_id')
      .eq('user_id', userData.user.id)
      .limit(1)
      .maybeSingle();
    if (!member?.tenant_id) return json({ ok: false, error: 'no_tenant' }, 400);

    // P6 (video.md): freeze the run's config INTO the job payload so run_pipeline
    // never has to re-read tenant_pipeline_config (kills the Save-vs-Run race).
    // Prefer the client-sent snapshot (= the committed row the upsert returned to
    // the browser); fall back to a server-side read for older web bundles. A
    // missing snapshot is never fatal — run_pipeline falls back to a live read.
    let snapshot: Record<string, unknown> | null = null;
    try {
      const body = await req.json().catch(() => null);
      const cs = body?.config_snapshot;
      if (cs && typeof cs === 'object' && !Array.isArray(cs)) snapshot = cs;
    } catch { /* no body — fine */ }
    if (!snapshot) {
      const { data: cfgRow } = await admin
        .from('tenant_pipeline_config')
        .select('*')
        .eq('tenant_id', member.tenant_id)
        .maybeSingle();
      if (cfgRow) snapshot = cfgRow;
    }

    const { data: job, error: jErr } = await admin
      .from('jobs')
      .insert({
        tenant_id: member.tenant_id,
        job_type: 'pipeline_run',
        status: 'queued',
        request_payload: {
          source: 'web',
          triggered_by: userData.user.id,
          ...(snapshot ? { config_snapshot: snapshot } : {}),
        },
        idempotency_key: `pipeline_run:${member.tenant_id}:${crypto.randomUUID()}`,
      })
      .select('id')
      .single();
    if (jErr) return json({ ok: false, error: 'enqueue_failed', message: jErr.message }, 500);

    return json({ ok: true, job_id: job.id });
  } catch (err) {
    return json({ ok: false, error: 'unknown', message: String(err) }, 500);
  }
});
