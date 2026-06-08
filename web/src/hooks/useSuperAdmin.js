import { useCallback, useEffect, useState } from 'react';
import { adminInvoke } from '../lib/adminApi.js';
import { computeCost } from '../lib/cost.js';

const maxDate = (a, b) => {
    if (!a) return b || null;
    if (!b) return a;
    return a > b ? a : b; // ISO strings compare lexicographically
};

// Cross-tenant intelligence for the Super Admin console.
//
// RLS hides cross-tenant data from the anon key, so this pulls everything via
// the service-role `admin-data` Edge Function (action: 'overview'), then
// aggregates client-side by walking the chain:
//   account.tenant_id -> persona.tiktok_account_id -> output.persona_id -> video.output_id
export function useSuperAdmin() {
    const [tenants, setTenants] = useState([]);
    const [totals, setTotals] = useState({
        tenants: 0, onboarded: 0, pending: 0, accounts: 0, images: 0, videos: 0, cost: 0,
    });
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        setStatus('loading');
        setError(null);
        try {
            const data = await adminInvoke('overview');
            const profiles = data.profiles || [];
            const accounts = data.accounts || [];
            const personas = data.personas || [];
            const outputs = data.outputs || [];
            const videos = data.videos || [];

            // --- account-level rollup -------------------------------------
            const accountInfo = new Map();
            accounts.forEach(a => accountInfo.set(a.id, {
                id: a.id, tiktok_id: a.tiktok_id, name: a.name,
                tenant_id: a.tenant_id, created_at: a.created_at,
                personas: 0, images: 0, videos: 0, qcPass: 0, qcSkip: 0,
                last: a.created_at || null,
            }));

            const personaAccount = new Map();
            personas.forEach(p => {
                personaAccount.set(p.id, p.tiktok_account_id);
                const acc = accountInfo.get(p.tiktok_account_id);
                if (acc) { acc.personas += 1; acc.last = maxDate(acc.last, p.created_at); }
            });

            const outputAccount = new Map();
            outputs.forEach(o => {
                const accId = personaAccount.get(o.persona_id);
                outputAccount.set(o.id, accId);
                const acc = accountInfo.get(accId);
                if (acc) {
                    acc.images += 1;
                    if (o.qc_status === 'pass' || o.qc_status === 'passed') acc.qcPass += 1;
                    else if (o.qc_status === 'skipped') acc.qcSkip += 1;
                    acc.last = maxDate(acc.last, o.created_at);
                }
            });

            videos.forEach(v => {
                const acc = accountInfo.get(outputAccount.get(v.output_id));
                if (acc) { acc.videos += 1; acc.last = maxDate(acc.last, v.created_at); }
            });

            // --- tenant-level rollup --------------------------------------
            const tenantAgg = new Map();
            const blank = () => ({ accounts: 0, personas: 0, images: 0, videos: 0, qcPass: 0, qcSkip: 0, last: null, accountList: [] });
            for (const acc of accountInfo.values()) {
                if (!acc.tenant_id) continue;
                const t = tenantAgg.get(acc.tenant_id) || blank();
                t.accounts += 1; t.personas += acc.personas; t.images += acc.images; t.videos += acc.videos;
                t.qcPass += acc.qcPass; t.qcSkip += acc.qcSkip; t.last = maxDate(t.last, acc.last);
                t.accountList.push(acc);
                tenantAgg.set(acc.tenant_id, t);
            }

            // Normalize our tenants.status (active|paused|disabled) to the
            // console's vocabulary (active|suspended|removed) so the SuperAdmin
            // components work unchanged. The write path maps back (tenantAdmin.js).
            const normStatus = (s) => (s === 'paused' ? 'suspended' : s === 'disabled' ? 'removed' : (s || 'active'));
            const rows = profiles.map(p => {
                const t = tenantAgg.get(p.tenant_id) || blank();
                const cost = computeCost({ images: t.images, videos: t.videos });
                const accountList = [...t.accountList].sort((x, y) => String(y.last || '').localeCompare(String(x.last || '')));
                return { ...p, status: normStatus(p.status), accounts: t.accounts, personas: t.personas, images: t.images, videos: t.videos, qcPass: t.qcPass, qcSkip: t.qcSkip, lastActivity: t.last, cost, accountList };
            });
            rows.sort((x, y) => String(y.created_at || '').localeCompare(String(x.created_at || '')));

            // 'disabled' = removed tombstone — exclude from platform totals.
            const totalsNext = rows.reduce((acc, r) => {
                if (r.status === 'disabled' || r.status === 'removed') return acc;
                return {
                    tenants: acc.tenants + 1,
                    onboarded: acc.onboarded + (r.onboarded ? 1 : 0),
                    pending: acc.pending + (r.onboarded ? 0 : 1),
                    accounts: acc.accounts + r.accounts,
                    images: acc.images + r.images,
                    videos: acc.videos + r.videos,
                    cost: acc.cost + r.cost,
                };
            }, { tenants: 0, onboarded: 0, pending: 0, accounts: 0, images: 0, videos: 0, cost: 0 });

            setTenants(rows);
            setTotals(totalsNext);
            setStatus('ready');
        } catch (err) {
            console.error('[Alluvi] super-admin load failed', err);
            setError(err);
            setStatus('error');
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    return { tenants, totals, status, error, reload: load };
}
