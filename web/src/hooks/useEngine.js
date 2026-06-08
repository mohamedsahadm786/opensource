import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// READ-ONLY view of the learning/tuning engine for the current tenant. All of
// these are written by the orchestrator/recompute job; the web only displays.
//   • v_tenant_exploration_progress — exploration coverage (per-tenant view)
//   • tenant_learning_state         — phase (exploration|active) + engine switch
//   • attribute_stats               — learned per-attribute estimates
//   • tuning_suggestions            — prompt/script edits awaiting review
export function useEngine(tenantId) {
    const [state, setState] = useState({ progress: null, learning: null, stats: [], suggestions: [] });
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (!tenantId) { setStatus('ready'); return; }
        setStatus('loading');
        setError(null);
        try {
            const [progRes, learnRes, statsRes, tuneRes] = await Promise.all([
                supabase.from('v_tenant_exploration_progress').select('*').eq('tenant_id', tenantId).maybeSingle(),
                supabase.from('tenant_learning_state').select('*').eq('tenant_id', tenantId).maybeSingle(),
                supabase.from('attribute_stats')
                    .select('attribute_key, dimension, kind, n, passes, estimate, context_key')
                    .eq('tenant_id', tenantId)
                    .order('n', { ascending: false })
                    .limit(200),
                supabase.from('tuning_suggestions')
                    .select('scope_type, scope_key, dimension, cause, suggested_edit, status, score_delta, evidence_n')
                    .eq('tenant_id', tenantId)
                    .order('updated_at', { ascending: false })
                    .limit(100),
            ]);
            const err = progRes.error || learnRes.error || statsRes.error || tuneRes.error;
            if (err) throw err;
            setState({
                progress: progRes.data || null,
                learning: learnRes.data || null,
                stats: statsRes.data || [],
                suggestions: tuneRes.data || [],
            });
            setStatus('ready');
        } catch (err) {
            console.error('[Alluvi] engine load failed', err);
            setError(err);
            setStatus('error');
        }
    }, [tenantId]);

    useEffect(() => { load(); }, [load]);

    return { ...state, status, error, reload: load };
}
