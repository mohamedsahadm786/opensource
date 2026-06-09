import { useCallback, useEffect, useRef, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { adminInvoke } from '../lib/adminApi.js';
import { ADMIN_PASS, ADMIN_USER, SESSION_KEY } from '../lib/constants.js';

const IMP_KEY = 'alluvi.impersonating'; // sessionStorage flag while a super-admin views a tenant

// Two ways in:
//  • Super admin (platform owner): hardcoded ADMIN_USER/ADMIN_PASS, gated by a
//    sessionStorage flag. No Supabase Auth. Lands on the super-admin console.
//  • Members: real Supabase Auth (email + password). Each member belongs to a
//    tenant; their tenant_id is carried in the JWT app_metadata.tenant_id and is
//    set server-side by the `provision-tenant` Edge Function (see below).
//
// `authed` is true if either path is active. A real member session ALWAYS wins
// over a lingering super-admin flag.
function deriveUser(session) {
    if (session?.user) {
        const u = session.user;
        const name = u.user_metadata?.name?.trim() || u.email?.split('@')[0] || 'Member';
        return {
            kind: 'member',
            id: u.id,
            tenantId: u.app_metadata?.tenant_id || null,
            name,
            email: u.email || null,
            role: 'Member',
        };
    }
    if (sessionStorage.getItem(SESSION_KEY) === 'ok') {
        return { kind: 'super_admin', id: null, tenantId: null, name: 'Super Admin', email: null, role: 'Platform owner' };
    }
    return null;
}

export function useAuth() {
    const [authed, setAuthed] = useState(false);
    const [user, setUser] = useState(null);
    const [ready, setReady] = useState(false);
    const [recovery, setRecovery] = useState(false); // true while handling a password-reset link
    const [impersonating, setImpersonating] = useState(() => {
        try { return JSON.parse(sessionStorage.getItem(IMP_KEY) || 'null'); } catch { return null; }
    });
    const provisioning = useRef(new Set()); // user ids currently being provisioned

    const adminFlag = () => sessionStorage.getItem(SESSION_KEY) === 'ok';

    const sync = (session) => {
        setAuthed(adminFlag() || Boolean(session));
        setUser(deriveUser(session));
    };

    // Ensure a member has a tenant. If their JWT has no tenant_id yet (fresh
    // signup, or a member created before provisioning existed), call the
    // service-role Edge Function to create tenant + membership and stamp
    // app_metadata.tenant_id, then REFRESH the session so the new token carries
    // it (app_metadata changes don't propagate to the live token automatically).
    const ensureProvisioned = useCallback(async (session) => {
        const u = session?.user;
        if (!u || u.app_metadata?.tenant_id) return;
        if (provisioning.current.has(u.id)) return;
        provisioning.current.add(u.id);
        try {
            const { data, error } = await supabase.functions.invoke('provision-tenant', {
                method: 'POST',
                body: { company_name: u.user_metadata?.name || null },
            });
            if (error || !data?.ok) {
                console.error('[Alluvi] provisioning failed', error || data);
                return;
            }
            await supabase.auth.refreshSession(); // fires onAuthStateChange with the new tenant_id
        } finally {
            provisioning.current.delete(u.id);
        }
    }, []);

    useEffect(() => {
        let mounted = true;
        supabase.auth.getSession().then(({ data }) => {
            if (!mounted) return;
            sync(data.session);
            setReady(true);
            ensureProvisioned(data.session);
        });
        const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
            // Arriving via a password-reset email → let the UI show a "set new
            // password" form instead of dropping the user into the dashboard.
            if (event === 'PASSWORD_RECOVERY') setRecovery(true);
            sync(session);
            ensureProvisioned(session);
        });
        return () => { mounted = false; sub.subscription.unsubscribe(); };
    }, [ensureProvisioned]);

    // Super-admin login. Clear any member session first so the two never coexist.
    const login = useCallback(async (username, password) => {
        await new Promise(r => setTimeout(r, 360)); // let the spinner show
        if (username === ADMIN_USER && password === ADMIN_PASS) {
            await supabase.auth.signOut();
            sessionStorage.setItem(SESSION_KEY, 'ok');
            setAuthed(true);
            setUser(deriveUser(null));
            return { ok: true };
        }
        return { ok: false, error: 'Invalid credentials. Please check your username and password.' };
    }, []);

    // Member sign up. The onAuthStateChange handler provisions the tenant.
    const signUp = useCallback(async ({ name, email, password }) => {
        sessionStorage.removeItem(SESSION_KEY);
        const { data, error } = await supabase.auth.signUp({
            email, password, options: { data: { name } },
        });
        if (error) return { ok: false, error: error.message };
        if (data.session) {
            setAuthed(true);
            await ensureProvisioned(data.session);
            return { ok: true, signedIn: true };
        }
        // Email confirmation is ON — they must confirm before a session exists.
        return { ok: true, signedIn: false };
    }, [ensureProvisioned]);

    const signIn = useCallback(async ({ email, password }) => {
        sessionStorage.removeItem(SESSION_KEY);
        const { data, error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) return { ok: false, error: error.message };
        setAuthed(Boolean(data.session));
        await ensureProvisioned(data.session);
        return { ok: true };
    }, [ensureProvisioned]);

    // Send a password-reset email. The link returns to the app's origin and fires
    // the PASSWORD_RECOVERY event (handled above) so the user can set a new password.
    const resetPassword = useCallback(async (email) => {
        const addr = (email || '').trim();
        if (!addr) return { ok: false, error: 'Enter your email.' };
        const { error } = await supabase.auth.resetPasswordForEmail(addr, {
            redirectTo: window.location.origin,
        });
        if (error) return { ok: false, error: error.message };
        return { ok: true };
    }, []);

    // Set a new password for the active (recovery) session, then clear recovery.
    const updatePassword = useCallback(async (password) => {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) return { ok: false, error: error.message };
        setRecovery(false);
        return { ok: true };
    }, []);

    // Super-admin → enter a tenant's workspace AS that tenant. The service-role
    // function mints a magic-link token for the tenant owner; we verify it to get
    // a real member session (so RLS passes and the tenant Dashboard works fully).
    const impersonate = useCallback(async (tenant) => {
        try {
            const data = await adminInvoke('impersonate', { tenant_id: tenant.tenant_id });
            if (!data?.token_hash) return { ok: false, error: 'Could not start impersonation.' };
            const info = { tenant_id: tenant.tenant_id, name: tenant.name, email: tenant.email };
            // mark impersonation + drop the admin flag BEFORE the member session lands
            try { sessionStorage.setItem(IMP_KEY, JSON.stringify(info)); } catch { /* noop */ }
            sessionStorage.removeItem(SESSION_KEY);
            const { error } = await supabase.auth.verifyOtp({ token_hash: data.token_hash, type: 'magiclink' });
            if (error) {
                try { sessionStorage.removeItem(IMP_KEY); } catch { /* noop */ }
                sessionStorage.setItem(SESSION_KEY, 'ok'); // restore super-admin
                return { ok: false, error: error.message };
            }
            setImpersonating(info);
            return { ok: true };
        } catch (err) {
            sessionStorage.setItem(SESSION_KEY, 'ok');
            return { ok: false, error: err.message || 'Impersonation failed.' };
        }
    }, []);

    // Exit impersonation: drop the tenant session and restore the super-admin.
    const exitImpersonation = useCallback(async () => {
        try { sessionStorage.removeItem(IMP_KEY); } catch { /* noop */ }
        sessionStorage.setItem(SESSION_KEY, 'ok'); // become super-admin again
        setImpersonating(null);
        await supabase.auth.signOut(); // fires onAuthStateChange(null) -> super_admin
    }, []);

    const logout = useCallback(async () => {
        sessionStorage.removeItem(SESSION_KEY);
        sessionStorage.removeItem(IMP_KEY);
        await supabase.auth.signOut();
        setAuthed(false);
        setUser(null);
        setRecovery(false);
        setImpersonating(null);
    }, []);

    return {
        authed, ready, user, recovery, impersonating,
        login, signUp, signIn, logout, resetPassword, updatePassword,
        impersonate, exitImpersonation,
    };
}
