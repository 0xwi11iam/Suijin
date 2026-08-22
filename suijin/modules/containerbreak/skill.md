# containerbreak

Analyzes real Docker/container configs for escape vectors — never
guesses. Two modes:

- docker_analyze: probes an exposed Docker API (version, containers
  list, container inspect) and reports: privileged containers, added
  capabilities, host mounts, socket exposure — with concrete escape
  commands for each finding
- escape_check: self-analysis of the current container's cgroup,
  capabilities, and mounts

Every finding includes a runnable escape command.
