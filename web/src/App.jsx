import { ToastProvider } from './contexts/ToastContext.jsx';
import { useTheme } from './hooks/useTheme.js';
import { useAuth } from './hooks/useAuth.js';
import { LoginScreen } from './components/LoginScreen.jsx';
import { Dashboard } from './components/Dashboard.jsx';
import { SuperAdminApp } from './components/SuperAdminApp.jsx';
import { ImpersonationBanner } from './components/ImpersonationBanner.jsx';

export default function App() {
    const { theme, toggle } = useTheme();
    const {
        authed, ready, user, recovery, impersonating,
        login, signUp, signIn, logout, resetPassword, updatePassword,
        impersonate, exitImpersonation,
    } = useAuth();

    function renderAuthed() {
        // Super-admin impersonating a tenant: a real tenant session is active, so
        // render the EXACT tenant Dashboard, wrapped with an exit banner.
        if (impersonating && user?.kind === 'member') {
            return (
                <div className="imp-shell">
                    <ImpersonationBanner name={impersonating.name} email={impersonating.email} onExit={exitImpersonation} />
                    <Dashboard theme={theme} onToggleTheme={toggle} onLogout={exitImpersonation} user={user} impersonated />
                </div>
            );
        }
        // Super admin gets its own console; everyone else (tenants) gets the
        // unchanged tenant Dashboard.
        if (user?.kind === 'super_admin') {
            return <SuperAdminApp theme={theme} onToggleTheme={toggle} onLogout={logout} user={user} onImpersonate={impersonate} />;
        }
        return <Dashboard theme={theme} onToggleTheme={toggle} onLogout={logout} user={user} />;
    }

    return (
        <ToastProvider>
            {!ready
                ? <div className="auth-shell" aria-hidden="true" />
                : (authed && !recovery)
                    ? renderAuthed()
                    : <LoginScreen
                        theme={theme}
                        onToggleTheme={toggle}
                        onLogin={login}
                        onSignIn={signIn}
                        onSignUp={signUp}
                        onForgotPassword={resetPassword}
                        onUpdatePassword={updatePassword}
                        recovery={recovery}
                    />}
        </ToastProvider>
    );
}
