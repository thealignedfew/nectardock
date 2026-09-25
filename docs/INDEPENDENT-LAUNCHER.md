# Independent desktop lifetime

## Why this exists

A Windows process can inherit a kill-on-close job from an AI desktop host.
`CREATE_NEW_PROCESS_GROUP`, `start`, and a hidden window do not establish an
independent lifetime. In the observed nested arrangement, requesting breakaway
escaped one job but left an outer kill-on-close job.

NectarDock 0.2.6 instead submits allowlisted requests to a fixed Windows Task
Scheduler action running as the same interactive user, without elevation.
There are no automatic triggers, arbitrary command payloads, credentials in
requests, history transfers, model prompts, or process termination actions.

The worker must have the actual Schedule service as its direct parent and zero
job limit flags. Task Scheduler's own job membership is expected. A process-local
certification flag is set only after this check, never from an environment value.
NectarDock's Tk UI runs in that worker. Account launch paths rebuild the existing
account environment and retain all existing account/history/saved-tab checks.

## Install and use

Run `install_desktop_launcher.ps1 -PythonExe <absolute-path-to-pythonw.exe>`
from the reviewed local installation. It creates only `NectarDock Desktop Launcher`,
Limited/InteractiveToken, manual demand, parallel instances, no execution timeout.
Run `python -B desktop_launcher.py probe` to inspect the worker boundary.
`pythonw desktop_launcher.py ui` starts the app through that boundary.

The queue is `Desktop-Launch-Requests` beside the installation. This is intentionally
not LocalAppData: a packaged host and the scheduled worker saw different filesystem
views of the initial apparent LocalAppData request path. The broker installer
restricts queue access and rejects unexpected explicit grants. Operational requests,
receipts, error logs and Code process certificates must remain private and ignored.

On an uncertain submission, inspect the named request folder before trying again.
An exclusive claim prevents one request running twice. A new click creates a new
request, so no automatic retry is performed. Requests expire after two minutes.

## Existing windows

A new Code invocation can forward to an existing main process for the same profile.
That is not a lifetime migration. An existing job-bound main process is allowed
only with a certified launch receipt matching PID, creation time and profile.
Otherwise NectarDock holds and identifies the PID. Save work and close only that
account profile yourself before reopening. Other accounts are not closed.

## Acceptance evidence and limits

- Synthetic tests cover input allowlists, UUID traversal, unsafe worker boundaries,
  duplicate claims, expiry, account routing, startup/reload ordering, timed-out
  dispatch reconciliation and existing-process receipt identity.
- Live diagnostic worker: direct parent was the Windows Schedule service; job
  flags were zero, distinct from the caller's nested kill-on-close jobs.
- A disposable scheduled worker launched a disposable child and exited. The child
  wrote a completion receipt three seconds later, and the task returned to Ready.
- No working VS Code windows, histories or ChatGPT process were restarted for testing.
  Actual ChatGPT-restart survival of production apps is still an acceptance check,
  not a completed claim. Existing windows do not acquire new ownership retroactively.

## Reload Window is different

VS Code exposes `workbench.action.reloadWindow` through its extension API. A future
optional post-open reload must be delivered inside the exact destination extension
host after activation, with a one-use request and loop prevention. Global keystrokes
or a reload in the originating window are not safe substitutes. Reload can restart
Claude extensions and interrupt work; it must not be automatic on every open.
This release does not claim to implement that option.
