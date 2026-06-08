// Super-admin data access — goes through the `admin-data` Edge Function, NOT
// direct table reads.
//
// Why: every table is RLS-scoped to the caller's own tenant, so the anon key
// can never see cross-tenant data. The super-admin console therefore routes all
// of its reads/writes through a service-role Edge Function that is gated by a
// shared secret (ADMIN_SECRET — the same hard-coded admin password, configured
// server-side on the function). The service key never reaches the browser.

import { supabase } from './supabase.js';
import { ADMIN_SECRET } from './constants.js';

// action: 'overview' | 'set_status' | 'log_event' | 'list_events'
export async function adminInvoke(action, payload = {}) {
    const { data, error } = await supabase.functions.invoke('admin-data', {
        method: 'POST',
        body: { action, secret: ADMIN_SECRET, ...payload },
    });
    if (error) throw error;
    if (data && data.ok === false) throw new Error(data.error || 'admin-data failed');
    return data;
}
