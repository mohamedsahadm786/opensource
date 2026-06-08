import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { TABLE } from '../lib/constants.js';
import { isMissingTableError } from '../lib/utils.js';

// Fields that feed the persona-appearance builder. Changing any of them
// invalidates the locked face, so we delete the persona — our schema then
// cascades the deletion down personas -> outputs -> videos automatically (all
// FKs are ON DELETE CASCADE), and the next pipeline run rebuilds from the new
// identity. Editing only the @handle does NOT trigger this.
const IDENTITY_FIELDS = ['name', 'gender', 'age', 'country', 'language', 'identity_factors'];

function identityChanged(prev, next) {
    if (!prev) return false;
    return IDENTITY_FIELDS.some((k) => {
        if (k === 'age') return Number(prev[k]) !== Number(next[k]);
        if (k === 'identity_factors') {
            return JSON.stringify(prev[k] || {}) !== JSON.stringify(next[k] || {});
        }
        return (prev[k] ?? '') !== (next[k] ?? '');
    });
}

// Deleting the persona is enough — DB cascade removes its outputs/videos.
async function invalidatePersona(accountId) {
    const { error } = await supabase
        .from('personas').delete().eq('tiktok_account_id', accountId);
    if (error) throw error;
}

// tenantId: the member's tenant (from the JWT). RLS scopes reads/writes to it,
// and new rows MUST carry it (the tenant_rw policy's WITH CHECK requires it).
export function useAccounts(tenantId = null) {
    const [accounts, setAccounts] = useState([]);
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        setStatus('loading');
        setError(null);
        let query = supabase.from(TABLE).select('*').order('created_at', { ascending: false });
        if (tenantId) query = query.eq('tenant_id', tenantId);
        const { data, error: err } = await query;
        if (err) {
            console.error('[Alluvi] accounts load failed', err);
            setError({ raw: err, missingTable: isMissingTableError(err) });
            setStatus('error');
            return;
        }
        setAccounts(data || []);
        setStatus('ready');
    }, [tenantId]);

    useEffect(() => { load(); }, [load]);

    const create = useCallback(async (payload) => {
        const row = tenantId ? { ...payload, tenant_id: tenantId } : payload;
        const { data, error: err } = await supabase.from(TABLE).insert(row).select().single();
        if (err) throw err;
        setAccounts(prev => [data, ...prev]);
        return data;
    }, [tenantId]);

    const update = useCallback(async (id, payload) => {
        const prev = accounts.find(a => a.id === id);
        const cascaded = identityChanged(prev, payload);
        // Invalidate first: if the row update fails, the next run just regenerates
        // from the unchanged identity (no stale persona pinned to a new identity).
        if (cascaded) await invalidatePersona(id);
        const { data, error: err } = await supabase
            .from(TABLE).update(payload).eq('id', id).select().single();
        if (err) throw err;
        setAccounts(prev => prev.map(a => (a.id === id ? data : a)));
        return { data, cascaded };
    }, [accounts]);

    const remove = useCallback(async (id) => {
        // Our schema cascades personas -> outputs -> videos on delete, so a single
        // delete of the account row is enough.
        const { error: err } = await supabase.from(TABLE).delete().eq('id', id);
        if (err) throw err;
        setAccounts(prev => prev.filter(a => a.id !== id));
    }, []);

    return { accounts, status, error, reload: load, create, update, remove };
}
