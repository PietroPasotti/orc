## Close completed vision docs

**Every time you run**, inspect every file in `.orc/vision/` (excluding
`README.md`). For each vision doc, decide whether the vision it describes
has been **fully implemented** — i.e. every capability it describes exists
in the codebase, and the tasks that delivered it appear in the `done` list
of `board.yaml` (or equivalent ADRs are in place and the code reflects them).

When a vision doc is complete:

1. Write a 2–4 sentence summary of what the vision described.
2. Collect the filenames of the `done` tasks that implemented it.
3. Append an entry to `.orc/orc-CHANGELOG.md`:

```markdown
## NNNN-short-title (closed YYYY-MM-DDTHH:MM:SSZ)

**Vision summary:** <2–4 sentence summary>

**Implemented by:**
- `.orc/work/NNNN-task-title.md`
- ...
```

4. Delete the vision document file from `.orc/vision/`.
5. Commit both changes together:

```
git add .orc/vision/NNNN-title.md .orc/orc-CHANGELOG.md
git commit -m "chore(orc): close vision NNNN-title"
```

Do this **before** creating any new tasks, so the board reflects the true
remaining work.

## Know when you are done

You are done when:
- All fully-implemented vision documents have been closed and logged in
  `.orc/orc-CHANGELOG.md`, **and**
- All remaining vision documents have been translated into tasks or ADRs, **and**
- All `#TODO` / `#FIXME` comments have been translated into tasks (or are
  already tracked on the board), **and**
- All tasks have been implemented and closed (the `open` list in `board.yaml` is empty).
