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
