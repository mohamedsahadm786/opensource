import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { RATING_CONFIG } from '../lib/ratingConfig.js';

// Load + save the QA rating for one generation (output). One row per output_id
// (upsert on output_id). The write shape is a HARD CONTRACT with the learning
// engine (run_recompute.py's explode()):
//
//   { tenant_id, output_id, decision: 'accept'|'reject'|'flag',
//     image_rated, video_rated, rated_by,
//     image: { gates:{<id>:{result, [auto], [disputed]}}, scores:{<id>:1..5}, [notes] },
//     video: { gates:{<id>:{result}},                     scores:{<id>:1..5}, [notes] } }
//
// Scores are OMITTED when left unrated. Auto image gates also carry auto:true +
// disputed. tenant_id must be set explicitly (asset_ratings is NOT NULL +
// RLS-checked; there is no tenant trigger on this table).

const AUTO_IDS = new Set(
    [...RATING_CONFIG.image.gates, ...RATING_CONFIG.video.gates]
        .filter(g => g.control === 'auto_badge')
        .map(g => g.id),
);

function toContract(secDraft) {
    const gates = {};
    for (const [id, g] of Object.entries(secDraft.gates || {})) {
        if (!g || !g.result) continue;
        gates[id] = AUTO_IDS.has(id)
            ? { result: g.result, auto: true, disputed: Boolean(g.disputed) }
            : { result: g.result };
    }
    const scores = {};
    for (const [id, v] of Object.entries(secDraft.scores || {})) {
        if (v != null) scores[id] = v;                 // omit unrated
    }
    const notes = {};
    for (const [id, t] of Object.entries(secDraft.notes || {})) {
        if (t && t.trim()) notes[id] = t.trim();
    }
    const out = { gates, scores };
    if (Object.keys(notes).length) out.notes = notes;
    return out;
}

function hasInput(secDraft) {
    return Object.values(secDraft.gates || {}).some(g => g?.result)
        || Object.values(secDraft.scores || {}).some(v => v != null);
}

export function useAssetRating(outputId) {
    const [existing, setExisting] = useState(null);
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (!outputId) { setExisting(null); setStatus('ready'); return; }
        setStatus('loading');
        setError(null);
        const { data, error: err } = await supabase
            .from('asset_ratings').select('*').eq('output_id', outputId).maybeSingle();
        if (err) {
            console.error('[Alluvi] rating load failed', err);
            setError(err);
            setStatus('error');
            return;
        }
        setExisting(data || null);
        setStatus('ready');
    }, [outputId]);

    useEffect(() => { load(); }, [load]);

    // draft: { triage:'Accept'|'Reject'|'Flag', image:{gates,scores,notes}, video:{...} }
    // context: { tenant_id, output_id, rater_id }
    const save = useCallback(async (draft, context) => {
        if (!context.tenant_id) throw new Error('Missing tenant context for rating.');
        const row = {
            tenant_id: context.tenant_id,
            output_id: context.output_id,
            rated_by: context.rater_id ?? null,
            decision: draft.triage ? draft.triage.toLowerCase() : null,
            image: toContract(draft.image),
            video: toContract(draft.video),
            image_rated: Boolean(draft.triage) || hasInput(draft.image),
            video_rated: hasInput(draft.video),
            updated_at: new Date().toISOString(),
        };
        const { data, error: err } = await supabase
            .from('asset_ratings').upsert(row, { onConflict: 'output_id' }).select().single();
        if (err) throw err;
        setExisting(data);
        return data;
    }, []);

    return { existing, status, error, reload: load, save };
}
