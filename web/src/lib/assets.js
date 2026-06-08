// Asset access — the single place that knows how to turn a stored object into a
// usable URL.
//
// Our Storage buckets are PRIVATE. The DB never stores a URL; a `media_assets`
// row stores only { bucket, path }. To display a file we mint a short-lived
// signed URL at read time. (017_storage_policies.sql scopes each tenant to its
// own `<tenant_id>/...` folder, so a member can only sign their own files.)

import { supabase } from './supabase.js';

const DEFAULT_TTL = 60 * 60; // 1 hour

// Sign one object. Returns the URL string, or null on failure.
export async function signedUrl(bucket, path, expiresIn = DEFAULT_TTL) {
    if (!bucket || !path) return null;
    const { data, error } = await supabase.storage.from(bucket).createSignedUrl(path, expiresIn);
    if (error) {
        console.warn('[Alluvi] signedUrl failed', bucket, path, error.message);
        return null;
    }
    return data?.signedUrl || null;
}

// Sign many objects in one bucket at once. paths -> { path: url }.
export async function signedUrls(bucket, paths, expiresIn = DEFAULT_TTL) {
    const clean = (paths || []).filter(Boolean);
    if (!bucket || clean.length === 0) return {};
    const { data, error } = await supabase.storage.from(bucket).createSignedUrls(clean, expiresIn);
    if (error) {
        console.warn('[Alluvi] signedUrls failed', bucket, error.message);
        return {};
    }
    const map = {};
    (data || []).forEach((d) => { if (d?.path && d?.signedUrl) map[d.path] = d.signedUrl; });
    return map;
}

// Download a (signed) asset URL as a file.
export function downloadAsset(url, filename) {
    if (!url) return;
    const a = document.createElement('a');
    a.href = url;
    if (filename) a.download = filename;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    document.body.appendChild(a);
    a.click();
    a.remove();
}
