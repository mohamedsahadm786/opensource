import { useCallback, useState } from 'react';
import { useSuperAdmin } from '../hooks/useSuperAdmin.js';
import { logImpersonation } from '../lib/audit.js';
import { SuperAdminSidebar } from './SuperAdminSidebar.jsx';
import { SuperAdminOverview } from './SuperAdminOverview.jsx';
import { TenantsList } from './TenantsList.jsx';
import { TenantDetail } from './TenantDetail.jsx';
import { AuditLogPanel } from './AuditLogPanel.jsx';
import { Topbar } from './Topbar.jsx';

const VIEWS = {
    overview: { title: 'Platform overview', subtitle: 'Analytics across every tenant.' },
    tenants:  { title: 'Tenants',           subtitle: 'Every workspace on the platform.' },
    detail:   { title: 'Tenant',            subtitle: 'Progress, cost, and configuration.' },
    activity: { title: 'Activity',          subtitle: 'Audit log of tenant page access.' },
};

// The Super Admin console. Entirely separate from the tenant Dashboard, which
// it only renders through impersonation (passing a synthetic tenant user so the
// existing tenant hooks scope correctly).
export function SuperAdminApp({ theme, onToggleTheme, onLogout, user, onImpersonate }) {
    const { tenants, totals, status, error, reload } = useSuperAdmin();

    const [view, setView] = useState('overview');
    const [selectedId, setSelectedId] = useState(null);
    const [sidebarOpen, setSidebarOpen] = useState(false);

    // Derive the selected tenant from the live list so edits/reloads stay fresh.
    const selected = selectedId ? tenants.find(t => t.tenant_id === selectedId) : null;

    const navigate = useCallback((next) => {
        setView(next);
        if (next !== 'detail') setSelectedId(null);
    }, []);

    const openTenant = useCallback((tenant) => {
        setSelectedId(tenant.tenant_id);
        setView('detail');
    }, []);

    const startImpersonation = useCallback(async (tenant) => {
        logImpersonation(tenant); // best-effort audit record
        const r = await onImpersonate?.(tenant); // swaps to a real tenant session (App renders the tenant Dashboard)
        if (r && !r.ok) console.error('[Alluvi] impersonation failed', r.error);
    }, [onImpersonate]);

    // ---- Console ----
    const meta = VIEWS[view];
    return (
        <section className="app-shell">
            <SuperAdminSidebar
                open={sidebarOpen}
                onClose={() => setSidebarOpen(false)}
                onLogout={onLogout}
                view={view}
                onNavigate={navigate}
                user={user}
            />

            <main className="main">
                <Topbar
                    theme={theme}
                    onToggleTheme={onToggleTheme}
                    onOpenSidebar={() => setSidebarOpen(true)}
                    title={meta.title}
                    subtitle={meta.subtitle}
                />

                {view === 'overview' && (
                    <SuperAdminOverview totals={totals} tenants={tenants} status={status} error={error} />
                )}

                {view === 'tenants' && (
                    <TenantsList
                        tenants={tenants}
                        status={status}
                        error={error}
                        onSelect={openTenant}
                    />
                )}

                {view === 'detail' && (
                    <TenantDetail
                        tenant={selected}
                        onBack={() => navigate('tenants')}
                        onImpersonate={startImpersonation}
                        onSaved={reload}
                    />
                )}

                {view === 'activity' && <AuditLogPanel />}

                <footer className="page-foot">
                    <p>Alluvi Onboarding Console · Super Admin</p>
                </footer>
            </main>
        </section>
    );
}
