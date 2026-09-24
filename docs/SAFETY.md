# Safety lessons carried into the code

## Registration is not freshness

A transfer can update the register while preserving the source copy. If someone
later resumes that source copy, the register can still identify the destination
even though the source now contains additional history. File modification time,
window color and a successful launch request do not resolve that disagreement.

Keep these facts separate: registered account, observed disk history, saved UI
state, live writer identity, and verified runtime adoption.

## Repair the right layer

Do not use a copy operation to repair an outdated registration. The reconciliation
command adopts an existing target only when its main history equals or byte-extends
the registered source and source companions are present and hash-identical.
Different histories require an explicit, separately reviewed survivor/merge decision.

## A later timestamp is not a complete history

Two branches can share a prefix and then diverge. A shorter branch can have a later
assistant output time without containing the longer branch's work. The survivor
dialog reports exact record comparison, recorded times and bounded text previews
separately. It does not score or automatically pick a winner.

An explicit source-survivor choice replaces the destination main history rather
than merging branches. Both originals are preserved, all compared companion hashes
are fenced, and a change after review requires a new review. A successful saved-file
operation does not prove native runtime adoption or retire the old source copy.

## Batch review is not automatic survivor selection

The scan lists every selected row before preservation begins. Each distinct branch
requires an explicit registered-source choice or exclusion. Bulk choices preserve
prior exclusions. Already-destination conversations are no-ops. Known live writers
are individually held; unresolved process identity holds pending rows.

The prepared batch is bound to the exact scanned register, membership and content.
Apply rechecks all included rows before writing. A partial failure retains checkpoints,
journal and a recovery marker; it is not an atomic rollback or a completed batch.
Identical snapshots can be reused only within one preparation after hash validation.
KEEP actions skip mutation journaling, not freshness checks. Shared memory stays unchanged.

## Unknown is not empty

Directory traversal errors must stop completeness-dependent operations. A walk
that silently skips an unreadable subtree can produce a false absence result.
Reconciliation uses strict traversal during preparation and apply validation.

## Evidence precedes mutation

Prepare with exact UUIDs, account identity, stable source and destination evidence,
and no known live writers. Preserve checkpoints and routing backups. Revalidate
after review and before publication. Read back what was written.

Partial publication is not success. Retain the pending marker and recovery evidence
instead of automatically rolling back over potentially newer work. Existing known
reference gaps stay explicit; prior exceptions cannot excuse new gaps.

## Remaining work

Workspace grouping is not history relocation. Multi-root groups keep each session's
native project and storage path. Consolidation preserves settings and exact recovery
bytes, checks the initial read fingerprint again before publication, and refuses to
retire a source workspace with saved conversation tabs. Partial publication holds
for review rather than automatically rolling back over possible newer writes.

General cross-account drift checks on every launch path, native-picker retirement,
crash-consistent multi-file transactions and clean-machine packaging are not proven
by this preview. An unenforced old-copy flag is not a verified cut.
