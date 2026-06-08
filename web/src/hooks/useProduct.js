import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { signedUrl } from '../lib/assets.js';

// The ONE product per tenant (products has UNIQUE(tenant_id)). The web writes
// products + uploads the real product photo to the private `products` bucket via
// the standard pattern: upload -> media_assets row -> products.reference_asset_id.
export function useProduct(tenantId) {
    const [product, setProduct] = useState(null);
    const [photoUrl, setPhotoUrl] = useState(null); // signed URL of the reference photo
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
        // resolve the reference photo to a signed URL for preview
        if (data?.reference_asset_id) {
            const { data: asset } = await supabase
                .from('media_assets').select('bucket, path').eq('id', data.reference_asset_id).maybeSingle();
            setPhotoUrl(asset ? await signedUrl(asset.bucket, asset.path) : null);
        } else {
            setPhotoUrl(null);
        }
        setStatus('ready');
    }, [tenantId]);

    useEffect(() => { load(); }, [load]);

    // Upload a product photo -> media_assets row -> returns its id.
    const uploadPhoto = useCallback(async (file) => {
        const ext = (file.name.split('.').pop() || 'jpg').toLowerCase();
        const path = `${tenantId}/products/product-${Date.now()}.${ext}`;
        const { error: upErr } = await supabase.storage
            .from('products').upload(path, file, { contentType: file.type || 'image/jpeg', upsert: true });
        if (upErr) throw upErr;
        const { data: asset, error: maErr } = await supabase
            .from('media_assets')
            .insert({
                tenant_id: tenantId, kind: 'product_ref', bucket: 'products', path,
                mime_type: file.type || 'image/jpeg', bytes: file.size,
            })
            .select('id')
            .single();
        if (maErr) throw maErr;
        return asset.id;
    }, [tenantId]);

    // Upsert the product (+ optional new photo). mask_prompt is required.
    const save = useCallback(async (payload, photoFile) => {
        if (!tenantId) throw new Error('No tenant context.');
        let referenceAssetId;
        if (photoFile) referenceAssetId = await uploadPhoto(photoFile);

        const row = {
            tenant_id: tenantId,
            product_key: payload.product_key?.trim() || null,
            name: payload.name?.trim() || null,
            peptide_compound: payload.peptide_compound?.trim() || null,
            dose: payload.dose?.trim() || null,
            category: payload.category?.trim() || null,
            packaging: payload.packaging ?? {},
            key_benefits: payload.key_benefits ?? [],
            target_audience: payload.target_audience ?? {},
            do_not_claim: payload.do_not_claim ?? [],
            mask_prompt: payload.mask_prompt?.trim()
                || 'white product box package, white rectangular box',
            qc_max_retries: payload.qc_max_retries == null ? 3 : Number(payload.qc_max_retries),
            product_info: payload.product_info ?? {},
            ...(referenceAssetId ? { reference_asset_id: referenceAssetId } : {}),
            updated_at: new Date().toISOString(),
        };
        const { data, error: err } = await supabase
            .from('products').upsert(row, { onConflict: 'tenant_id' }).select().single();
        if (err) throw err;
        setProduct(data);
        await load(); // refresh signed photo URL
        return data;
    }, [tenantId, uploadPhoto, load]);

    return { product, photoUrl, status, error, reload: load, save };
}
