# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue: open this repository's
**Security** tab and choose **Report a vulnerability**.

Include what you found, how to reproduce it and what it could affect. This is a personal project,
so replies are best effort.

## Scope

Faultline runs from source on your own machine, and it comes as is, with no warranty (see
[LICENSE](LICENSE)). Reports are welcome on the code in this repository, its dependencies as pinned
here, and its GitHub workflows.

The services are open by default for local use. Before exposing them to a network you don't trust, set
`PRICER_API_KEY` and `QLP_WRITE_TOKEN`: see the
[deployment guide](docs/guides/deployment_guide.md#4-api-authentication).

API keys belong in `.env`, which is gitignored. Never put a key in an issue or a pull request.
