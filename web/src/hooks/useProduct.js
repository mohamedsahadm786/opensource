import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { signedUrl } from '../lib/assets.js';

// The ONE product per tenant (products has UNIQUE(tenant_id)). The web writes
// products + uploads the real product photo to the private `products` bucket via
// the standard pattern: upload -> media_assets row -> products.reference_asset_id.
export function useProduct(tenantId) {
    const [product, setProduct] = useState(null);
    const [photoUrl, setPhotoUrl] = useState(null); // signed URL of the front reference photo
    const [anglePhotoUrl, setAnglePhotoUrl] = useState(null); // signed URL of the optional 3/4-angle photo
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (!tenantId) { setProduct(null); setStatus('ready'); return; }
        setStatus('loading');
        setError(null);
        const { data, error: err } = await supabase
            .from('products').select('*').eq('tenant_id', tenantId).maybeSingle();
        if (err) {
            console.error('[Alluvi] product load failed', err);
            setError(err);
            setStatus('error');
            return;
        }
        setProduct(data || null);
        // resolve the reference photos to signed URLs for preview
        if (data?.reference_asset_id) {
            const { data: asset } = await supabase
                .from('media_assets').select('bucket, path').eq('id', data.reference_asset_id).maybeSingle();
            setPhotoUrl(asset ? await signedUrl(asset.bucket, asset.path) : null);
        } else {
            setPhotoUrl(null);
        }
        if (data?.reference_angle_asset_id) {
            const { data: asset } = await supabase
                .from('media_assets').select('bucket, path').eq('id', data.reference_angle_asset_id).maybeSingle();
            setAnglePhotoUrl(asset ? await signedUrl(asset.bucket, asset.path) : null);
        } else {
            setAnglePhotoUrl(null);
        }
        setStatus('ready');
    }, [tenantId]);

    useEffect(() => { load(); }, [load]);

    // Upload a product photo -> media_assets row -> returns its id.
    // angle=true uploads the optional second (3/4 angled) reference.
    const uploadPhoto = useCallback(async (file, angle = false) => {
        const ext = (file.name.split('.').pop() || 'jpg').toLowerCase();
        const stem = angle ? 'product-angle' : 'product';
        const path = `${tenantId}/products/${stem}-${Date.now()}.${ext}`;
        const { error: upErr } = await supabase.storage
            .from('products').upload(path, file, { contentType: file.type || 'image/jpeg', upsert: true });
        if (upErr) throw upErr;
        const { data: asset, error: maErr } = await supabase
            .from('media_assets')
            .insert({
                tenant_id: tenantId, kind: angle ? 'product_ref_angle' : 'product_ref', bucket: 'products', path,
                mime_type: file.type || 'image/jpeg', bytes: file.size,
            })
            .select('id')
            .single();
        if (maErr) throw maErr;
        return asset.id;
    }, [tenantId]);

    // Upsert the product's RAW inputs (+ optional new photo). The structured
    // name / product_info / packaging are derived afterwards by convert-briefs,
    // so we never overwrite them here: product_key & name are preserved (or get
    // a placeholder on first save to satisfy NOT NULL), and product_info /
    // packaging are simply omitted from the payload (left untouched on update).
    const save = useCallback(async (payload, photoFile, anglePhotoFile) => {
        if (!tenantId) throw new Error('No tenant context.');
        let referenceAssetId;
        if (photoFile) referenceAssetId = await uploadPhoto(photoFile);
        let angleAssetId;
        if (anglePhotoFile) angleAssetId = await uploadPhoto(anglePhotoFile, true);

        const row = {
            tenant_id: tenantId,
            product_key: product?.product_key || 'product',
            name: product?.name || 'Product',   // placeholder; convert-briefs sets the real name
            mask_prompt: payload.mask_prompt?.trim()
                || 'white product box package, white rectangular box',
            qc_max_retries: payload.qc_max_retries == null ? 3 : Number(payload.qc_max_retries),
            product_brief_text: payload.product_brief_text?.trim() || null,
            ...(referenceAssetId ? { reference_asset_id: referenceAssetId } : {}),
            ...(angleAssetId ? { reference_angle_asset_id: angleAssetId } : {}),
            updated_at: new Date().toISOString(),
        };
        const { data, error: err } = await supabase
            .from('products').upsert(row, { onConflict: 'tenant_id' }).select().single();
        if (err) throw err;
        setProduct(data);
        await load(); // refresh signed photo URLs
        return data;
    }, [tenantId, uploadPhoto, load, product]);

    return { product, photoUrl, anglePhotoUrl, status, error, reload: load, save };
}
