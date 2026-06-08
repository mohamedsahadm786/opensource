import { adminInvoke } from './adminApi.js';

// Super-admin tenant lifecycle control, via the service-role admin-data Edge
// Function. The console speaks active|suspended|removed; our tenants.status is
// active|paused|disabled — map before writing (and useSuperAdmin maps back).
const TO_DB = { active: 'active', suspended: 'paused', removed: 'disabled' };

export async function setTenantStatus(tenantId, status) {
    await adminInvoke('set_status', { tenant_id: tenantId, status: TO_DB[status] || status });
}
