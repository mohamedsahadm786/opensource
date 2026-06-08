import { useCallback, useEffect, useState } from 'react';
import { adminInvoke } from '../lib/adminApi.js';

// Recent impersonation events for the Activity view — read via the service-role
// admin-data Edge Function (impersonation_events has no anon policy).
export function useAuditLog(limit = 100) {
    const [events, setEvents] = useState([]);
    const [status, setStatus] = useState('loading'); // 'loading' | 'ready' | 'error'
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        setStatus('loading');
        setError(null);
        try {
            const data = await adminInvoke('list_events', { limit });
            setEvents(data.events || []);
            setStatus('ready');
        } catch (err) {
            console.error('[Alluvi] audit log load failed', err);
            setError(err);
            setStatus('error');
        }
    }, [limit]);

    useEffect(() => { load(); }, [load]);

    return { events, status, error, reload: load };
}
