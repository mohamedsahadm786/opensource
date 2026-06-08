import { useCallback, useEffect, useRef, useState } from 'react';
import { supabase } from '../lib/supabase.js';
import { ADMIN_PASS, ADMIN_USER, SESSION_KEY } from '../lib/constants.js';

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
        const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
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

    const logout = useCallback(async () => {
        sessionStorage.removeItem(SESSION_KEY);
        await supabase.auth.signOut();
        setAuthed(false);
        setUser(null);
    }, []);

    return { authed, ready, user, login, signUp, signIn, logout };
}
