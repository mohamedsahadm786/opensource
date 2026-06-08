import { useCallback, useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// Resolves the current tenant context for a MEMBER.
//   • super_admin → tenantId null, onboarded true (uses its own console)
//   • member      → tenantId from the JWT (app_metadata.tenant_id, set by the
//                   provision-tenant Edge Function). Loads the tenants row.
//
// onboarded lives in tenants.settings.onboarded (a jsonb flag — no schema change),
// flipped true when the tenant finishes the setup page (config + key + product).
export function useTenant(user) {
    const isAdmin = user?.kind === 'super_admin';
    const tenantId = user?.kind === 'member' ? (user.tenantId || null) : null;

    const [profile, setProfile] = useState(null);   // the tenants row
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        if (isAdmin) { setProfile(null); setStatus('ready'); return; }
        if (!tenantId) { setProfile(null); setStatus('loading'); return; } // provisioning in flight
        setStatus('loading');
        setError(null);
        const { data, error: selErr } = await supabase
            .from('tenants').select('*').eq('id', tenantId).maybeSingle();
        if (selErr) {
            console.error('[Alluvi] tenant load failed', selErr);
            setError(selErr);
            setStatus('error');
            return;
        }
        setProfile(data || null);
        setStatus('ready');
    }, [isAdmin, tenantId]);

    useEffect(() => { load(); }, [load]);

    // Persist company + brand + script config (the jsonb blobs the pipeline reads).
    const saveTenantConfig = useCallback(async (payload) => {
        if (!tenantId) throw new Error('No tenant context.');
        const set = (k, v) => (v === undefined ? {} : { [k]: v });
        const patch = {
            ...(payload.name !== undefined ? { name: payload.name?.trim() || null } : {}),
            ...(payload.slug !== undefined ? { slug: payload.slug?.trim() || null } : {}),
            ...(payload.email !== undefined ? { email: payload.email?.trim() || null } : {}),
            ...set('plan', payload.plan),
            ...set('brand_config', payload.brand_config),
            ...set('script_company_info', payload.script_company_info),
            ...set('script_directives', payload.script_directives),
            ...set('gpu_provider', payload.gpu_provider),
            ...(payload.gpu_host !== undefined ? { gpu_host: payload.gpu_host?.trim() || null } : {}),
            ...set('gpu_url_template', payload.gpu_url_template),
            updated_at: new Date().toISOString(),
        };
        const { data, error: err } = await supabase
            .from('tenants').update(patch).eq('id', tenantId).select().single();
        if (err) throw err;
        setProfile(data);
        return data;
    }, [tenantId]);

    // Store the Anthropic key in Vault (server-side; never a column).
    const storeAnthropicKey = useCallback(async (key) => {
        if (!key?.trim()) return;
        const { data, error: err } = await supabase.functions.invoke('store-tenant-secret', {
            method: 'POST', body: { key: key.trim() },
        });
        if (err) throw err;
        if (!data?.ok) throw new Error(data?.error || 'Could not store the Anthropic key.');
        await load();
    }, [load]);

    // Flip the onboarded flag once setup is complete.
    const markOnboarded = useCallback(async () => {
        if (!tenantId) throw new Error('No tenant context.');
        const settings = { ...(profile?.settings || {}), onboarded: true };
        const { data, error: err } = await supabase
            .from('tenants').update({ settings, updated_at: new Date().toISOString() })
            .eq('id', tenantId).select().single();
        if (err) throw err;
        setProfile(data);
    }, [tenantId, profile]);

    const onboarded = isAdmin || Boolean(profile?.settings?.onboarded);

    return {
        tenantId, isAdmin, onboarded, profile, status, error,
        reload: load, saveTenantConfig, storeAnthropicKey, markOnboarded,
    };
}
