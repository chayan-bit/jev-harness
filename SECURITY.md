# Security policy

## Supported versions

| Version | Supported |
|---|---|
| Latest release (currently 0.1.x) | Yes |
| `main` branch | Yes |
| Older releases | No; please upgrade |

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's [private vulnerability reporting](https://github.com/chayan-bit/jev-harness/security/advisories/new).
Do not open a public issue, discussion, or pull request for a suspected vulnerability.

Include a description of the issue, the affected version or commit, the impact you expect, and steps or a minimal script to reproduce it.
You should receive an acknowledgement within 3 business days and an initial assessment within 10 business days.
Fixes are developed in a private advisory, released as a patch version, and disclosed publicly with credit to the reporter unless you prefer to stay anonymous.
Vulnerabilities in the underlying framework belong to [Jev-Frame](https://github.com/chayan-bit/Jev-Frame/security/advisories/new).

## Authority boundary

Advice, hook context and shadow judgments carry no execution or permission authority.
A report that shows Jev advice can grant, widen or bypass a host permission is a security issue.
So is any hook output that allows or approves an action, advice that survives changed evidence or an expired report, or a path that sends evidence containing private-key or API-token patterns to a provider.

## Handling credentials

Never paste TypeSafe API keys or any other credentials into issues, pull requests, advisories, logs, fixtures, captures, or recordings.
If a key is exposed, revoke it in your TypeSafe account immediately and redact the text where it appeared.
jev-harness uses a key only from the argument or environment variable you provide and never writes key material into reports, records, ledgers or captures.
Evidence and captures that contain common private-key or API-token patterns are refused before they are sent or stored.
