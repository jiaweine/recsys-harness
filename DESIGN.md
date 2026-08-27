# RecSys Harness — Precision Workspace

A dense, evidence-first interface for an autonomous search and recommendation engineering workbench.

## Reference hierarchy

The primary structural reference is **Linear** from Awesome DESIGN.md: dense technical information, quiet hierarchy, hairline borders, compact operational rows, restrained elevation, and one muted indigo accent. Linear informs how information is organized; it does **not** require the whole product to use a near-black canvas.

The **regular product theme** uses Vercel-style neutral application chrome as a secondary reference: a soft gray canvas, white working surfaces, dark neutral text, subtle gray separators, and high-contrast primary actions. **Graphite Precision** remains the optional dark theme, preserving the existing near-black surface ladder for users who prefer it.

Use **Raycast** only as a secondary reference for command/execution feedback and keyboard-first controls. Do not copy external branding, logos, marketing composition, or decorative motifs.

The resulting design system is named **Precision Workspace** and preserves the Chinese “序枢” product identity.

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

1. **Evidence before decoration.** Running state, task state, ranked results, evidence, verification, and workspace context must be easier to scan than ornamental copy.
2. **One chromatic accent.** Use muted indigo for active navigation, progress, focus, signal bars, and agent-state emphasis. Green is reserved for verified/success states; orange is reserved for warnings and review states.
3. **Regular first, dark by choice.** New users start in the regular light workspace. Graphite dark remains a first-class user-selectable mode. Never force dark mode merely because a design reference uses a dark marketing canvas.
4. **Layer surfaces instead of flattening them.** Regular mode uses soft gray canvas → white workspace → subtle neutral sub-surfaces. Dark mode uses the Graphite near-black ladder. Neither mode should collapse into one flat color.
5. **Hairlines over cards.** Prefer 1px separators, surface shifts, and compact rows. Use elevation only for the composer, modal/sheet, authentication surfaces, and other true overlays.
6. **Compact but readable.** UI labels must be at least 10px, primary content is 11–14px, and welcome display text is 28–38px.
7. **Agent work must look live.** Execution uses progress, phase labels, trace rows, state changes, and explicit cycle IDs rather than decorative loading animation.
8. **Show real runtime intelligence.** Verification, cycles, tool calls, evidence, Critic confidence, memory hits, cost, ranking signals, diagnostic output, experiment deltas, mission requirements, hypotheses, permissions, and learned strategies may be surfaced only when present in the real Harness payload.
9. **No synthetic business dashboard.** Do not invent CTR, conversion, uplift, confidence bands, or recommendation metrics the runtime did not calculate.
10. **Show autonomy boundaries as product information.** Permissions, budgets, constraints, evaluation gates, rollback readiness, and persistence guarantees are part of the user experience, not implementation trivia.
11. **Theme changes presentation, not meaning.** Regular and dark modes expose the same content, states, interaction order, accessibility semantics, and verification hierarchy.
12. **Preserve product identity.** Chinese naming and the “序枢” brand remain first-class; this is not a skin of another product.

## Theme modes

### Regular — default

The regular theme is the default for new users and for no-JavaScript fallback. It is designed for long-running engineering work and daytime readability.

```css
--canvas: #f6f7f9;
--surface: #ffffff;
--surface-2: #fafafa;
--surface-3: #f1f2f4;
--ink: #18181b;
--ink-soft: #3f3f46;
--muted: #71717a;
--muted-2: #a1a1aa;
--line: #e4e4e7;
--line-strong: #d4d4d8;
--accent: #5e6ad2;
--accent-strong: #4f5bc1;
--success: #16855b;
--warning: #b95f18;
```

Regular mode should read as a professional application, not a blank white document: the outer canvas and navigation rail use soft gray, the primary workspace uses white, secondary panels use `#fafafa`, and borders do most of the grouping work.

### Graphite — optional dark

Graphite Precision is the user-selectable dark mode and preserves the existing low-luminance engineering-console character.

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

### Theme behavior

- The top bar exposes exactly two user choices: **常规** and **暗色**.
- The selected mode is persisted locally and restored before product styles paint so reload does not flash the wrong theme.
- Browser `theme-color` follows the active mode.
- Theme controls remain minimum-44px touch targets on mobile; the current implementation uses 46px height for layout-rounding headroom.
- Do not auto-switch based on operating-system theme while explicit product choices are available. The user’s stored selection wins.
- README/product-gallery captures use the regular theme by default so the primary presentation is not all-black.
- New components must be reviewed in both themes. Do not ship a light page containing isolated dark cards, or a dark page containing isolated light panels, unless the contrast has a semantic reason.

## Component rules

### Navigation

- In regular mode the sidebar is a soft-gray navigation rail separated from the white workspace by a hairline.
- In Graphite mode the sidebar remains the lowest-luminance functional rail.
- Active scenes use a restrained surface shift plus a 2px indigo rail in both themes.
- Hover must not move layout horizontally.
- “新任务” is a compact bordered control rather than a large marketing CTA.

### Run Library

- Recent runs are a navigation library, not a log dump.
- Every row exposes its real scene with a compact badge and keeps title/time readable without increasing row height excessively.
- Exactly one selected historical/current conversation may expose `aria-current="page"`.
- `当前` and `运行中` are explicit text states rather than color-only decoration.
- Selection moves synchronously on user interaction; asynchronous metadata enrichment must not leave one-frame stale current state.

### Run Context Strip

- The context strip is a compact identity/status line, not another card.
- Desktop may expose scene, concrete target, lifecycle state, persisted time, and verification when those values exist in real UI state.
- Execution lifecycle comes from the runtime state; interaction affordances such as “可继续追问” must not overwrite the completed lifecycle state.
- On mobile, prioritize scene, task identity, lifecycle, and verification. Lower-priority target/time details yield rather than creating horizontal scrolling.
- Context state must follow historical conversation switching and clear stale target/verification values when no longer applicable.

### Run Navigator / Workspace Switcher

- A completed run exposes a compact result navigator; incomplete or empty tasks do not carry persistent result chrome.
- Desktop navigation stays in the task header and jumps to Overview, Ranked Result, Strategy Experiment, Agent Trace, and Evidence without duplicating runtime data.
- Destinations that do not exist in the real completed payload remain unavailable rather than opening empty placeholders.
- `Cmd/Ctrl + K` opens a Raycast-inspired Workspace Switcher for current-run destinations, Workspace, input focus, new task, and recent persisted tasks.
- Recent-task commands reuse the Run Library as the source of truth; the palette does not create a second history model or refetch while merely opening/filtering.
- User-provided history titles are rendered as inert text, never executable markup.
- Command filtering is keyboard-operable with Up/Down, Enter, and Escape. Closing the palette restores or transfers focus deliberately.
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
- Historical reads must not pass through completed-run `fetch` observers; comparison uses a passive same-origin credentialed read path so old payloads cannot overwrite the active Snapshot, Trace, Control Plane, or Verification UI.
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
- The primary Execute action may remain high-contrast monochrome in regular mode; isolated black functional panels should not.

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
- Trace phase labels need enough geometric separation from their titles to remain readable on mobile.
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

- In regular mode the user prompt uses a restrained pale neutral/indigo surface with an indigo left edge; in Graphite it uses the existing graphite bubble.
- Assistant responses stay document-like rather than chat-bubble-like in both themes.
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
- **The mobile sheet follows the selected theme.** Regular mode uses white/light-neutral sheet surfaces; Graphite mode uses the near-black ladder. Mixed-theme sheet bodies are a visual regression.

## Shape, depth, motion

- Default functional radius: 7–10px. Avoid excessive pill/card styling.
- Depth is primarily surface color + border, following Linear-style restraint.
- Regular mode may use very soft shadows for true elevated objects; do not turn every result section into a floating card.
- Do not use generic gradients or glass blur in customer-facing surfaces.
- Default transition: 150–260ms.
- Motion communicates state change, not decoration.
- Respect `prefers-reduced-motion`.

## Accessibility

- Maintain visible focus states in both themes.
- Do not encode state with color alone.
- Preserve semantic buttons, labels, tab roles, native `details/summary`, and ARIA attributes.
- Minimum interactive target remains approximately 44px on touch layouts where controls are frequently tapped.
- Ranking and experiment tables must remain readable when signal columns wrap on narrow screens.
- Trace summaries must remain understandable while collapsed; expanded details are supplementary.
- Desktop and mobile product layouts must not introduce page-level horizontal scrolling.
- Command navigation must be fully usable without a pointer, and modal close should restore focus instead of dropping keyboard users at the document root.
- Theme selection uses semantic buttons and `aria-pressed`; the selected theme must remain understandable without relying on color.

## Product hygiene

- Customer-facing CSS has no remote font import, generic gradient, or glass blur.
- Customer-facing typography never falls below 10px.
- Frontend text must not leak third-party model/provider branding or internal ranking implementation terminology.
- All `frontend/*.js` files are included in product-hygiene scanning and JavaScript syntax checks in CI.
- New product surfaces must include regular and Graphite presentation rather than silently inheriting hard-coded dark colors.

## Visual QA contract

UI changes are verified against the **real running product**, not static mockups.

- Pull requests that change product UI or the design contract run FastAPI plus headless Chromium and upload a `visual-qa` artifact; PR runs never publish or rewrite README screenshots.
- README screenshots are published only from `main`, after real capture and immutable CDN verification, and are captured in the default **regular** theme.
- Browser QA must complete a real task and verify Run Snapshot, Ranked Result Analysis, Strategy Experiment, telemetry, Verification, Mission Graph, Agent Trace, Control Plane, and Learning Ledger from the completed payload.
- Search QA verifies real ranking rows and the `match`, `quality`, `freshness`, and `popularity` signal columns; strategy QA verifies independent gates plus current/candidate metric comparison.
- The no-adaptation QA task must never be presented as if a candidate strategy was activated.
- Progress/evidence tab counts must match the real trace/evidence rows.
- Run Navigator/Workspace Switcher QA verifies current-run navigation, recent-task switching, keyboard focus, filtering, Escape behavior, and inert rendering of user-provided task titles.
- Run Compare QA prepares real persisted history, verifies same-target context plus real Rank Movement and run-level facts, and asserts historical reads do not mutate the current Snapshot or Verification surface.
- Run Context QA verifies lifecycle identity independently from composer follow-up state and guards compact mobile identity/overflow behavior.
- Theme QA verifies a fresh user starts in regular mode, switches to Graphite, persists both directions across reload, updates browser theme chrome, keeps mobile controls touch-safe, and preserves non-overflowing layout in both modes.
- Desktop theme QA guards exact primary surface colors; mobile theme QA uses luminance tiers because responsive Graphite surfaces intentionally differ from desktop token values.
- Mobile inspector QA is theme-aware: regular sheet surfaces remain light; Graphite sheet surfaces remain low-luminance.
- QA deliberately simulates a transient run-polling failure and verifies the product recovers without leaving stale reconnect state visible after completion.
- Browser console errors and same-origin HTTP failures fail the visual QA run.
- The lead desktop capture is framed around ranked-result/experiment evidence, while the mobile progress capture is framed around real Agent Trace events so product documentation reflects the current console rather than legacy mockup composition.

## Implementation

The visual system is intentionally layered so product logic remains isolated:

- `frontend/theme-graphite.css` — Graphite dark-mode base and shared pre-theme product skin.
- `frontend/theme-modes.css` — regular-light tokens and component-level light presentation; loaded after product modules so it can neutralize hard-coded dark surfaces without rewriting runtime logic.
- `frontend/theme-controls.css` — shared responsive sizing contract for the user-facing theme chooser.
- `frontend/theme.js` — pre-paint theme restore, explicit regular/dark selection, `aria-pressed`, local persistence, and browser `theme-color` synchronization.
- `frontend/product-ui.css` / `product-ui.js` — runtime telemetry, ranked-result analysis, experiment gates, and run-result visualization.
- `frontend/trace-ui.css` / `trace-ui.js` — Mission Graph and Agent Trace presentation using structured runtime events only.
- `frontend/control-ui.css` / `control-ui.js` — Control Plane and Learning Ledger presentation.
- `frontend/run-nav.css` / `run-nav.js` — compact run navigation and Workspace Switcher.
- `frontend/run-compare.css` / `run-compare.js` — persisted-run factual comparison and same-target rank movement.
- `frontend/run-library.css` / `run-library.js` — scene-aware recent-run navigation and current/running state.
- `frontend/run-context.css` / `run-context.js` — compact run identity/lifecycle strip.
- `scripts/check_theme_modes.py` — real Chromium verification of regular/dark defaults, persistence, primary surfaces, mobile touch targets, and overflow.
- `scripts/capture_readme_assets.py` — real-browser desktop/mobile product QA and regular-theme screenshot capture.
- `.github/workflows/readme-assets.yml` — PR visual-QA artifact generation plus main-only README screenshot publication.

Product modules create their surfaces only when the corresponding real payload exists. Theme selection changes presentation only. Search/recommendation algorithms, Agent Harness Runtime, Verifier logic, persistence, and evolution behavior remain independent from the visual layer.
