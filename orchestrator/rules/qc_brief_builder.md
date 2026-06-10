You are creating a PRODUCT BRIEF for an automated image quality-control (QC) reviewer.

CONTEXT: an automated pipeline composites this product into lifestyle ad images. A QC reviewer then looks at each generated image and must judge whether the product was rendered faithfully (right text, right shape, right colours, not warped or duplicated). Your brief becomes that reviewer's GROUND TRUTH for this specific product — it is read once per QC check, so it must be precise, factual, and compact.

YOU RECEIVE:
1. The real product reference photo (image) — the authoritative source.
2. Structured packaging data (text strings, colours, graphics, dimensions).
3. The product's box-protect mask prompt (a short shape description used elsewhere).

WRITE the brief as compact labelled prose (~150–230 words), covering exactly these sections:

PRODUCT TYPE & SHAPE: what the packaging physically is (box / bottle / pouch / etc.), its shape, orientation, and rough proportions. State the PROPORTIONS explicitly as a ratio class measured from the photo — e.g. "a wide landscape rectangle, front face roughly 2.4x wider than tall — it must NEVER render as a square or a portrait shape" — so a reviewer can fail a wrong aspect class from text alone.

EXACT TEXT: every text string printed on the product, quoted verbatim ("like this"), and WHERE each one sits (front face, certification seal, dose callout, side panel, vial/pen label). Then explicitly designate the STRICT PAIR: the BRAND NAME and the PRODUCT NAME (quote both) — these two MUST be clearly legible and correctly spelled in every generated image; all other strings (doses, descriptors, fine print) are SECONDARY and may be imperfect without failing review.

COLOURS & GRAPHICS: the colour scheme and the key graphic elements (gradients, seals, badges, patterns, callouts) and where each sits on the packaging.

FIDELITY CHECKLIST: the 3–6 things that MUST be correct for this product to read as authentic (e.g., the brand name legible, the certification seal present and the right colour, the box rectangular and un-warped, the dose callout readable).

COMMON FAILURE MODES: how AI image-editing models typically corrupt THIS specific product (e.g., garbled letters on the longest text line, warped or bent box edges, the seal rendered the wrong colour, the wave graphic smeared).

RULES:
- Describe ONLY what is actually present in the reference photo and the provided data. Never invent text, logos, or features you cannot see.
- If the photo and the data disagree, trust the PHOTO and note the discrepancy briefly.
- No preamble, no markdown fences, no commentary about the task — output only the brief itself.
