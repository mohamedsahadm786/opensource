import { useCallback, useEffect, useRef, useState } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { useAccounts } from '../hooks/useAccounts.js';
import { useTenant } from '../hooks/useTenant.js';
import { usePipelineRun } from '../hooks/usePipelineRun.js';
import { useRunProgress } from '../hooks/useRunProgress.js';
import { usePipelineConfig } from '../hooks/usePipelineConfig.js';
import { useToast } from '../contexts/ToastContext.jsx';
import { friendlySupabaseError } from '../lib/utils.js';
import { Sidebar } from './Sidebar.jsx';
import { Topbar } from './Topbar.jsx';
import { Stats } from './Stats.jsx';
import { AccountsPanel } from './AccountsPanel.jsx';
import { ProductPanel } from './ProductPanel.jsx';
import { PublishingPanel } from './PublishingPanel.jsx';
import { AnalyticsPanel } from './AnalyticsPanel.jsx';
import { EnginePanel } from './EnginePanel.jsx';
import { SettingsPanel } from './SettingsPanel.jsx';
import { TenantSetup } from './TenantSetup.jsx';
import { AccountFormModal } from './AccountFormModal.jsx';
import { DeleteModal } from './DeleteModal.jsx';
import { RunControl } from './RunControl.jsx';
import { RunConfigModal } from './RunConfigModal.jsx';

// Error codes returned by the trigger-pipeline Edge Function / invoke layer.
const RUN_ERRORS = {
    function_unreachable: "Couldn't reach the trigger function. Is it deployed?",
    no_tenant: 'No tenant is linked to your account yet. Try reloading.',
    enqueue_failed: 'Could not enqueue the run. Please try again.',
    invalid_token: 'Your session expired — sign in again.',
    unknown: 'Pipeline trigger failed.',
};

const VIEWS = {
    accounts: { title: 'TikTok Accounts', subtitle: 'Manage every account your automation publishes from.', hasSearch: true },
    product: { title: 'Product', subtitle: 'The one product per workspace, used across the pipeline.', hasSearch: false },
    publishing: { title: 'Publishing', subtitle: 'Browse generated images and videos by account.', hasSearch: true },
    analytics: { title: 'Analytics', subtitle: 'Pipeline health, demographics, and what your automation produced.', hasSearch: false },
    engine: { title: 'Learning engine', subtitle: 'Exploration coverage, learned beliefs, and tuning suggestions.', hasSearch: false },
    settings: { title: 'Settings', subtitle: 'Anthropic key and GPU connection.', hasSearch: false },
};

export function Dashboard({ theme, onToggleTheme, onLogout, user, impersonated = false }) {
    const toast = useToast();
    const {
        tenantId, isAdmin, onboarded, status: tenantStatus, profile,
        reload: reloadTenant, saveTenantConfig, storeAnthropicKey, markOnboarded,
    } = useTenant(user);
    const { accounts, status, error, reload, create, update, remove } = useAccounts(tenantId);
    const { running, run, runStartedAt, jobId, clearRun } = usePipelineRun();
    const { counts, currentStage, completed, stalled } = useRunProgress(runStartedAt, jobId);
    const { config: runConfig, isComplete: runConfigComplete, save: saveRunConfig } = usePipelineConfig(tenantId);

    const tenantLoading = !isAdmin && tenantStatus === 'loading';
    const needsSetup = !isAdmin && tenantStatus === 'ready' && !onboarded;

    const [view, setView] = useState('accounts');
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const [search, setSearch] = useState('');
    const [genderFilter, setGenderFilter] = useState('');
    const [countryFilter, setCountryFilter] = useState('');

    const [formState, setFormState] = useState({ open: false, mode: 'create', target: null });
    const [deleteState, setDeleteState] = useState({ open: false, target: null });
    const [runSettingsOpen, setRunSettingsOpen] = useState(false);

    const searchRef = useRef(null);

    useEffect(() => {
        function onKey(e) {
            if (e.key !== '/') return;
            if (!VIEWS[view]?.hasSearch) return;
            const tag = (document.activeElement?.tagName) || '';
            if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
            if (formState.open || deleteState.open) return;
            e.preventDefault();
            searchRef.current?.focus();
        }
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [view, formState.open, deleteState.open]);

    const navigate = useCallback((next) => { setView(next); setSearch(''); }, []);
    const openOnboard = useCallback(() => setFormState({ open: true, mode: 'create', target: null }), []);
    const openEdit = useCallback((account) => setFormState({ open: true, mode: 'edit', target: account }), []);
    const closeForm = useCallback(() => setFormState((s) => ({ ...s, open: false })), []);

    const handleFormSubmit = useCallback(async (payload) => {
        try {
            if (formState.mode === 'edit') {
                const { cascaded } = await update(formState.target.id, payload);
                toast.success(cascaded
                    ? 'Account updated. Persona will be regenerated on the next pipeline run.'
                    : 'Account updated.');
            } else {
                await create(payload);
                toast.success('Account onboarded.');
            }
            setFormState((s) => ({ ...s, open: false }));
        } catch (err) {
            throw new Error(friendlySupabaseError(err));
        }
    }, [formState.mode, formState.target, create, update, toast]);

    const openDelete = useCallback((account) => setDeleteState({ open: true, target: account }), []);
    const closeDelete = useCallback(() => setDeleteState((s) => ({ ...s, open: false })), []);

    const handleRun = useCallback(async () => {
        if (!runConfigComplete) {
            setRunSettingsOpen(true);
            toast.info('Complete the run settings first.');
            return;
        }
        try {
            await saveRunConfig(runConfig);
        } catch (err) {
            toast.error('Could not save run settings.');
            return;
        }
        const result = await run();
        if (result.alreadyRunning) return;
        if (result.ok) {
            toast.success('Pipeline run enqueued. Progress will appear next to the Run button.');
            return;
        }
        toast.error(RUN_ERRORS[result.error] || RUN_ERRORS.unknown);
    }, [run, toast, runConfigComplete, runConfig, saveRunConfig]);

    const handleDeleteConfirm = useCallback(async (target) => {
        try {
            await remove(target.id);
            toast.success('Account deleted.');
            setDeleteState({ open: false, target: null });
        } catch (err) {
            toast.error(friendlySupabaseError(err));
        }
    }, [remove, toast]);

    const meta = VIEWS[view];

    // A paused/disabled tenant is blocked (mapped from suspend/remove). The super
    // admin impersonating them bypasses this (impersonated=true).
    const accountStatus = profile?.status || 'active';
    if (!impersonated && !isAdmin && (accountStatus === 'paused' || accountStatus === 'disabled')) {
        return (
            <section className="auth-shell">
                <div className="auth-card auth-card--blocked">
                    <h2>{accountStatus === 'disabled' ? 'Account removed' : 'Account suspended'}</h2>
                    <p>
                        Your access has been {accountStatus === 'disabled' ? 'removed' : 'temporarily suspended'} by
                        an administrator. Please contact your platform administrator.
                    </p>
                    <button type="button" className="btn btn-ghost btn-block" onClick={onLogout}>Sign out</button>
                </div>
            </section>
        );
    }

    const showRunControls = view === 'accounts' && onboarded;

    return (
        <section className="app-shell">
            <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} onLogout={onLogout}
                view={view} onNavigate={navigate} user={user} />

            <main className="main">
                <Topbar
                    theme={theme}
                    onToggleTheme={onToggleTheme}
                    onOpenSidebar={() => setSidebarOpen(true)}
                    title={meta.title}
                    subtitle={meta.subtitle}
                    search={search}
                    onSearchChange={meta.hasSearch ? setSearch : undefined}
                    searchRef={searchRef}
                    onOnboard={showRunControls ? openOnboard : undefined}
                    runControl={showRunControls ? (
                        <div className="run-cluster-wrap">
                            <button
                                type="button"
                                className={`btn btn-ghost run-settings-btn${runConfigComplete ? '' : ' is-incomplete'}`}
                                onClick={() => setRunSettingsOpen(true)}
                                title={runConfigComplete ? 'Run settings' : 'Run settings — setup required'}
                            >
                                <SlidersHorizontal />
                                <span>Run settings</span>
                            </button>
                            <RunControl
                                running={running}
                                onRun={handleRun}
                                runStartedAt={runStartedAt}
                                counts={counts}
                                currentStage={currentStage}
                                completed={completed}
                                stalled={stalled}
                                onClear={clearRun}
                                canRun={runConfigComplete}
                                disabledReason="Complete run settings to enable Run"
                            />
                        </div>
                    ) : null}
                />

                {view === 'accounts' && tenantLoading && (
                    <div className="state state-loading"><div className="spinner" /><p>Loading your workspace…</p></div>
                )}

                {view === 'accounts' && needsSetup && (
                    <TenantSetup
                        user={user}
                        tenantId={tenantId}
                        profile={profile}
                        saveTenantConfig={saveTenantConfig}
                        storeAnthropicKey={storeAnthropicKey}
                        markOnboarded={markOnboarded}
                        onDone={reloadTenant}
                    />
                )}

                {view === 'accounts' && !tenantLoading && !needsSetup && (
                    <>
                        <Stats accounts={accounts} />
                        <AccountsPanel
                            accounts={accounts}
                            status={status}
                            error={error}
                            search={search}
                            genderFilter={genderFilter}
                            onGenderFilter={setGenderFilter}
                            countryFilter={countryFilter}
                            onCountryFilter={setCountryFilter}
                            onReload={reload}
                            onOnboard={openOnboard}
                            onEdit={openEdit}
                            onDelete={openDelete}
                        />
                    </>
                )}

                {view === 'product' && <ProductPanel tenantId={tenantId} />}

                {view === 'publishing' && (
                    <PublishingPanel
                        accounts={accounts}
                        status={status}
                        error={error}
                        search={search}
                        onReload={reload}
                        rater={user?.email || user?.id || 'unknown'}
                    />
                )}

                {view === 'analytics' && <AnalyticsPanel tenantId={tenantId} />}
                {view === 'engine' && <EnginePanel tenantId={tenantId} />}
                {view === 'settings' && <SettingsPanel tenantId={tenantId} />}

                <footer className="page-foot">
                    <p>Alluvi Console · open-source pipeline build</p>
                </footer>
            </main>

            <AccountFormModal
                open={formState.open}
                mode={formState.mode}
                initial={formState.target}
                tenantId={tenantId}
                onClose={closeForm}
                onSubmit={handleFormSubmit}
            />

            <DeleteModal
                open={deleteState.open}
                target={deleteState.target}
                onClose={closeDelete}
                onConfirm={handleDeleteConfirm}
            />

            <RunConfigModal
                open={runSettingsOpen}
                config={runConfig}
                accounts={accounts}
                tenantId={tenantId}
                onClose={() => setRunSettingsOpen(false)}
                onSave={saveRunConfig}
            />
        </section>
    );
}
