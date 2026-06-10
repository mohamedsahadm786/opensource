-- 020: optional SECOND product reference photo (a 3/4 angled view).
-- Step 2 (Qwen-Image-Edit) passes it as Picture 3 so the model understands the
-- box's 3D depth (front face fidelity comes from the main front photo).
-- Nullable + additive: tenants without it keep the exact current behavior.
alter table public.products
  add column if not exists reference_angle_asset_id uuid
  references public.media_assets(id) on delete set null;
