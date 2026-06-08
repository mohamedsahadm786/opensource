-- ============================================================================
-- 019_set_secret_rpc.sql  — server-side helper to store a tenant's Anthropic key
-- ----------------------------------------------------------------------------
-- The browser cannot write to Vault (service-role only). The `store-tenant-secret`
-- Edge Function calls THIS security-definer RPC instead, which:
--   * creates (or rotates) the Vault secret named 'anthropic:'||tenant_id
--   * stores only the reference name on tenants.anthropic_secret_name
-- The pipeline reads it back via the existing get_tenant_anthropic_key(tenant_id).
-- Granted to service_role only. ADDITIVE — alters no existing object.
-- ============================================================================

create or replace function public.set_tenant_anthropic_key(p_tenant_id uuid, p_key text)
returns void
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  v_name text := 'anthropic:' || p_tenant_id::text;
  v_id   uuid;
begin
  select id into v_id from vault.secrets where name = v_name limit 1;
  if v_id is null then
    perform vault.create_secret(p_key, v_name, 'Anthropic key');
  else
    perform vault.update_secret(v_id, p_key, v_name, 'Anthropic key');
  end if;

  update public.tenants
     set anthropic_secret_name = v_name,
         updated_at = now()
   where id = p_tenant_id;
end;
$$;

revoke execute on function public.set_tenant_anthropic_key(uuid, text) from public;
grant  execute on function public.set_tenant_anthropic_key(uuid, text) to service_role;
