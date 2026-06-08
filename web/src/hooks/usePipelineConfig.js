import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// Per-tenant run controls -> public.tenant_pipeline_config (one row per tenant,
// tenant_id PK). The web writes this; the orchestrator (run_pipeline.py) reads it.
// Proven defaults already live in the schema; the form just overrides them.
const EMPTY = {
    creation_mode: 'all',          // 'all' | 'new_only' | 'specific'
    target_account_id: null,       // required when creation_mode='specific'
    num_videos_per_account: 1,
    video_mode: 'multishot',       // 'multishot' | 'silentfirst'
    video_duration_seconds: 10,
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
};

export function isPipelineConfigComplete(c) {
    if (!c) return false;
    if (Number(c.num_videos_per_account) < 1) return false;
    if (c.creation_mode === 'specific' && !c.target_account_id) return false;
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
            target_account_id: draft.creation_mode === 'specific' ? (draft.target_account_id || null) : null,
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
