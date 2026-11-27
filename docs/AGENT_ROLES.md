# Agent roles

Bulk work runs on Ollama-backed agents; Claude reviews and steers.

| Role | Agent | Model | Does |
|------|-------|-------|------|
| Curator + DevOps | Hermes (`ollama launch hermes`) | `deepseek-v4.1-flash:cloud` | Runs playlists, watches health/CI, restarts, files `ops` issues, opens small fix PRs ([skill](../skills/curator-devops/SKILL.md)) |
| Feature dev | Claude Code on Ollama (`ollama launch claude`) | `deepseek-v4.1-flash:cloud` or `kimi-k3:cloud` | Implements FP issues in its own worktree, opens PRs |
| Review + steering | Claude (`@claude review` on a PR, or a Claude Code session) | Claude | Reviews PRs, writes issues/acceptance criteria, takes over where the Ollama agent stalls |
| Merge, release | Human | - | Merges PRs, pushes tags |

## Setup

```bash
ollama pull deepseek-v4.1-flash:cloud
ollama launch hermes --model deepseek-v4.1-flash:cloud
```

Give Hermes a fine-grained GitHub token for this repo only: Issues and Pull requests read/write,
Contents read/write, Actions read. No Administration or Workflows permission, so it cannot merge
past branch protection or change CI.

## Handing work to an Ollama agent

1. Claude (or you) writes a GitHub issue with acceptance criteria.
2. `git worktree add ../spotify-curator-<n> -b feat/<n> origin/main`
3. In that folder: `ollama launch claude --model deepseek-v4.1-flash:cloud`, prompt:
   "Implement issue #<n> following CLAUDE.md, then open a PR."
4. Comment `@claude review` on the PR. If the agent is stuck after two attempts, hand the
   branch to a Claude session.
