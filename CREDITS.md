# Credits & Third-Party Attributions

## License

Suijin is licensed under the **GNU Affero General Public License v3.0 or later
(AGPL-3.0-or-later)**. The full text is in [LICENSE](LICENSE).

## Inspirations

Suijin's design was inspired by, but does not incorporate code from, these
projects:

- [RedAmon](https://github.com/samugit83/redamon)
- [Sakana Fugu](https://github.com/sakana-ai/Fugu) — MIT-licensed

## Historical provenance

The `tui/` terminal shell introduced in v2.3.0-beta was a fork of
[opencode](https://github.com/sst/opencode) v1.18.18 (MIT-licensed). That code
has since been removed from the tree; only stylistic references remain. It is
recorded here for transparency.

## Runtime dependencies

Suijin's Python dependencies (langgraph, rich, textual, pydantic, and others)
are installed with `pip` and are not vendored into this repository. Each retains
its own upstream license.
