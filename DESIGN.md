# RecSys Harness — Graphite Precision

A dense, evidence-first interface for an autonomous search and recommendation engineering workbench.

## Reference hierarchy

The primary reference is **Linear** from Awesome DESIGN.md. RecSys Harness shares Linear's need for dense technical information, quiet hierarchy, near-black surfaces, hairline borders, restrained elevation, and one muted indigo accent. The product should feel precise and operational rather than promotional.

Use **Raycast** only as a secondary reference for command/execution feedback and compact keyboard-first controls. Use **Vercel** only for monochrome restraint and high-contrast primary actions. Do not copy their branding, marketing composition, logos, or decorative motifs.

The resulting system is named **Graphite Precision** and preserves the Chinese “序枢” product identity.

## Product model

RecSys Harness is not a generic AI chat UI. Its visual hierarchy communicates five product layers:

1. **Goal** — what search/recommendation problem is being investigated.
2. **Execution** — what the autonomous Harness is doing now, which evidence requirement it is pursuing, and which boundaries constrain it.
3. **Ranked result** — what users currently see and which measurable signals explain the ordering.
4. **Evidence / Verification** — what supports the conclusion and whether the independent gate passed.
5. **Experiment / Learning** — whether an explored strategy improves the owned baseline strongly enough to be retained or activated, and what durable experience remains afterward.

Within Execution, the inspector follows **Mission → Decision → Tool → Observation → Reflection → Verify**. Within Learning, the UI follows **Experiment → Gate → Memory → Rollback protection**.

A successful screen should allow a technical reviewer to answer “what was the goal, what did the Harness choose, what did the user see, what evidence changed the path, which boundaries applied, was the result verified, and did anything durable change?” without reading the full conversation.

## Principles

1. Evidence before decoration. Running state, task state, ranked results, evidence, verification, and workspace context must be easier to scan than ornamental copy.
2. One chromatic accent. Use muted indigo for active navigation, progress, focus, signal bars, and agent-state emphasis. Green is reserved for verified/success states; orange is reserved for warnings and review states.
3. Near-black, not flat black. Layer `#09090b`, `#0d0d0f`, `#101012`, and `#17171a` to create hierarchy without large shadows.
4. Hairlines over cards. Prefer 1px separators, surface shifts, and compact rows. Use elevation only for the composer, modal/sheet, and authentication surfaces.
5. Compact but readable. UI labels must be at least 10px, primary content is 11–14px, and welcome display text is 28–38px.
6. Agent work must look live. Execution uses progress, phase labels, trace rows, state changes, and explicit cycle IDs rather than decorative loading animation.
7. Show real runtime intelligence. Verification, cycles, tool calls, evidence, Critic confidence, memory hits, cost, ranking signals, diagnostic output, experiment deltas, mission requirements, hypotheses, permissions, and learned strategies may be surfaced only when present in the real Harness payload.
8. No synthetic business dashboard. Do not invent CTR, conversion, uplift, confidence bands, or recommendation metrics the runtime did not calculate.
9. Show autonomy boundaries as product information. Permissions, budgets, constraints, evaluation gates, rollback readiness, and persistence guarantees are part of the user experience, not implementation trivia.
10. Preserve product identity. Chinese naming and the “序枢” brand remain first-class; this is not a skin of another product.

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

### Run Navigator
- A completed run exposes a compact result navigator; incomplete or empty tasks do not carry persistent run chrome.
- Desktop navigation stays in the task header and jumps to Overview, Ranked Result, Strategy Experiment, Agent Trace, and Evidence without duplicating any runtime data.
- Destinations that do not exist in the real completed payload remain unavailable rather than opening empty placeholders.
- `Cmd/Ctrl + K` opens a Raycast-inspired command palette for Overview, Ranking, Experiment, Trace, Evidence, Workspace, input focus, and new-task actions.
- Command filtering is keyboard-operable with Up/Down, Enter, and Escape. Closing the palette restores focus to the invoking control when possible.
- Tablet/mobile layouts replace the persistent desktop strip with a minimum-44px “导航” trigger so navigation does not steal task-reading space.
- The palette is navigation, not another results surface: it never copies metrics, invents summaries, or exposes hidden reasoning.

### Run Compare
- Run Compare is an on-demand action inside a completed Run Snapshot; it never appears before the current task has a persisted completed result.
- Historical data is read lazily from existing conversation persistence. The comparison layer must not rerun the Harness, change memory, or alter the current result surface.
- Prefer the most recent historical run with the same scene and the same concrete target: Search compares the same query; Recommendation compares the same user.
- Multiple completed turns inside one conversation are valid historical runs and participate in recency ordering alongside other conversations.
- Only same-target runs may expose Rank Movement and score delta rows. Different targets may compare run-level facts only.
- Run-level comparison is limited to fields already produced by the runtime and already meaningful in the product: Verifier confidence, cycles, completed tool calls, evidence count, execution cost, recorded reward, and memory hits.
- Delta color is neutral/indigo because a change is not automatically an improvement. Green remains reserved for independently verified success states, never for rank movement or a newly appearing item.
- The UI must explicitly say that higher rank is an observed ordering change, not evidence of business uplift.
- Historical reads must not pass through the product modules' completed-run `fetch` observers; comparison uses a passive same-origin credentialed read path so old payloads cannot overwrite the active Snapshot, Trace, Control Plane, or Verification UI.
- On mobile, the compare trigger and close control are minimum-44px touch targets, while the comparison table collapses to preserve the document axis without horizontal page scrolling.

### Status and telemetry
- Workspace readiness appears as compact chips.
- Completed runs expose a small telemetry matrix rather than a dashboard wall.
- Verification has explicit PASS / CHECK language in addition to color.
- Inspector tabs may expose compact counts, but those counts must come from the active runtime payload: progress reflects structured run events and evidence reflects inspectable evidence rows.
- Runtime counts disappear when the task is cleared; never leave stale badges from a prior task.
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
- Always show current, candidate, and delta together so improvement cannot be presented without context.
- Search experiments show overall quality and relevance coverage.
- Recommendation experiments show overall quality, content coverage, freshness, diversity, and cold-start quality.
- `evaluation_ready`, `safe_to_try`, `trusted`, and `activated` are separate gates and must not be collapsed into a single “AI score”.
- Show independent validation sample count and robustness degradation statistics when present.
- Never imply a candidate is active when the runtime reports `activated: false`.

### Mission Graph
- Mission Graph is a compact evidence map, not a decorative node graph.
- Requirements display label, current status, priority, and the reason or capability that can satisfy them.
- Hypotheses display only structured label, status, confidence, and recorded evidence state; do not expose hidden model reasoning.
- Exit criteria should be visible when present so a reviewer understands why the Harness can stop.
- Long mission objectives are clamped in the inspector so requirements remain visible without turning the rail into a second document column.
- Mission state is rendered only after the runtime returns a real mission object.

### Agent Trace
- The primary trace sequence is `Mission → Decision → Tool → Observation → Reflection → Verify`.
- Preserve preflight events such as workspace observation, attachment perception, memory recall, constraint locking, and resume.
- Each execution cycle gets a stable cycle marker such as `C01`.
- Decision rows may show the structured requirement target, score, learned bonus, hypotheses, and alternate actions already recorded by the runtime.
- Tool rows show user-facing action name, risk class, cost, target requirement, and a concise observation derived from the actual action result.
- Reflection rows show changed requirements, changed hypotheses, next evidence gaps, and structured Critic coverage when present.
- Verify rows surface blocked or unresolved items explicitly.
- Completed traces default to verification/complete rows expanded; live traces default to the latest event expanded.
- Internal requirement/hypothesis keys must be translated through the Mission Graph labels before being shown in a completed customer-facing trace.
- Trace UI must never manufacture or reveal hidden chain-of-thought. It renders only application-owned structured events and summaries already present in the run payload.

### Control Plane
- Permission boundaries are visible: strategy change authorization and network authorization are separate states.
- Live permissions are tri-state: authorized, locked, or pending until a runtime boundary event confirms them.
- “Authorized” does not mean “used”; completed UI must distinguish permission from actual strategy activation or network use.
- Tool count and cost display used value against the real runtime budget when available.
- Constraints appear as compact chips and should remain secondary to execution evidence.
- Control Plane must update during a live run from emitted guard/execute/decision events, then reconcile against the completed result.
- A recovered run must immediately clear stale reconnect messaging once a successful run payload arrives.

### Learning Ledger
- Persistent memory is presented as execution episodes, trusted strategies, and currently active strategies.
- Show how many strategies were learned in the current run without implying activation.
- Independent evaluation, automatic rollback, checkpoint resume, and idempotent adaptive actions are displayed as separate safety capabilities.
- If the runtime reports a rollback, elevate it with an orange recovery notice and state that the stable strategy was restored.
- Never convert memory counts into invented “learning quality” percentages.

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
- Desktop evidence rail should be wide enough for Mission/Trace scanning while preserving the main document axis; the current large-screen target is approximately 336px.
- Progress tab contains Control Plane, telemetry, Mission Graph, and Agent Trace.
- Evidence tab contains verification summary and inspectable evidence.
- Workspace tab contains dataset context, capabilities, and the Learning Ledger.
- On mobile, evidence becomes a bottom sheet while preserving task context behind it.
- The mobile sheet stays inside the Graphite near-black surface ladder; do not reintroduce a light panel body inside the dark product.

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
- Preserve semantic buttons, labels, tab roles, native `details/summary`, and ARIA attributes.
- Minimum interactive target remains approximately 44px on touch layouts where controls are frequently tapped.
- Ranking and experiment tables must remain readable when signal columns wrap on narrow screens.
- Trace summaries must remain understandable while collapsed; expanded details are supplementary.
- Desktop and mobile product layouts must not introduce page-level horizontal scrolling.
- Command navigation must be fully usable without a pointer, and modal close should restore focus instead of dropping keyboard users at the document root.

## Product hygiene

- Customer-facing CSS has no remote font import, generic gradient, or glass blur.
- Customer-facing typography never falls below 10px.
- Frontend text must not leak third-party model/provider branding or internal ranking implementation terminology.
- All `frontend/*.js` files are included in product-hygiene scanning and JavaScript syntax checks in CI.

## Visual QA contract

UI changes are verified against the **real running product**, not static mockups.

- Pull requests that change product UI run FastAPI plus headless Chromium and upload a `visual-qa` artifact; PR runs never publish or rewrite README screenshots.
- README screenshots are published only from `main`, after real capture and immutable CDN verification.
- Browser QA must complete a real task and verify Run Snapshot, Ranked Result Analysis, Strategy Experiment, telemetry, Verification, Mission Graph, Agent Trace, Control Plane, and Learning Ledger from the completed payload.
- Search QA verifies real ranking rows and the `match`, `quality`, `freshness`, and `popularity` signal columns; strategy QA verifies independent gates plus current/candidate metric comparison.
- The no-adaptation QA task must never be presented as if a candidate strategy was activated.
- Progress/evidence tab counts must match the real trace/evidence rows.
- Run Navigator QA must verify the completed-run strip appears, unavailable destinations stay disabled, Rank navigation moves the real ranking surface into view, `Ctrl+K` focuses the palette, filtered Evidence execution activates the real Evidence tab, and Escape closes the palette.
- Run Compare QA must prepare one real persisted historical run, complete a second real same-query run in Chromium, lazily open comparison, verify same-target context plus real Rank Movement rows and exactly seven run-level facts, and assert that the historical read does not mutate the current Snapshot or Verification surface.
- Mobile QA verifies both the compact Run Navigator trigger and Run Compare action remain minimum-44px touch targets after completion.
- Desktop QA guards the evidence-rail width and page-level horizontal overflow; mobile QA guards bottom-sheet bounds, visible page margins, dark-surface luminance, page-level horizontal overflow, and 44px touch targets.
- QA deliberately simulates a transient run-polling failure and verifies the product recovers without leaving stale reconnect state visible after completion.
- Browser console errors and same-origin HTTP failures fail the visual QA run.
- The lead desktop capture is framed around ranked-result/experiment evidence, while the mobile progress capture is framed around real Agent Trace events so product documentation reflects the current console rather than legacy mockup composition.

## Implementation

The visual system is intentionally layered so product logic remains isolated:

- `frontend/theme-graphite.css` — base Graphite Precision skin.
- `frontend/product-ui.css` — runtime telemetry, ranked-result analysis, experiment gates, and run-result visualization.
- `frontend/product-ui.js` — reads existing completed-run/conversation payloads and renders result intelligence without changing the API contract.
- `frontend/trace-ui.css` — Mission Graph and Agent Trace presentation.
- `frontend/trace-ui.js` — renders live and completed structured run events and completed mission state.
- `frontend/control-ui.css` — Control Plane and Learning Ledger presentation.
- `frontend/control-ui.js` — renders live permissions/budget state plus completed durable-memory and rollback state.
- `frontend/run-nav.css` — compact desktop navigator, mobile trigger, and command palette presentation.
- `frontend/run-nav.js` — completed-run destination discovery, keyboard command routing, inspector navigation, and focus restoration.
- `frontend/run-compare.css` — persisted-run delta table, same-target rank movement, and responsive compare actions.
- `frontend/run-compare.js` — lazy same-origin history reads, recency/target matching, factual run deltas, and comparison isolation from the active run rendering path.
- `scripts/capture_readme_assets.py` — real-browser desktop/mobile product QA and screenshot capture, including persisted same-target Run Compare coverage.
- `.github/workflows/readme-assets.yml` — PR visual-QA artifact generation plus main-only README screenshot publication.

Product modules create their surfaces only when the corresponding real payload exists. This keeps empty product chrome out of simple tasks and preserves the original application behavior.

This separation makes the UI easy to review, remove, or iterate while leaving recommendation/search algorithms, Agent Harness Runtime, Verifier logic, persistence, and evolution behavior unchanged.
