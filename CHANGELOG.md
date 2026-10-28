# Changelog

## 0.7.1
- Matching now splits identifiers (`TimeoutError` -> timeout + error, `api_client` -> api + client), so decisions are
  linked automatically to the errors and dead ends they respond to.
- Added a 1-minute demo video and its generator (`demo/make_demo.py`).
- MCP server reports the package version.

## 0.7.0
- Deterministic inference (event order, execution ids), evidence and confidence on every inferred memory,
  confirm / dismiss, mission card, clickable reasoning walk in the graph, MIT license.
