# Changelog

## 0.2.5 source preview

- Added a batch transition table that collects all selected conflicts before backups,
  with explicit per-account survivor choices, exclusions, one combined review and Apply.
  Bulk choices preserve exclusions. No winner or workspace launch is automatic.
- Skip already-destination conversations, reuse verified identical snapshots within
  one preparation, and omit mutation-journal flushes for unchanged KEEP operations.
  Content, membership, runtime, authentication and recovery checks remain enforced.
- Known live writers are held per row. Unknown identity holds pending rows. Large
  unchanged registered checkpoints retain strict, bounded attachment validation.
- Shared project memory is unchanged. Source copies remain preserved, not retired.
  Interrupted batches keep checkpoints and a recovery marker, not a success receipt.
- Added synthetic engine and GUI regressions for scope, stale evidence, interruptions,
  bulk exclusions and mid-Apply changes. The external helper portability limit remains.

## 0.2.4 source preview

- Added a single-conversation branch comparison dialog, also offered from a held
  main-history conflict. No winner is preselected.
- Displays account identity, recorded local user-text/output times, common-prefix
  and branch counts, bounded text previews, history and companion hashes.
  Thinking blocks and tool payloads are excluded from previews.
- An explicit registered-source survivor replaces the destination only after
  preparation, original backups and reviewed confirmation. Keeping the destination
  cancels the transfer; it does not adopt a different registered home.
- History, companion and register changes invalidate stale choices. Authentication,
  live-writer and recovery guards remain enforced. Apply does not open a workspace.
- Source copies remain preserved. This is replacement, not merge or verified cut.
- Verified 72 self-contained Python tests and four launcher tests. The private
  baseline passed 158 Python tests; helper-dependent execution is still not portable.

## 0.2.3 source preview

- A workspace group can contain multiple native project roots. Catalog, selection,
  folder validation and discovery agree on membership without rewriting each
  conversation's project, native UUID or account.
- Added guarded workspace-only consolidation with exact metadata backups and
  history hash verification. Unexpected roots or saved source-workspace tabs hold.
- Partial publication retains the recovery marker. Concurrent configuration edits
  are checked against the exact initially parsed bytes, including unchanged targets.
- The example folds the Compliance project into the Analytics launch group.
  Existing native Recent entries are not rewritten in a running VS Code profile.
- Verified 59 self-contained Python tests and 4 launcher tests. The broader private
  environment passed 141 Python tests; that is not clean-machine or GUI certification.

## 0.2.2 source preview

First sanitized source checkpoint. This is not the first operational build and
does not import the private operational repository's commit history.

- Includes metadata-only registration reconciliation with reviewed source/target
  checks, preserved checkpoints, registry backups and pending recovery evidence.
- Companion enumeration fails closed on inaccessible directories.
- Includes account-colored usage, explicit stale readings, display preferences,
  local timestamps, sorting, refresh controls and account-aware workspace launching.
- Includes synthetic regression tests and a Windows CI job for the self-contained
  subset. No clean-machine desktop installation or runtime adoption is claimed.

Operational account details, logs, histories, usage caches, transaction records
and machine-specific incident evidence are excluded. See docs/PUBLICATION.md.
