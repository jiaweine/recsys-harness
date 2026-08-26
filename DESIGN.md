# RecSys Harness — Graphite Precision

A dense, evidence-first interface for an autonomous search and recommendation workbench.

## Reference hierarchy

The primary reference is **Linear** from Awesome DESIGN.md. RecSys Harness shares Linear's need for dense technical information, quiet hierarchy, near-black surfaces, hairline borders, restrained elevation, and one muted indigo accent. The product should feel precise and operational rather than promotional.

Use **Raycast** only as a secondary reference for command/execution feedback and compact keyboard-first controls. Use **Vercel** only for monochrome restraint and high-contrast primary actions. Do not copy their branding, marketing composition, logos, or decorative motifs.

The resulting system is named **Graphite Precision** and preserves the Chinese “序枢” product identity.

## Product model

RecSys Harness is not a generic AI chat UI. Its visual hierarchy communicates five layers:

1. **Goal** — what search/recommendation problem is being investigated.
2. **Execution** — what the autonomous Harness is doing now and why.
3. **Ranked result** — what users currently see and which measurable signals explain the ordering.
4. **Evidence / Verification** — what supports the conclusion and whether the independent gate passed.
5. **Experiment / Learning** — whether an explored strategy improves the owned baseline strongly enough to be retained or activated.

A successful screen should allow a technical reviewer to answer “what did it do, what did the user see, why was it ranked this way, what evidence supports the conclusion, and did the candidate strategy pass?” without reading the full conversation.

## Principles

1. Evidence before decoration. Running state, task state, ranked results, evidence, verification, and workspace context must be easier to scan than ornamental copy.
2. One chromatic accent. Use muted indigo for active navigation, progress, focus, signal bars, and agent-state emphasis. Green is reserved for verified/success states; orange is reserved for warnings and review states.
3. Near-black, not flat black. Layer `#09090b`, `#0d0d0f`, `#101012`, and `#17171a` to create hierarchy without large shadows.
4. Hairlines over cards. Prefer 1px separators, surface shifts, and compact rows. Use elevation only for the composer, modal/sheet, and authentication surfaces.
5. Compact but readable. UI labels must be at least 10px, primary content is 11–14px, and welcome display text is 28–38px.
6. Agent work must look live. Execution uses progress, phase labels, trace rows, and state changes rather than decorative loading animation.
7. Show real runtime intelligence. Verification, cycles, tool calls, evidence, Critic confidence, memory hits, cost, ranking signals, diagnostic output, experiment deltas, and learned strategies may be surfaced only when present in the real Harness payload.
8. No synthetic business dashboard. Do not invent CTR, conversion, uplift, confidence bands, or recommendation metrics the runtime did not calculate.
9. Preserve product identity. Chinese naming and the “序枢” brand remain first-class; this is not a skin of another product.

## Core tokens

```css
--canvas: #09090b;
--surface: #111113;
--surface-2: #161619;
--surface-3: #202024;
--ink: #f4f4f5;
--ink-soft: #d4d4d8;
--muted: #a1a1aa;
--muted-2: #71717a;
--line: #28282d;
--line-strong: #3a3a40;
--accent: #5e6ad2;
--accent-strong: #8b93e8;
--success: #34d399;
--warning: #fb923c;
```

## Component rules

### Navigation
- Sidebar is the darkest functional surface.
- Active scenes use a graphite row plus a 2px indigo rail.
- Hover must not move layout horizontally.
- “新任务” is a compact bordered control rather than a large marketing CTA.

### Status and telemetry
- Workspace readiness appears as compact chips.
- Completed runs expose a small telemetry matrix rather than a dashboard wall.
- Verification has explicit PASS / CHECK language in addition to color.
- Metrics must come from existing runtime output; never fabricate demo numbers.

### Main workspace
- The document axis remains dominant.
- Hero copy can be editorial, but subsequent surfaces are operational and compact.
- Suggested prompts are list rows with separators, not standalone marketing cards.
- A completed run starts with Run Snapshot, then optional ranked-result analysis and strategy experiment surfaces.

### Ranked result analysis
- Search and recommendation output is displayed as a dense table, not ecommerce-style cards.
- Keep rank and total score visually stable at the row edges.
- Search rows expose the real `match`, `quality`, `freshness`, and `popularity` signals.
- Recommendation rows expose the real `fit`, `quality`, `freshness`, and `novelty` signals.
- Signal bars are explanatory aids for the underlying numeric values; they are not new metrics.
- Diagnosis belongs directly above the affected ranking and must reuse the real diagnose action output.
- Warning badges may only reflect an existing runtime rule, such as weak top-match evidence, cold start, or an insufficient eligible candidate pool.

### Strategy experiment
- Experiment UI appears only when an evolution action actually ran.
- Always show baseline, candidate, and delta together so improvement cannot be presented without context.
- Search experiments show overall quality and relevance coverage.
- Recommendation experiments show overall quality, content coverage, freshness, diversity, and cold-start quality.
- `evaluation_ready`, `safe_to_try`, `trusted`, and `activated` are separate gates and must not be collapsed into a single “AI score”.
- Show independent validation sample count and robustness degradation statistics when present.
- Never imply a candidate is active when the runtime reports `activated: false`.

### Conversation
- User prompts use a restrained graphite bubble with an indigo left edge.
- Assistant responses stay document-like rather than chat-bubble-like.
- Structured runtime output complements the written conclusion; it does not replace it.

### Agent running state
- Indigo is reserved for active progress and focus.
- Running state should resemble a live execution trace, not a generic spinner.
- Stop/cancel remains visually secondary until interaction.

### Composer
- Composer is the main elevated object in the workspace.
- Focus uses a soft indigo ring.
- Execute is a high-contrast monochrome action in the Vercel spirit, without Vercel branding.

### Evidence inspector
- Inspector is supporting context, not a second primary canvas.
- Progress tab contains execution telemetry and trace.
- Evidence tab contains verification summary and inspectable evidence.
- On mobile, evidence becomes a bottom sheet while preserving task context behind it.

## Shape, depth, motion

- Default functional radius: 7–10px. Avoid excessive pill/card styling.
- Depth is primarily surface color + border, following Linear-style restraint.
- Do not use generic gradients or glass blur in customer-facing surfaces.
- Default transition: 150–260ms.
- Motion communicates state change, not decoration.
- Respect `prefers-reduced-motion`.

## Accessibility

- Maintain visible focus states.
- Do not encode state with color alone.
- Preserve semantic buttons, labels, tab roles, and ARIA attributes.
- Minimum interactive target remains approximately 44px on touch layouts.
- Ranking and experiment tables must remain readable when signal columns wrap on narrow screens.

## Implementation

The visual system is intentionally layered so product logic remains isolated:

- `frontend/theme-graphite.css` — base Graphite Precision skin.
- `frontend/product-ui.css` — runtime telemetry, ranked-result analysis, experiment gates, and run-result visualization.
- `frontend/product-ui.js` — reads existing completed-run/conversation payloads and renders runtime intelligence without changing the API contract.

`product-ui.js` creates result-analysis and experiment surfaces only when real payloads contain the corresponding actions. This keeps empty product chrome out of simple tasks and preserves the original application behavior.

This separation makes the UI easy to review, remove, or iterate while leaving recommendation/search algorithms and Harness Runtime behavior unchanged.
