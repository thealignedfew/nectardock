# NectarDock

An account-aware desktop switchboard for AI coding conversations.

By [The Aligned Few](https://thealignedfew.com). Maintained by
[@maxeckemoff](https://github.com/maxeckemoff).

**Developer source preview, not an installable release.** This repository contains
a sanitized snapshot of the Windows prototype. Account addresses, local paths,
project names and conversation fixtures are examples. Do not replace them with
private values and commit the result.

## What the prototype does

- Shows registered conversations and their account/workspace associations.
- Opens account-isolated VS Code workspaces with selection and saved-tab checks.
- Stages reviewed saved-history transitions with preservation and recovery evidence.
- Compares two branches of one conversation and lets the user explicitly choose
  the registered source as the destination survivor, with backups and freshness guards.
- Reconciles registration to an existing equal or extended history without copying
  over that history.
- Displays last-known account usage, explicitly distinguishing stale data.
- Compares selected settings, skills and MCP configuration before reviewed changes.

The initial provider integration is Claude Code on Windows. Provider-independent
architecture, Codex integration and generalized onboarding are future work.
The bee/apiary branding describes organizing conversations, not an affiliation
with any AI provider.

## Important limits

This is **not** a mechanism for sharing credentials, pooling subscriptions, or
bypassing provider limits. Authentication stays with each provider's own flow.
Users are responsible for using their accounts in accordance with applicable terms.

Registered location is not proof of the freshest history. A successful file or
registry operation is not proof that a model resumed the intended conversation.
Preserved old copies are not yet retired from every native history picker.
Automatic selection of a freshest copy, verified cut semantics, and protection
across every launch route are not complete.

Three hash-pinned preservation/process/map helpers and a local usage adapter are
not bundled yet. The full desktop engine and its helper-dependent tests therefore
cannot run from a clean clone. Do not point this snapshot at valuable histories.
There is no supported installer, executable download, marketplace extension,
mobile pairing endpoint, or remote mutation API in this repository.

## Development checks

On Windows with Python 3.11 (including Tk) and Node.js 22:

```powershell
python -B -m unittest test_account_inventory test_regularize test_usage_snapshot test_runtime_diagnostics test_switchboard_activity test_switchboard_instance test_switcher_ui_rows test_display_options test_group_consolidation test_workspace_consolidation.WorkspaceConsolidationTests test_survivor_review test_survivor_dialog -q
node --test vscode-extension/test-launcher.js
```

The 0.2.4 sanitized snapshot passes 72 Python tests and four standalone Node
tests. These use synthetic data and do not launch provider sessions. The separate
VS Code extension-host test requires an actual extension test host and is not
covered by the command above. It checks activation and command registration only,
not backend diagnostics or workspace launch success.

The 0.2.4 private operational baseline passed 158 Python tests. That broader result does
not imply the 86 helper-dependent tests can run in this repository. CI deliberately
runs only the self-contained set and reports this boundary.

## Where to start

- [Roadmap](docs/ROADMAP.md): priorities and release gates.
- [Safety lessons](docs/SAFETY.md): registration, history and runtime distinctions.
- [Contributing](CONTRIBUTING.md): review and synthetic-test requirements.
- [Security](SECURITY.md): what never belongs in an issue or commit.
- [Publication boundary](docs/PUBLICATION.md): what was excluded or sanitized.

## Licensing

A distribution license has not been selected. Public visibility does not grant
an open-source license. Please do not assume permission to redistribute until a
license is explicitly added by the project owner.
