# Agent roles

Bulk work runs on Ollama-backed agents; Claude reviews and steers.

| Role | Agent | Model | Does |
|------|-------|-------|------|
| Curator + DevOps | Hermes (`ollama launch hermes`) | `deepseek-v4.1-flash:1017-cloud` | Runs playlists, watches health/CI, restarts, files `ops` issues, opens small fix PRs ([skill](../skills/curator-devops/SKILL.md)) |
| Feature dev | Claude Code on Ollama (`ollama launch claude`) | `deepseek-v4.1-flash:1017-cloud` or `kimi-k3:cloud` | Implements FP issues in its own worktree, opens PRs |
| Review + steering | Claude (`@claude review` on a PR, or a Claude Code session) | Claude | Reviews PRs, writes issues/acceptance criteria, takes over where the Ollama agent stalls |
| Merge, release | Human | - | Merges PRs, pushes tags |

## Setup

Hermes in the container (`deploy/`) uses Ollama on the host. `HERMES_MODEL` in `.env` is not
read; set the model in Hermes's config (saved in `deploy/data/config.yaml`):

```bash
ollama pull deepseek-v4.1-flash:1017-cloud
docker exec hermes-spotify-curator sh -c 'hermes config set model.provider custom &&
  hermes config set model.base_url http://host.docker.internal:11434/v1 &&
  hermes config set model.api_key ollama &&
  hermes config set model.default deepseek-v4.1-flash:1017-cloud'
docker compose restart hermes-spotify-curator
```

The Hermes API server (port 8642, used by the health check) needs `API_SERVER_ENABLED=true`,
`API_SERVER_HOST=0.0.0.0` and an `API_SERVER_KEY` in `deploy/.env`.

Give Hermes a fine-grained GitHub token for this repo only: Issues and Pull requests read/write,
Contents read/write, Actions read. No Administration or Workflows permission, so it cannot merge
past branch protection or change CI.

## Handing work to an Ollama agent

1. Claude (or you) writes a GitHub issue with acceptance criteria.
2. `git worktree add ../spotify-curator-<n> -b feat/<n> origin/main`
3. In that folder: `ollama launch claude --model deepseek-v4.1-flash:1017-cloud`, prompt:
   "Implement issue #<n> following CLAUDE.md, then open a PR."
4. Comment `@claude review` on the PR. If the agent is stuck after two attempts, hand the
   branch to a Claude session.
