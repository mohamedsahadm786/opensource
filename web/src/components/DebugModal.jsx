import { Bug, X } from 'lucide-react';
import { Modal } from './Modal.jsx';
import { useOutputDebug } from '../hooks/useOutputDebug.js';

// Per-output prompt inspector (Brief v2 §7). Read-only.
//   mode='image' → image stages (image_generations: step1/step2/step3) + the
//                  Opus prompt-builder calls that produced them.
//   mode='video' → video shots (media_generations: Wan motion + dialogue) + the
//                  script-generation call.
export function DebugModal({ open, row, mode = 'image', onClose }) {
    const { images, media, llm, status } = useOutputDebug(open ? row?.id : null, open ? row?.video?.id : null);
    if (!open || !row) return null;

    const isVideo = mode === 'video';
    const title = isVideo ? 'Video prompts for this output' : 'Image prompts for this output';
    // llm_calls.purpose: 'script' is the video script; everything else
    // (phasea/step1/step2/qc) built the image.
    const llmRows = (llm || []).filter((c) => (isVideo ? c.purpose === 'script' : c.purpose !== 'script'));
    const nothing = isVideo
        ? media.length === 0 && llmRows.length === 0
        : images.length === 0 && llmRows.length === 0;

    return (
        <Modal open={open} onClose={onClose} labelledBy="debug-title">
            <div className="modal-card" style={{ maxWidth: 720, width: '92vw', maxHeight: '88vh', display: 'flex', flexDirection: 'column' }}>
                <div className="modal-head">
                    <div>
                        <p className="modal-eyebrow"><Bug size={13} style={{ verticalAlign: '-2px' }} /> {row.scenario_id || row.id}</p>
                        <h2 id="debug-title">{title}</h2>
                    </div>
                    <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X /></button>
                </div>

                <div className="modal-body" style={{ overflowY: 'auto' }}>
                    {status === 'loading' && <div className="state state-loading"><div className="spinner" /><p>Loading prompts…</p></div>}
                    {status === 'ready' && nothing && (
                        <p className="ana-empty">No stored {isVideo ? 'video' : 'image'} prompts for this output yet.</p>
                    )}

                    {!isVideo && images.length > 0 && (
                        <Section title="Image stages (image_generations)">
                            {images.map((g, i) => (
                                <Block key={i} head={`${g.stage_name} · seed ${g.seed ?? '—'} · cfg ${g.cfg ?? '—'}`}>
                                    <Field label="prompt" value={g.prompt} />
                                    <Field label="negative" value={g.negative_prompt} />
                                    <Field label="mask" value={g.mask_prompt} />
                                </Block>
                            ))}
                        </Section>
                    )}

                    {isVideo && media.length > 0 && (
                        <Section title="Video shots (media_generations)">
                            {media.map((m, i) => (
                                <Block key={i} head={m.stage_name}>
                                    <Field label="motion prompt" value={m.prompt} />
                                    <Field label="negative" value={m.negative_prompt} />
                                    <Field label="dialogue" value={m.params?.dialogue} />
                                </Block>
                            ))}
                        </Section>
                    )}

                    {llmRows.length > 0 && (
                        <Section title={isVideo ? 'Script generation (llm_calls)' : 'Prompt-builder calls (llm_calls)'}>
                            {llmRows.map((c, i) => (
                                <Block key={i} head={c.purpose}>
                                    <Field label="user message" value={c.user_message} />
                                    <Field label="raw response" value={c.raw_response} />
                                </Block>
                            ))}
                        </Section>
                    )}
                </div>

                <div className="modal-foot">
                    <button type="button" className="btn btn-primary" onClick={onClose}>Close</button>
                </div>
            </div>
        </Modal>
    );
}

function Section({ title, children }) {
    return (
        <section style={{ marginBottom: 16 }}>
            <p className="ana-subhead">{title}</p>
            {children}
        </section>
    );
}
function Block({ head, children }) {
    return (
        <div className="setup-card" style={{ marginTop: 8, gap: 8 }}>
            <p className="rubric-group-label" style={{ margin: 0 }}>{head}</p>
            {children}
        </div>
    );
}
function Field({ label, value }) {
    if (!value) return null;
    return (
        <div>
            <p className="field-label" style={{ marginBottom: 2 }}>{label}</p>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-muted)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px' }}>{value}</pre>
        </div>
    );
}
