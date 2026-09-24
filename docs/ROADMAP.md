# Development priorities

1. **Prevent stale-history launches.** Compare supported account copies by content
   lineage and expose unknown/divergent states. Align standalone open and apply-and-open
   checks. Clearly label registered location rather than claiming current authority.
2. **Make the engine self-contained.** Extract the private helpers behind tested
   interfaces, remove hardcoded account/project bindings, and use private user config
   with synthetic examples. Preserve every existing safety check.
3. **Implement verified moves.** Preserve a recoverable backup, publish and verify the
   destination, then retire the previous native-discoverable copy only through a
   separately reviewed transaction. Test interruptions and restoration.
4. **Ship onboarding and packaging.** Provider-native login for each account, explicit
   identity verification, selectable colors including BLUE, a real Windows application
   identity, and clean-machine installation tests.
5. **Add read-only mobile diagnostics.** Pairing, minimized projections, explicit stale
   state and authenticated transport precede any remote transition proposal. No phone
   mutation API is part of this source preview.
6. **Evaluate Codex through a provider adapter.** Do not assume Claude history formats,
   account limits, authentication or runtime adoption semantics apply to another provider.

## Release gates

- Synthetic tests, independent review and appropriate integration tests.
- No private data in source, fixtures, documentation, screenshots or commit history.
- Documented rollback/recovery limits and no false runtime-adoption claims.
- Reviewed license choice, self-contained dependencies and a clean-machine build
  before describing the project as an installable open-source release.
