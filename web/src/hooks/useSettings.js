import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// Per-tenant settings:
//   • Anthropic key  -> Supabase Vault via the store-tenant-secret Edge Function
//     (write-only; never read back into the browser). We only surface WHETHER one
//     is set (tenants.anthropic_secret_name).
//   • GPU connection -> tenants.gpu_provider / gpu_host / gpu_url_template
//     (gpu_host is the RunPod pod-id that changes on restart).
export function useSettings(tenantId) {
    const [gpu, setGpu] = useState({ gpu_provider: 'runpod', gpu_host: '', gpu_url_template: '' });
    const [hasAnthropicKey, setHasAnthropicKey] = useState(false);
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (!tenantId) { setStatus('ready'); return; }
        setStatus('loading');
        setError(null);
        const { data, error: err } = await supabase
            .from('tenants')
            .select('gpu_provider, gpu_host, gpu_url_template, anthropic_secret_name')
            .eq('id', tenantId)
            .maybeSingle();
        if (err) {
            console.error('[Alluvi] settings load failed', err);
            setError({ raw: err });
            setStatus('error');
            return;
        }
        setGpu({
            gpu_provider: data?.gpu_provider || 'runpod',
            gpu_host: data?.gpu_host || '',
            gpu_url_template: data?.gpu_url_template || 'https://{host}-{port}.proxy.runpod.net',
        });
        setHasAnthropicKey(Boolean(data?.anthropic_secret_name));
        setStatus('ready');
    }, [tenantId]);

    useEffect(() => { load(); }, [load]);

    const saveGpu = useCallback(async (next) => {
        if (!tenantId) throw new Error('No tenant context.');
        const patch = {
            gpu_provider: next.gpu_provider || 'runpod',
            gpu_host: next.gpu_host?.trim() || null,
            gpu_url_template: next.gpu_url_template?.trim() || null,
            updated_at: new Date().toISOString(),
        };
        const { error: err } = await supabase.from('tenants').update(patch).eq('id', tenantId);
        if (err) throw err;
        setGpu({ ...next });
    }, [tenantId]);

    const saveAnthropicKey = useCallback(async (key) => {
        if (!key?.trim()) return;
        const { data, error: err } = await supabase.functions.invoke('store-tenant-secret', {
            method: 'POST', body: { key: key.trim() },
        });
        if (err) throw err;
        if (!data?.ok) throw new Error(data?.error || 'Could not store the Anthropic key.');
        setHasAnthropicKey(true);
    }, []);

    return { gpu, hasAnthropicKey, status, error, reload: load, saveGpu, saveAnthropicKey };
}
