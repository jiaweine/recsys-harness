# RecSys Harness — Graphite Precision

A dense, evidence-first interface for an autonomous search and recommendation workspace. The visual language borrows the information hierarchy of Linear and the restraint of Vercel without copying either product.

## Principles

1. Evidence before decoration. Running state, task state, evidence, and workspace context must be visually easier to scan than ornamental copy.
2. One accent only. Use violet for active navigation, progress, focus, and capability emphasis. Do not introduce competing brand colors.
3. Near-black, not pure black. Layer `#09090b`, `#0d0d0f`, `#101012`, and `#17171a` to create hierarchy without large shadows.
4. Thin borders over cards. Prefer 1px separators and subtle inset highlights. Use elevation only for the composer, sheets, and authentication surfaces.
5. Compact but readable. UI labels are 10–12px, primary content 13–14px, and welcome display text is 28–38px.
6. Agent work must look live. Running state uses a restrained violet pulse and progress line; successful readiness uses green only.
7. Preserve product identity. Chinese naming and the “序枢” brand remain first-class; this is not a skin of another product.

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
--accent: #8b7cf6;
--accent-strong: #a79cff;
--success: #34d399;
--warning: #fb923c;
```

## Component rules

### Navigation
- Sidebar is the darkest functional surface.
- Active scenes use a filled graphite row plus a 2px violet rail.
- Hover should not move layout horizontally.
- “新任务” is a compact bordered control rather than a large primary CTA.

### Status chips
- Default status chips use graphite fill and a 1px border.
- Readiness is represented with a small green dot.
- Quiet capability states remain text-first and low contrast.

### Main workspace
- The document axis is dominant; avoid dashboard-card grids in the central canvas.
- Hero copy may be editorial, but all following interaction surfaces must be compact and operational.
- Suggested prompts are list rows with separators, not standalone cards.

### Conversation
- User prompts use a restrained graphite bubble with violet left edge.
- Assistant responses remain document-like rather than chat-bubble-like.
- Headings are high contrast; body copy is softer to preserve long-read comfort.

### Agent running state
- Violet is reserved for active progress and focus.
- A running block should feel like a live execution trace, not a loading spinner.
- Stop/cancel remains visually secondary until hovered.

### Composer
- Composer is the main elevated object in the workspace.
- Focus state uses a soft violet ring.
- Execute action is high-contrast white-on-dark inversion for fast acquisition.

### Evidence inspector
- Inspector is supporting context, not a second primary canvas.
- Timeline nodes and tab state use violet sparingly.
- On mobile, evidence becomes a bottom sheet while preserving task context behind it.

## Motion
- Default transition: 150–260ms.
- Motion communicates state change, not decoration.
- Respect `prefers-reduced-motion`.

## Accessibility
- Maintain visible focus states.
- Do not encode state with color alone.
- Preserve existing semantic buttons, labels, tab roles, and aria attributes.
- Minimum interactive target remains approximately 44px where existing mobile controls use touch interaction.

## Implementation

The theme is implemented as an additive visual layer in `frontend/theme-graphite.css`, loaded after the existing layout CSS. This keeps current behavior and responsive structure intact while making the design system easy to remove, iterate, or compare.
