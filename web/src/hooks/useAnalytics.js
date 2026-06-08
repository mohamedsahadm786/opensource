import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// Pulls the tenant's pipeline (lean columns) so the analytics panel can compute
// every breakdown client-side. RLS scopes reads to the caller's tenant; we also
// pass the explicit tenant filter as defense-in-depth. Fine for MVP scale.
export function useAnalytics(tenantId = null) {
    const [data, setData] = useState({ accounts: [], personas: [], outputs: [], videos: [] });
    const [status, setStatus] = useState('loading');   // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        setStatus('loading');
        setError(null);
        try {
            const scoped = (q) => (tenantId ? q.eq('tenant_id', tenantId) : q);
            const [accountsRes, personasRes, outputsRes, videosRes] = await Promise.all([
                scoped(supabase.from('tiktok_accounts')
                    .select('id, tiktok_id, name, gender, age, country, language, created_at')),
                scoped(supabase.from('personas').select('id, tiktok_account_id, created_at')),
                scoped(supabase.from('outputs')
                    .select('id, persona_id, scenario_id, scenario_key, scenario_title, qc_status, qc_reason, attempts, created_at')),
                scoped(supabase.from('videos').select('id, output_id, scenario_id, created_at')),
            ]);
            const err = accountsRes.error || personasRes.error || outputsRes.error || videosRes.error;
            if (err) throw err;
            setData({
                accounts: accountsRes.data || [],
                personas: personasRes.data || [],
                outputs: outputsRes.data || [],
                videos: videosRes.data || [],
            });
            setStatus('ready');
        } catch (err) {
            console.error('[Alluvi] analytics load failed', err);
            setError(err);
            setStatus('error');
        }
    }, [tenantId]);

    useEffect(() => { load(); }, [load]);

    return { data, status, error, reload: load };
}
