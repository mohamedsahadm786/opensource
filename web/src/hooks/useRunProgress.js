import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

const POLL_INTERVAL_MS = 8_000;
const STALL_TIMEOUT_MS = 30 * 60 * 1000;   // job still 'running' but ZERO activity this long -> likely hung

// Polls a run's progress. Completion / failure are driven by the durable JOB the
// worker updates (jobs.status = 'succeeded' | 'failed') — NOT a heuristic, so the
// pill no longer falsely flips to "complete" during a long video render.
//
// While the job runs we also surface the CURRENT stage: run_pipeline writes a
// stage_executions row at each step (phasea | step1 | step2 | qc | step3 |
// script | video), and we show the latest one ("Compositing the product…") so the
// user can see the long render is alive and exactly where it is.
//
// Returns: { counts, currentStage, lastChangeAt, completed, stalled }.
export function useRunProgress(runStartedAt, jobId = null) {
    const [counts, setCounts] = useState({ personas: 0, outputs: 0, videos: 0 });
    const [currentStage, setCurrentStage] = useState(null);
    const [lastChangeAt, setLastChangeAt] = useState(null);
    const [completed, setCompleted] = useState(false);
    const [stalled, setStalled] = useState(false);

    useEffect(() => {
        if (!runStartedAt) {
            setCounts({ personas: 0, outputs: 0, videos: 0 });
            setCurrentStage(null);
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
                const [pRes, oRes, vRes, stageRes, jobRes] = await Promise.all([
                    supabase.from('personas').select('*', { count: 'exact', head: true }).gt('created_at', startISO),
                    supabase.from('outputs').select('*', { count: 'exact', head: true }).gt('created_at', startISO),
                    supabase.from('videos').select('*', { count: 'exact', head: true }).gt('created_at', startISO),
                    supabase.from('stage_executions')
                        .select('stage_name, created_at')
                        .gt('created_at', startISO)
                        .order('created_at', { ascending: false })
                        .limit(1),
                    jobId
                        ? supabase.from('jobs').select('status').eq('id', jobId).maybeSingle()
                        : Promise.resolve({ data: null }),
                ]);
                if (cancelled) return;

                const next = { personas: pRes.count || 0, outputs: oRes.count || 0, videos: vRes.count || 0 };
                const stage = stageRes.data?.[0]?.stage_name || null;
                // "activity" = counts OR the live stage changing; either keeps the heartbeat alive.
                const sig = `${next.personas},${next.outputs},${next.videos},${stage || ''}`;
                if (sig !== lastSig) {
                    lastSig = sig;
                    lastChange = Date.now();
                    setCounts(next);
                    setCurrentStage(stage);
                    setLastChangeAt(lastChange);
                }

                const jobStatus = jobRes?.data?.status;
                const quiet = Date.now() - lastChange;

                // Completion is authoritative: the worker sets the job to succeeded/failed.
                if (jobStatus === 'succeeded') {
                    setCompleted(true);
                    setStalled(false);
                } else if (jobStatus === 'failed') {
                    setStalled(true);
                } else if (quiet > STALL_TIMEOUT_MS) {
                    // Last-resort safety net: no rows AND no stage change for 30 min -> probably hung.
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

    return { counts, currentStage, lastChangeAt, completed, stalled };
}
