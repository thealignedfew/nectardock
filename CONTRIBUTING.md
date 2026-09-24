# Contributing

NectarDock is an early Windows prototype. Start with a small issue describing an
observable problem or bounded proposal. Do not attach real conversations or account
configuration. Use synthetic identities, paths, UUIDs and history records.

## Change discipline

1. Use a focused branch and pull request. Keep public source and private operational
   data separate.
2. Add a regression that fails for the actual missing behavior before implementing
   a safety fix. Test real file operations in temporary directories, not real homes.
3. Preserve identity checks, writer checks, reviewed manifests, backups and recovery
   markers. Do not add force/bypass switches as a substitute for reconciliation.
4. Run the self-contained checks documented in README.md. State which integrations
   were not tested. A green subset is not proof that the full engine works.
5. Obtain review before applying changes to valuable histories. Keep backup and
   recovery evidence outside the source repository.

## Maintainer boundaries

Provider adapters own authentication and provider-specific formats. The core should
eventually own typed inventory, evidence, reviewed transactions and recovery. The
UI and a future mobile companion must not infer authority from display labels or
call arbitrary commands supplied over a network.

Do not add credentials, live account emails, personal paths, raw process environment,
transcripts, usage snapshots, customer data, operational UUIDs or private incident
reports. Generated examples must be visibly synthetic.

Licensing is pending owner selection. Do not submit third-party code without clear
provenance, and do not assume this preview already has an open-source license.
