import { Cpu, RefreshCw, Sparkles, TrendingUp } from 'lucide-react';
import { useEngine } from '../hooks/useEngine.js';

// Read-only window into the learning/tuning engine for this tenant. All written
// by the orchestrator / recompute job — the web only displays.
export function EnginePanel({ tenantId }) {
    const { progress, learning, stats, suggestions, status, error, reload } = useEngine(tenantId);

    return (
        <section className="panel">
            <header className="panel-head">
                <div>
                    <h2>Learning engine</h2>
                    <p className="panel-sub">Exploration coverage, learned beliefs, and tuning suggestions (read-only).</p>
                </div>
                <div className="filters">
                    <button type="button" className="btn btn-ghost btn-sm" onClick={reload}><RefreshCw /><span>Refresh</span></button>
                </div>
            </header>

            {status === 'loading' && <div className="state state-loading"><div className="spinner" /><p>Loading engine state…</p></div>}
            {status === 'error' && (
                <div className="state state-error">
                    <Cpu strokeWidth={1.5} />
                    <h3>Couldn't load engine state</h3>
                    <p>{error?.message || 'Please try again.'}</p>
                    <button type="button" className="btn btn-ghost" onClick={reload}>Try again</button>
                </div>
            )}

            {status === 'ready' && (
                <div className="analytics-body">
                    {/* Phase + coverage */}
                    <div className="analytics-grid analytics-grid-2">
                        <div className="ana-card">
                            <div className="ana-card-head">
                                <span className="ana-card-icon ana-card-icon--violet"><Sparkles /></span>
                                <div><h3>Engine state</h3><p>Exploration → active lifecycle</p></div>
                            </div>
                            <ul className="dist-list">
                                <li className="dist-row"><div className="dist-row-head"><span className="dist-label">Phase</span><span className="dist-count">{learning?.phase || 'exploration'}</span></div></li>
                                <li className="dist-row"><div className="dist-row-head"><span className="dist-label">Engine enabled</span><span className="dist-count">{learning?.engine_enabled ? 'yes' : 'no'}</span></div></li>
                                <li className="dist-row"><div className="dist-row-head"><span className="dist-label">Min coverage to flip</span><span className="dist-count">{learning?.min_coverage_pct ?? 100}%</span></div></li>
                            </ul>
                        </div>
                        <div className="ana-card">
                            <div className="ana-card-head">
                                <span className="ana-card-icon ana-card-icon--green"><TrendingUp /></span>
                                <div><h3>Exploration progress</h3><p>Curated scenarios worked through</p></div>
                            </div>
                            <div className="funnel-step">
                                <div className="funnel-meta">
                                    <span className="funnel-label">{progress?.resolved_curated ?? 0} / {progress?.active_curated ?? 0} resolved</span>
                                    <span className="funnel-count">{progress?.pct_complete ?? 0}%</span>
                                </div>
                                <div className="funnel-bar-wrap">
                                    <div className="funnel-bar funnel-bar--green" style={{ width: `${progress?.pct_complete ?? 0}%` }} />
                                </div>
                                <p className="funnel-note">{progress?.is_complete ? 'Curated set complete — engine can select via Thompson sampling.' : 'Still walking the curated set.'}</p>
                            </div>
                        </div>
                    </div>

                    {/* Learned beliefs */}
                    <div className="ana-card">
                        <div className="ana-card-head"><h3>Learned attribute beliefs</h3><p>Top by sample size (attribute_stats)</p></div>
                        {stats.length === 0 ? <p className="ana-empty">No learning data yet.</p> : (
                            <div className="table-wrap">
                                <table className="data-table">
                                    <thead><tr><th>Attribute</th><th>Dimension</th><th>Kind</th><th>n</th><th>Estimate</th></tr></thead>
                                    <tbody>
                                        {stats.slice(0, 40).map((s, i) => (
                                            <tr key={i}>
                                                <td className="td-handle">{s.attribute_key}</td>
                                                <td>{s.dimension}</td>
                                                <td>{s.kind}</td>
                                                <td>{s.n}</td>
                                                <td>{s.estimate == null ? '—' : Number(s.estimate).toFixed(3)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {/* Tuning suggestions */}
                    <div className="ana-card">
                        <div className="ana-card-head"><h3>Tuning suggestions</h3><p>Prompt/script edits the engine proposes</p></div>
                        {suggestions.length === 0 ? <p className="ana-empty">No tuning suggestions yet.</p> : (
                            <div className="table-wrap">
                                <table className="data-table">
                                    <thead><tr><th>Scope</th><th>Dimension</th><th>Suggested edit</th><th>Status</th><th>Δ</th></tr></thead>
                                    <tbody>
                                        {suggestions.map((t, i) => (
                                            <tr key={i}>
                                                <td className="td-handle">{t.scope_key}</td>
                                                <td>{t.dimension}</td>
                                                <td>{t.suggested_edit || '—'}</td>
                                                <td><span className="tag">{t.status}</span></td>
                                                <td>{t.score_delta == null ? '—' : Number(t.score_delta).toFixed(2)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </section>
    );
}
