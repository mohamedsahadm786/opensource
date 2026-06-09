import { AlertTriangle, CheckCircle2, Play, X } from 'lucide-react';

// Friendly labels for the live pipeline stage. run_pipeline writes a
// stage_executions row at each step; useRunProgress surfaces the latest stage_name.
const STAGE_LABELS = {
    phasea: 'Creating persona portrait',
    step1: 'Generating scene (PuLID)',
    step2: 'Compositing product (Qwen)',
    qc: 'Quality-checking image',
    step3: 'Realism pass',
    script: 'Writing video script',
    video: 'Rendering video (WAN → lip-sync → merge)',
};

// Visual states:
//   idle      → green RUN button
//   active    → live progress pill ("Compositing product (Qwen) · 1 image")
//   completed → green "Pipeline complete · N videos" pill (only when the job succeeds)
//   stalled   → amber warning + Re-run button (job failed, or no activity for 30 min)
export function RunControl({ running, onRun, runStartedAt, counts, currentStage, completed, stalled, onClear, canRun = true, disabledReason }) {
    const hasRun = !!runStartedAt;
    const blocked = !canRun;

    if (hasRun && completed) {
        const v = counts.videos;
        return (
            <div className="run-cluster">
                <div className="run-pill run-pill--done" title="The pipeline finished">
                    <CheckCircle2 />
                    <span>Pipeline complete{v ? ` · ${v} video${v === 1 ? '' : 's'}` : ''}</span>
                    <button type="button" className="run-pill-clear" onClick={onClear} aria-label="Dismiss">
                        <X />
                    </button>
                </div>
                <button
                    type="button"
                    className="btn btn-run"
                    onClick={onRun}
                    disabled={running || blocked}
                    title={blocked ? (disabledReason || 'Complete run settings first') : 'Run the pipeline again'}
                >
                    {running ? <span className="btn-spinner" aria-hidden="true" /> : <Play fill="currentColor" />}
                    <span>{running ? 'Starting…' : 'Run again'}</span>
                </button>
            </div>
        );
    }

    if (hasRun && stalled) {
        return (
            <div className="run-cluster">
                <div className="run-pill run-pill--stalled" title="The run failed, or had no activity for 30 minutes">
                    <AlertTriangle />
                    <span>Run stalled</span>
                    <button type="button" className="run-pill-clear" onClick={onClear} aria-label="Clear stalled run">
                        <X />
                    </button>
                </div>
                <button
                    type="button"
                    className="btn btn-run"
                    onClick={onRun}
                    disabled={running || blocked}
                    title={blocked ? (disabledReason || 'Complete run settings first') : 'Run the pipeline again'}
                >
                    {running
                        ? <span className="btn-spinner" aria-hidden="true" />
                        : <Play fill="currentColor" />}
                    <span>{running ? 'Starting…' : 'Re-run'}</span>
                </button>
            </div>
        );
    }

    if (hasRun) {
        const parts = [];
        if (counts.personas) parts.push(`${counts.personas} persona${counts.personas === 1 ? '' : 's'}`);
        if (counts.outputs)  parts.push(`${counts.outputs} image${counts.outputs === 1 ? '' : 's'}`);
        if (counts.videos)   parts.push(`${counts.videos} video${counts.videos === 1 ? '' : 's'}`);
        const summary = parts.join(' · ');
        const label = STAGE_LABELS[currentStage] || 'Pipeline running…';

        return (
            <div className="run-pill run-pill--active" title={`Started at ${new Date(runStartedAt).toLocaleTimeString()}`}>
                <span className="run-pill-dot" />
                <span>{label}{summary ? ` · ${summary}` : ''}</span>
                <button type="button" className="run-pill-clear" onClick={onClear} aria-label="Hide run status">
                    <X />
                </button>
            </div>
        );
    }

    return (
        <button
            type="button"
            className="btn btn-run"
            onClick={onRun}
            disabled={running || blocked}
            title={blocked ? (disabledReason || 'Complete run settings first') : 'Run the pipeline now'}
        >
            {running
                ? <span className="btn-spinner" aria-hidden="true" />
                : <Play fill="currentColor" />}
            <span>{running ? 'Starting…' : 'Run'}</span>
        </button>
    );
}
