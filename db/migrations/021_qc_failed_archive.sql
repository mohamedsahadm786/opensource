-- ============================================================================
-- 021_qc_failed_archive.sql — archive QC-FAILED Step-2 composites (ADDITIVE)
-- ----------------------------------------------------------------------------
-- TEMPORARY tuning aid (2026-06-11): every QC-failed Step-2 attempt's image is
-- copied to a new private 'qc-failed' bucket by the orchestrator BEFORE the pod
-- overwrites the deterministic .../step2.jpg object on the next attempt. The
-- web Image-debug panel shows each failed attempt (image + its Qwen prompt +
-- the QC issues) so bad composites can be eyeballed during prompt tuning.
-- Remove at deployment time: drop the bucket + policy + qc_checks.step2_prompt,
-- and the _archive_qc_fail call in orchestrator/run_pipeline.py.
--
-- The orchestrator writes with the SERVICE key (bypasses RLS); the web only
-- READS (signed URLs), so a select policy is enough.
-- Idempotent. Does NOT alter any existing policy or column.
-- ============================================================================

-- 1) the private bucket
insert into storage.buckets (id, name, public)
values ('qc-failed', 'qc-failed', false)
on conflict (id) do nothing;

-- 2) tenant-scoped READ policy (same pattern as 017; path = '<tenant_id>/...').
--    NOTE: no `alter table storage.objects enable row level security` — RLS is
--    already on and the SQL-editor role isn't the table owner (errors 42501).
drop policy if exists st_qc_failed_read on storage.objects;
create policy st_qc_failed_read on storage.objects
  for select to authenticated
  using (
    bucket_id = 'qc-failed'
    and (storage.foldername(name))[1] = public.current_tenant_id()::text
  );

-- 3) the exact Qwen prompt that produced the attempt being judged, stored next
--    to its verdict (also in image_generations once the pod logs per-attempt,
--    but this keeps the QC audit row self-contained).
alter table public.qc_checks add column if not exists step2_prompt text;
