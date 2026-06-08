import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { signedUrls } from '../lib/assets.js';

// Every (scene image + optional video) pair for one tiktok account, latest-first.
//
// Buckets are PRIVATE: the DB stores asset references (media_assets rows), not
// URLs. We resolve the final realism image (outputs.step3_asset_id, falling back
// to final_image_asset_id / step2_asset_id) and the final video
// (videos.final_video_asset_id) to short-lived SIGNED URLs at read time.
//
// Output row shape matches what PublishingPanel / RatingWorkspace expect:
//   { id, persona_id, created_at, scenario_id, scenario_title, qc_status,
//     image_storage_url, image_file_id:null, image_url:null,
//     video: { id, storage_url, dialogue, prompt_used } | null }
export function usePublishingForAccount(accountId /*, tenantId */) {
    const [rows, setRows] = useState([]);
    const [status, setStatus] = useState('idle');   // 'idle' | 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (!accountId) { setRows([]); setStatus('idle'); return; }
        setStatus('loading');
        setError(null);

        const { data: persona, error: pErr } = await supabase
            .from('personas').select('id').eq('tiktok_account_id', accountId).maybeSingle();
        if (pErr) { setError(pErr); setStatus('error'); return; }
        if (!persona) { setRows([]); setStatus('ready'); return; }

        const { data: outputs, error: oErr } = await supabase
            .from('outputs')
            .select(`
                id, created_at, scenario_id, scenario_key, scenario_title, persona_id, qc_status, status,
                step3_asset_id, step2_asset_id, final_image_asset_id,
                videos ( id, final_video_asset_id, dialogue, wan_motion_prompt, created_at, status )
            `)
            .eq('persona_id', persona.id)
            .order('created_at', { ascending: false });
        if (oErr) { setError(oErr); setStatus('error'); return; }

        const list = (outputs || []).map((o) => {
            const video = Array.isArray(o.videos) ? o.videos[0] : o.videos;
            return {
                _imageAssetId: o.step3_asset_id || o.final_image_asset_id || o.step2_asset_id || null,
                _videoAssetId: video?.final_video_asset_id || null,
                row: {
                    id: o.id,
                    persona_id: o.persona_id,
                    created_at: o.created_at,
                    scenario_id: o.scenario_key || o.scenario_id,
                    scenario_title: o.scenario_title,
                    qc_status: o.qc_status,
                    image_file_id: null,
                    image_url: null,
                    image_storage_url: null,
                    video: video ? {
                        id: video.id,
                        storage_url: null,
                        dialogue: video.dialogue || null,
                        prompt_used: video.wan_motion_prompt || null,
                    } : null,
                },
            };
        });

        // Resolve all referenced media_assets -> { id: {bucket, path} } in one query.
        const assetIds = [...new Set(list.flatMap(x => [x._imageAssetId, x._videoAssetId]).filter(Boolean))];
        const assetMap = {};
        if (assetIds.length) {
            const { data: assets } = await supabase
                .from('media_assets').select('id, bucket, path').in('id', assetIds);
            (assets || []).forEach(a => { assetMap[a.id] = a; });
        }

        // Batch-sign per bucket.
        const byBucket = {};
        for (const a of Object.values(assetMap)) {
            (byBucket[a.bucket] ||= []).push(a.path);
        }
        const urlByPath = {};
        for (const [bucket, paths] of Object.entries(byBucket)) {
            Object.assign(urlByPath, await signedUrls(bucket, paths));
        }
        const urlFor = (assetId) => {
            const a = assetMap[assetId];
            return a ? urlByPath[a.path] || null : null;
        };

        const flat = list.map((x) => {
            const r = x.row;
            r.image_storage_url = urlFor(x._imageAssetId);
            if (r.video) r.video.storage_url = urlFor(x._videoAssetId);
            return r;
        });
        setRows(flat);
        setStatus('ready');
    }, [accountId]);

    useEffect(() => { load(); }, [load]);

    // No Drive legacy in this build — assets are always present as signed URLs.
    // These stubs keep the component API stable (they are never actually called
    // because image_storage_url / video.storage_url are populated above).
    const noop = useCallback(async () => { throw new Error('mirror_unavailable'); }, []);

    return { rows, status, error, reload: load, mirrorVideo: noop, mirrorImage: noop };
}
