import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// Every prompt that produced one output (Brief v2 §7), read-only.
//
// Linkage in THIS pipeline (stage_executions / stage_execution_id are NOT
// populated, so we join on the asset/video ids the orchestrator actually sets):
//   image stages → image_generations.output_asset_id ∈ {outputs.step1/step2/step3_asset_id}
//                  (image_generations.prompt is the full positive prompt fed to the model)
//   video shots  → media_generations.video_id == videos.id
export function useOutputDebug(outputId, videoId) {
    const [data, setData] = useState({ images: [], media: [], llm: [] });
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (!outputId) { setStatus('ready'); return; }
        setStatus('loading');
        setError(null);
        try {
            // Resolve the output's per-stage image assets, then fetch the
            // image_generations that produced them.
            const { data: out } = await supabase
                .from('outputs')
                .select('step1_asset_id, step2_asset_id, step3_asset_id')
                .eq('id', outputId)
                .maybeSingle();
            const assetIds = [out?.step1_asset_id, out?.step2_asset_id, out?.step3_asset_id].filter(Boolean);

            const [imgRes, medRes] = await Promise.all([
                assetIds.length
                    ? supabase.from('image_generations')
                        .select('stage_name, prompt, negative_prompt, mask_prompt, seed, cfg, created_at')
                        .in('output_asset_id', assetIds)
                        .order('created_at', { ascending: true })
                    : Promise.resolve({ data: [] }),
                videoId
                    ? supabase.from('media_generations')
                        .select('stage_name, prompt, negative_prompt, params, created_at')
                        .eq('video_id', videoId)
                        .order('created_at', { ascending: true })
                    : Promise.resolve({ data: [] }),
            ]);
            // llm_calls have no per-output link in this pipeline (no output id,
            // stage_execution_id is null), so they can't be scoped to one output.
            // The real per-stage prompt lives on image_generations.prompt above.
            setData({ images: imgRes.data || [], media: medRes.data || [], llm: [] });
            setStatus('ready');
        } catch (err) {
            console.error('[Alluvi] debug load failed', err);
            setError(err);
            setStatus('error');
        }
    }, [outputId, videoId]);

    useEffect(() => { load(); }, [load]);

    return { ...data, status, error, reload: load };
}
