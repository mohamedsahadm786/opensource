import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

const POLL_INTERVAL_MS = 20_000;
const STALL_TIMEOUT_MS = 45 * 60 * 1000;   // no progress at all -> stalled
const QUIET_COMPLETE_MS = 5 * 60 * 1000;   // progress, then quiet -> assume complete

// Polls progress of a run and resolves its phase:
//   running   -> rows still appearing (show counts)
//   completed -> the enqueued job reached 'succeeded' (reliable), OR rows
//                appeared and then went quiet for QUIET_COMPLETE_MS (heuristic)
//   stalled   -> the job 'failed', OR no rows at all for STALL_TIMEOUT_MS
//
// Counts are RLS-scoped to the caller's tenant automatically.
// Returns: { counts:{personas,outputs,videos}, lastChangeAt, completed, stalled }.
export function useRunProgress(runStartedAt, jobId = null) {
    const [counts, setCounts] = useState({ personas: 0, outputs: 0, videos: 0 });
    const [lastChangeAt, setLastChangeAt] = useState(null);
    const [completed, setCompleted] = useState(false);
    const [stalled, setStalled] = useState(false);

    useEffect(() => {
        if (!runStartedAt) {
            setCounts({ personas: 0, outputs: 0, videos: 0 });
            setLastChangeAt(null);
            setCompleted(false);
            setStalled(false);
            return;
        }

        let cancelled = false;
        let lastSig = '';
        let lastChange = runStartedAt;
        const startISO = new Date(runStartedAt).toISOString();

        async function poll() {
            if (cancelled) return;
            try {
                const [pRes, oRes, vRes, jobRes] = await Promise.all([
                    supabase.from('personas').select('*', { count: 'exact', head: true }).gt('created_at', startISO),
                    supabase.from('outputs').select('*', { count: 'exact', head: true }).gt('created_at', startISO),
                    supabase.from('videos').select('*', { count: 'exact', head: true }).gt('created_at', startISO),
                    jobId
                        ? supabase.from('jobs').select('status').eq('id', jobId).maybeSingle()
                        : Promise.resolve({ data: null }),
                ]);
                if (cancelled) return;

                const next = { personas: pRes.count || 0, outputs: oRes.count || 0, videos: vRes.count || 0 };
                const sig = `${next.personas},${next.outputs},${next.videos}`;
                if (sig !== lastSig) {
                    lastSig = sig;
                    lastChange = Date.now();
                    setCounts(next);
                    setLastChangeAt(lastChange);
                }

                const jobStatus = jobRes?.data?.status;
                const progressed = next.personas + next.outputs + next.videos > 0;
                const quiet = Date.now() - lastChange;
                const heuristicDone = progressed && quiet > QUIET_COMPLETE_MS;

                if (jobStatus === 'succeeded' || heuristicDone) {
                    setCompleted(true);
                    setStalled(false);
                } else if (jobStatus === 'failed' || (!progressed && quiet > STALL_TIMEOUT_MS)) {
                    setStalled(true);
                }
            } catch (err) {
                console.error('[Alluvi] run progress poll failed', err);
            }
        }

        poll();
        const id = setInterval(poll, POLL_INTERVAL_MS);
        return () => { cancelled = true; clearInterval(id); };
    }, [runStartedAt, jobId]);

    return { counts, lastChangeAt, completed, stalled };
}
