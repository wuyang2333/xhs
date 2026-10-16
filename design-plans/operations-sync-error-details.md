# Make Sync Timeout Messages Actionable

Written against: 74b95d6

## Evidence chain

- Surface: `/platforms/xhs/operations`, account card after an Android profile scan timeout
- Problem: the warning's primary description renders the full Python/ADB command, local executable paths, checkpoint path, and timeout text as one dense paragraph; the user cannot quickly tell what was saved or what to do next.
- Design evidence: the provided rendered screenshot; the local operations surface uses Ant Design `Alert` and already has separate task progress and action controls.
- Owner: `frontend/src/pages/platforms/xhs/operations-page.tsx`
- Scope and affected surfaces: account sync status `Alert`, completion/error toast text emitted by the same operations page
- Uncertainty: the exact technical-detail presentation should be validated at narrow desktop and mobile widths after implementation.

## Design decision

Separate user-facing recovery guidance from diagnostic output. The primary alert should state the outcome in plain Chinese, include the number of notes retained when available, and expose the next valid action (continue syncing or analyze existing content). Keep the raw command/error available behind a compact, optional technical-details disclosure so troubleshooting information remains available without dominating the normal workflow.

## Reuse

- Existing Ant Design `Alert`, `Typography.Text`, `Button`, and `Collapse`/`Typography.Paragraph` primitives.
- Exemplar: `frontend/src/pages/platforms/xhs/operations-page.tsx` existing status alert and `statusAction` action slot.

## Changes

1. `frontend/src/pages/platforms/xhs/operations-page.tsx`
   - Change: derive a sanitized, user-facing sync message for timeout/partial states from `summary.sync.error`, `operationTask.payload.remote_error`, `scanned_count`, and `target_count`; omit executable paths and command arguments from the primary description.
   - Change: render raw diagnostic text only in a collapsed technical-details control, with safe text wrapping and no horizontal overflow.
   - Change: keep the existing close behavior and persisted dismissal key unchanged; a new sync result must still create a new alert identity.
   - Preserve: progress display for active tasks, existing `statusAction` buttons, partial-result state, and the ability to inspect the underlying error.
   - Verify: after a timeout the alert reads as a concise outcome, reports retained notes, offers the existing next action, and reveals the original diagnostic only when expanded.
2. `frontend/src/pages/platforms/xhs/operations-page.tsx`
   - Change: replace the finished-task toast's raw failure text with the same sanitized summary used by the alert, while retaining the technical detail in the account card.
   - Preserve: success toasts and task polling behavior.
   - Verify: completion/error toast never exposes a Windows path or full command by default.

## Scope

- Inherit: all XHS account cards using the operations status alert.
- Verify: quick, full, rebuild, partial-timeout, and analysis-failure states; long Chinese account names and narrow viewport wrapping.
- Exclude: backend error payload schema, Android scan behavior, checkpoint retention, and unrelated operations-page layout changes.

## Validation

- Product: trigger or simulate a partial Android sync timeout; confirm retained-note count and next action are understandable without opening details.
- Interface: inspect `/platforms/xhs/operations` at desktop and mobile widths; expand/collapse technical details; refresh after dismissing the alert.
- System: confirm the same sanitized formatter is used by the alert and completion toast, with no second error-display pattern.
- Repository: `npm run build` → succeeds with no TypeScript errors.

## Stop conditions

- Stop if the backend no longer supplies a stable error string or retained-note count; keep the primary message generic and do not guess a cause.
- Stop if technical details require a new shared component outside this surface; keep the change local rather than widening scope.

## Design documentation

- After acceptance and validation: none required; this is a local presentation change using existing primitives.
