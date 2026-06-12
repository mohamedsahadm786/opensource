import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// Per-tenant run controls -> public.tenant_pipeline_config (one row per tenant,
// tenant_id PK). The web writes this; the orchestrator (run_pipeline.py) reads it.
// Proven defaults already live in the schema; the form just overrides them.
const EMPTY = {
    creation_mode: 'all',          // 'all' | 'new_only' | 'specific'
    target_account_ids: [],        // jsonb array of tiktok_accounts.id (when 'specific')
    num_videos_per_account: '',    // REQUIRED — user must enter it (this gates the Run button)
    video_mode: 'multishot',       // 'multishot' | 'silentfirst'
    video_duration_seconds: '',    // REQUIRED — user must enter it (this gates the Run button)
    shot_seconds: 5,
    intro_seconds: 2,
    outro_seconds: 2,
    tail_seconds: 0,
    lips_expression: 2.0,
    inference_steps: 40,
    punch_in: 1.2,
    threshold: 70,
    seed: null,
    step_3_enabled: true,
    qc_enabled: true,
    realism_denoise: '',           // optional — empty = pod default (0.30)
    realism_lora_strength: '',     // optional — empty = pod default (0.7)
    wan_steps: '',                 // optional — empty = workflow default (20)
    wan_cfg_high: '',              // optional — empty = workflow default (3.5)
    wan_cfg_low: '',               // optional — empty = workflow default (3.5)
    interp_target_fps: '',         // optional — empty = off (native 16 fps)
};

export function isPipelineConfigComplete(c) {
    if (!c) return false;
    // num_videos_per_account + video_duration_seconds are required (no default),
    // so the Run button stays disabled until the user fills them in Run settings.
    if (!c.num_videos_per_account || Number(c.num_videos_per_account) < 1) return false;
    if (!c.video_duration_seconds || Number(c.video_duration_seconds) < 1) return false;
    if (c.creation_mode === 'specific' && (!Array.isArray(c.target_account_ids) || c.target_account_ids.length === 0)) return false;
    return Boolean(c.creation_mode) && Boolean(c.video_mode);
}

export function usePipelineConfig(tenantId) {
    const [config, setConfig] = useState(EMPTY);
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (!tenantId) { setConfig(EMPTY); setStatus('ready'); return; }
        setStatus('loading');
        setError(null);
        const { data, error: err } = await supabase
            .from('tenant_pipeline_config').select('*').eq('tenant_id', tenantId).maybeSingle();
        if (err) {
            console.error('[Alluvi] pipeline config load failed', err);
            setError(err);
            setStatus('error');
            return;
        }
        setConfig(data ? { ...EMPTY, ...data } : { ...EMPTY });
        setStatus('ready');
    }, [tenantId]);

    useEffect(() => { load(); }, [load]);

    const numOrNull = (v) => (v === '' || v == null ? null : Number(v));

    const save = useCallback(async (draft) => {
        if (!tenantId) throw new Error('No tenant context.');
        const row = {
            tenant_id: tenantId,
            creation_mode: draft.creation_mode || 'all',
            target_account_ids: draft.creation_mode === 'specific' ? (draft.target_account_ids || []) : [],
            num_videos_per_account: numOrNull(draft.num_videos_per_account) ?? 1,
            video_mode: draft.video_mode || 'multishot',
            video_duration_seconds: numOrNull(draft.video_duration_seconds) ?? 10,
            shot_seconds: numOrNull(draft.shot_seconds) ?? 5,
            intro_seconds: numOrNull(draft.intro_seconds) ?? 2,
            outro_seconds: numOrNull(draft.outro_seconds) ?? 2,
            tail_seconds: numOrNull(draft.tail_seconds) ?? 0,
            lips_expression: numOrNull(draft.lips_expression) ?? 2.0,
            inference_steps: numOrNull(draft.inference_steps) ?? 40,
            punch_in: numOrNull(draft.punch_in) ?? 1.2,
            threshold: numOrNull(draft.threshold) ?? 70,
            seed: numOrNull(draft.seed),
            step_3_enabled: Boolean(draft.step_3_enabled),
            qc_enabled: Boolean(draft.qc_enabled),
            realism_denoise: numOrNull(draft.realism_denoise),
            realism_lora_strength: numOrNull(draft.realism_lora_strength),
            wan_steps: numOrNull(draft.wan_steps),
            wan_cfg_high: numOrNull(draft.wan_cfg_high),
            wan_cfg_low: numOrNull(draft.wan_cfg_low),
            interp_target_fps: numOrNull(draft.interp_target_fps),
            updated_at: new Date().toISOString(),
        };
        const { data, error: err } = await supabase
            .from('tenant_pipeline_config').upsert(row, { onConflict: 'tenant_id' }).select().single();
        if (err) throw err;
        setConfig({ ...EMPTY, ...data });
        return data;
    }, [tenantId]);

    return { config, status, error, isComplete: isPipelineConfigComplete(config), reload: load, save };
}
