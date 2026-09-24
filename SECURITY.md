# Security

This is an unsupported developer source preview. Do not expose it to the public
internet or use it as a remote command server. No security audit or safe production
deployment is claimed.

Do not post credentials, OAuth tokens, login links, transcripts, raw account homes,
process environments, usage exports or customer data in issues or pull requests.

For a suspected vulnerability, use GitHub's private vulnerability-reporting control
if it is enabled for this repository. If no private reporting control is available,
open a minimal issue asking the maintainer for a private channel, without exploit
details or sensitive data. Do not send an unredacted diagnostic bundle.

Account authentication, pairing, remote access and mutation permissions must have
separate explicit boundaries. Read-only inventory must not trigger login, usage
fetches, workspace creation, process termination, transfers or recovery actions.

Live history and account records must never become CI fixtures. Security fixes need
synthetic regression coverage and a description of affected trust boundaries.
