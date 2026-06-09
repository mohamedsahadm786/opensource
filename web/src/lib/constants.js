// Which Supabase project the app talks to is decided by environment, NOT
// hardcoded. Set these in `.env.local` (dev) and the host env (prod).
//
// IMPORTANT: VITE_SUPABASE_ANON_KEY must be the ANON / PUBLISHABLE key — never
// the service/secret key. The browser is RLS-scoped by tenant_id from the JWT;
// the service key lives only inside the Supabase Edge Functions (server-side).
export const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
export const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_KEY) {
    throw new Error(
        'Missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY. Create a .env.local file ' +
        '(copy .env.example) and restart `npm run dev`.',
    );
}

// The only human-input table the web owns directly.
export const TABLE = 'tiktok_accounts';

// ---------------------------------------------------------------------------
// Super-admin (platform owner) — hard-coded credentials, MVP only.
// Preserved exactly from the reference build. KNOWN SECURITY ITEM: rotate /
// move to real auth before any public launch. The same value is configured
// server-side as the ADMIN_SECRET env on the `admin-data` Edge Function, which
// is what actually gates cross-tenant access (the service key never ships here).
// ---------------------------------------------------------------------------
export const ADMIN_USER = 'admin';
export const ADMIN_PASS = 'Alluvi@admin@1512';
export const ADMIN_SECRET = ADMIN_PASS;

export const SESSION_KEY = 'alluvi.session';
export const THEME_KEY = 'alluvi.theme';

// Per-asset cost estimate for the Super Admin console. No real metering yet —
// tune to match actual GPU/Anthropic spend.
export const COST_RATES = {
    image: 0.05,   // $ per generated scene image (an `outputs` row)
    video: 0.20,   // $ per generated video (a `videos` row)
};

// Our schema enforces male/female (Stage 1 hard-fails otherwise).
export const GENDER_OPTIONS = [
    { value: 'female', label: 'Female' },
    { value: 'male', label: 'Male' },
];

export const PLAN_OPTIONS = [
    { value: 'free', label: 'Free' },
    { value: 'pro', label: 'Pro' },
    { value: 'enterprise', label: 'Enterprise' },
];

// tenant_pipeline_config option lists
export const CREATION_MODE_OPTIONS = [
    { value: 'all', label: 'All accounts' },
    { value: 'new_only', label: 'New accounts only (no persona yet)' },
    { value: 'specific', label: 'Specific accounts' },
];

export const VIDEO_MODE_OPTIONS = [
    { value: 'multishot', label: 'Multishot' },
    { value: 'silentfirst', label: 'Silent-first' },
];

export const COUNTRY_SUGGESTIONS = [
    'United States', 'United Kingdom', 'Canada', 'Australia', 'India',
    'Germany', 'France', 'Spain', 'Italy', 'Brazil', 'Mexico', 'Japan',
    'South Korea', 'Indonesia', 'Philippines', 'Vietnam', 'Netherlands',
    'Sweden', 'UAE', 'Singapore',
];

export const LANGUAGE_SUGGESTIONS = [
    'English', 'Spanish', 'Portuguese', 'French', 'German', 'Italian',
    'Hindi', 'Arabic', 'Japanese', 'Korean', 'Mandarin', 'Indonesian',
    'Vietnamese', 'Tagalog', 'Dutch', 'Swedish', 'Turkish', 'Russian',
];
