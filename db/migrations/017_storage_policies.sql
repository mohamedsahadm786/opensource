-- ============================================================================
-- 017_storage_policies.sql  — browser Storage access for the web app (ADDITIVE)
-- ----------------------------------------------------------------------------
-- The base schema (001 §21) creates 5 PRIVATE buckets but ships no
-- storage.objects RLS, so the anon/authenticated key cannot upload or sign URLs.
-- This adds per-tenant Storage policies so the WEB (anon key, RLS) can:
--   * upload its own files   (product photo -> 'products', voice -> 'audio')
--   * sign/read its own files (portraits/images/audio/videos)
-- scoped to the caller's own '<tenant_id>/...' folder.
--
-- The orchestrator + the admin-data Edge Function use the SERVICE key, which
-- BYPASSES RLS — so they are completely unaffected by these policies.
-- Idempotent (drop-if-exists). Does NOT alter any existing table/policy.
-- ============================================================================

-- NOTE: storage.objects already has RLS ENABLED by Supabase by default, and the
-- SQL-editor role is NOT the owner of that system table, so we must NOT run
-- `alter table storage.objects enable row level security` (it errors 42501).
-- Creating policies on storage.objects IS permitted from the SQL editor — that's
-- all this migration needs.

-- A user owns the objects whose FIRST path segment equals their tenant_id.
-- e.g. '<tenant_id>/products/box.jpg'  ->  (storage.foldername(name))[1] = '<tenant_id>'
-- public.current_tenant_id() reads tenant_id from the JWT app_metadata.

do $$
declare b text;
begin
  foreach b in array array['products','portraits','images','audio','videos']
  loop
    -- read
    execute format($f$
      drop policy if exists %1$s on storage.objects;
      create policy %1$s on storage.objects
        for select to authenticated
        using (
          bucket_id = %2$L
          and (storage.foldername(name))[1] = public.current_tenant_id()::text
        );
    $f$, 'st_'||b||'_read', b);

    -- insert
    execute format($f$
      drop policy if exists %1$s on storage.objects;
      create policy %1$s on storage.objects
        for insert to authenticated
        with check (
          bucket_id = %2$L
          and (storage.foldername(name))[1] = public.current_tenant_id()::text
        );
    $f$, 'st_'||b||'_insert', b);

    -- update
    execute format($f$
      drop policy if exists %1$s on storage.objects;
      create policy %1$s on storage.objects
        for update to authenticated
        using (
          bucket_id = %2$L
          and (storage.foldername(name))[1] = public.current_tenant_id()::text
        )
        with check (
          bucket_id = %2$L
          and (storage.foldername(name))[1] = public.current_tenant_id()::text
        );
    $f$, 'st_'||b||'_update', b);

    -- delete
    execute format($f$
      drop policy if exists %1$s on storage.objects;
      create policy %1$s on storage.objects
        for delete to authenticated
        using (
          bucket_id = %2$L
          and (storage.foldername(name))[1] = public.current_tenant_id()::text
        );
    $f$, 'st_'||b||'_delete', b);
  end loop;
end$$;
