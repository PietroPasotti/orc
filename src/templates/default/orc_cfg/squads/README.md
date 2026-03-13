# Squad profiles

Drop `.yaml` files here to define or override squad configurations for this
project, or edit the default squad profile.  

Project-defined profiles take precedence over orc defaults.

## Schema

```yaml
name: broad
description: |
  Wider parallel configuration for larger projects.
composition:
  - role: planner
    count: 1                  # must always be 1
    model: claude-sonnet-4.6
  - role: coder
    count: 4                  # parallel coders
    model: claude-sonnet-4.6
  - role: qa
    count: 2                  # parallel QA reviewers
    model: claude-sonnet-4.6
timeout_minutes: 180
```

Run `orc squads` to list all available profiles.
