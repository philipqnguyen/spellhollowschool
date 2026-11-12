# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue, pull
request or discussion.

Use GitHub's private vulnerability reporting: on the repository page open
**Security**, then **Report a vulnerability**
(<https://github.com/philipqnguyen/spellhollowschool/security/advisories/new>).
Only the maintainers see the report.

Include what you can of:

- what an attacker can do, and what they need first (an account, a
  particular setting, network access to the game port);
- the steps or a small script that shows it, against a local server
  (`bin/dev`) rather than someone else's;
- the commit you tested and the settings that matter (for example
  `SHS_TRUST_PROXY`, `SHS_CLIENT_IP_HEADER`, whether email is configured).

Once a fix is on `main`, the advisory is published with credit to you,
unless you would rather stay anonymous.

## What is covered

Only the latest `main` is supported: there are no release branches, and
fixes are not backported.

In scope: the game server (`crates/`), the client (`client/`), the shipped
deploy configuration (`config/deploy.yml`, `.kamal/`, `docker/`) and the
scripts in this repository. Examples:

- signing in as someone else, or keeping access after a logout or a
  password reset;
- reading or changing another player's data, items, Crowns or messages;
- cheating that the server should refuse (it is authoritative over
  movement, duels and the economy);
- getting around a rate limit, or a cheap request that costs the server a
  lot;
- injecting script or markup through names, chat or mail.

Not covered:

- servers run by other people: report those to whoever runs them;
- anything that needs `SHS_DEV=1`, which is for local development only and
  hands out account access by design
  ([docs/configuration.md](docs/configuration.md#shs_dev-development-mode));
- the placeholder chat word list (operators supply their own);
- denial of service by sheer volume of traffic;
- problems in third-party dependencies with no effect on this project
  (report those upstream).

## Running your own server

[docs/deploy.md, "Before you open a server to the public"](docs/deploy.md#before-you-open-a-server-to-the-public)
lists what to check before a server goes on the internet: `SHS_DEV` off,
your own word list, email, a private game port behind the proxy settings,
and backups.
