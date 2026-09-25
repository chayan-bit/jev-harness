# Security policy

## Reporting a vulnerability

Please report security vulnerabilities privately through [GitHub security advisories](https://github.com/chayan-bit/jev-harness/security/advisories/new).
Do not open a public issue for a suspected vulnerability.

Include a description of the issue, the affected version or commit, and steps to reproduce it.
You should receive an acknowledgement within a few days, and fixes are coordinated with the reporter before public disclosure.

## Handling credentials

Never paste TypeSafe API keys or any other credentials into issues, pull requests, advisories, logs, or fixtures.
If a key is exposed, revoke it in your TypeSafe account immediately and redact the text where it appeared.
jev-harness uses a key only from the argument or environment variable you provide and never writes key material into reports, records, ledgers or captures.
Evidence and captures that contain common private-key or API-token patterns are refused before they are sent or stored.

## Authority boundary

Advice, hook context and shadow judgments carry no execution or permission authority.
A report that suggests Jev advice can grant, widen or bypass a host permission is a security issue.

## Supported versions

Security fixes are made on the latest release and the `main` branch.
