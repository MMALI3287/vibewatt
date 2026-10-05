# Security policy

vibewatt reads local agent logs and, for plan utilization, the Claude Code OAuth
token on your machine. Bugs that leak that data matter to us.

## Supported versions

Only the latest release on PyPI gets fixes.

## Reporting a vulnerability

Report it privately through
[GitHub's private vulnerability reporting](https://github.com/MMALI3287/vibewatt/security/advisories/new).
Please do not open a public issue.

Include the version (`vibewatt --version` or `pip show vibewatt`), your OS and the
steps to reproduce. Leave out real tokens, credentials and log contents. A
redacted example is enough.

You should get a first reply within 7 days. Once a fix is released, the advisory
is published with credit to you unless you prefer otherwise.

## In scope

- Anything that sends local data off the machine beyond the calls listed in the
  README's network table
- The dashboard answering a non-loopback host or a cross-site request
- Prompt text, credentials or tokens reaching the SQLite store, an export or a log
- Path traversal or unbounded reads in a log parser
