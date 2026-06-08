-- ============================================================================
-- 018_impersonation_events.sql  — super-admin audit log (ADDITIVE)
-- ----------------------------------------------------------------------------
-- Records platform-owner (super-admin) actions against tenants: viewing a
-- tenant's console ("Page"/impersonation) and lifecycle changes
-- (suspend/reactivate/remove). Written + read ONLY by the service-role
-- `admin-data` Edge Function. RLS is enabled with NO anon/authenticated policy,
-- so the browser anon key can never touch it; the service key bypasses RLS.
-- Does NOT alter any existing table.
-- ============================================================================

create table if not exists public.impersonation_events (
  id            uuid primary key default gen_random_uuid(),
  actor         text not null default 'super_admin',
  tenant_id     uuid references public.tenants(id) on delete set null,
  tenant_name   text,
  tenant_email  text,
  action        text not null,           -- view_page | suspend | reactivate | remove
  created_at    timestamptz not null default now()
);

create index if not exists idx_impersonation_events_created
  on public.impersonation_events (created_at desc);

alter table public.impersonation_events enable row level security;
-- (intentionally no policy: only service_role — which bypasses RLS — uses it.)

grant all on public.impersonation_events to service_role;
