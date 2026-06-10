import { adminInvoke } from './adminApi.js';

// Record a super-admin action against a tenant. Best-effort: a logging failure
// must never block the action itself. Written via the service-role admin-data
// Edge Function (impersonation_events has no anon policy).
async function logEvent(tenant, action) {
    try {
        // NOTE: the event verb travels as `event_action` — a payload key named
        // `action` collides with (and overwrites) the router's action in
        // adminInvoke's body spread, which silently broke all audit writes.
        await adminInvoke('log_event', {
            event_action: action,
            tenant_id: tenant.tenant_id,
            tenant_name: tenant.name || null,
            tenant_email: tenant.email || null,
        });
    } catch (err) {
        console.warn('[Alluvi] audit log failed', err);
    }
}

// Opening a tenant's interface ("Page" / view-as).
export const logImpersonation = (tenant) => logEvent(tenant, 'view_page');

// Lifecycle actions: 'suspend' | 'reactivate' | 'remove'.
export const logTenantAction = (tenant, action) => logEvent(tenant, action);
