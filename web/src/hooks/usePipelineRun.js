import { useCallback, useState } from 'react';
import { supabase } from '../lib/supabase.js';

const RUN_KEY = 'alluvi.runStartedAt';
const JOB_KEY = 'alluvi.runJobId';

function readNum(key) {
    try { const n = Number(sessionStorage.getItem(key)); return Number.isFinite(n) && n ? n : null; }
    catch { return null; }
}

// Triggers a pipeline run by enqueuing a durable job (trigger-pipeline Edge
// Function inserts a public.jobs row with job_type='pipeline_run'). The
// orchestrator's claim_next_job RPC is the consumer. On success we persist the
// run's start time + job id so the progress pill survives a refresh.
export function usePipelineRun() {
    const [running, setRunning] = useState(false);
    const [runStartedAt, setRunStartedAt] = useState(() => readNum(RUN_KEY));
    const [jobId, setJobId] = useState(() => {
        try { return sessionStorage.getItem(JOB_KEY) || null; } catch { return null; }
    });

    const run = useCallback(async () => {
        if (running) return { ok: false, alreadyRunning: true };
        setRunning(true);
        try {
            const { data, error } = await supabase.functions.invoke('trigger-pipeline', {
                method: 'POST', body: {},
            });
            if (error) {
                console.error('[Alluvi] trigger function unreachable', error);
                return { ok: false, error: 'function_unreachable', message: error.message };
            }
            if (data?.ok) {
                const t = Date.now();
                try {
                    sessionStorage.setItem(RUN_KEY, String(t));
                    if (data.job_id) sessionStorage.setItem(JOB_KEY, data.job_id);
                } catch { /* noop */ }
                setRunStartedAt(t);
                setJobId(data.job_id || null);
                return { ok: true, job_id: data.job_id };
            }
            return { ok: false, error: data?.error || 'unknown', message: data?.message };
        } catch (err) {
            console.error('[Alluvi] trigger crashed', err);
            return { ok: false, error: 'unknown', message: err.message };
        } finally {
            setRunning(false);
        }
    }, [running]);

    const clearRun = useCallback(() => {
        try { sessionStorage.removeItem(RUN_KEY); sessionStorage.removeItem(JOB_KEY); } catch { /* noop */ }
        setRunStartedAt(null);
        setJobId(null);
    }, []);

    return { running, run, runStartedAt, jobId, clearRun };
}
