# Squad profiles

Drop `.yaml` files here to define or override squad configurations for this
project.  Project-level profiles take precedence over the package defaults.

## Schema

```yaml
name: broad
description: |
  Wider parallel configuration for larger projects.
composition:
  planner: 1   # must always be 1
  coder: 4     # parallel coders
  qa: 2        # parallel QA reviewers
timeout_minutes: 180
```

Run `orc squads` to list all available profiles.
