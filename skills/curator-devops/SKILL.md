---
name: curator-devops
description: "Operate the spotify-curator deployment: health checks, logs, restarts, CI status, and filing issues or small PRs for failures. Use when asked whether the curator is healthy, why a run failed, or to keep the deployment running."
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [devops, docker, ci, github, ops]
    related_skills: [spotify-curator]
---

# Curator DevOps

You keep spotify-curator running. You observe, restart, and report. You do not ship code:
changes go through a pull request that a human (with Claude as reviewer) approves.

Run commands from the `deploy/` directory of the checkout. Services: `hermes-spotify-curator`
(Hermes + daemon) and `mcp` (container `spotify-curator-mcp`, port 8788 on localhost).

## Allowed without asking

| Task | Command |
|------|---------|
| Health | `curl -fsS http://127.0.0.1:8788/health` and `docker compose ps` |
| Logs | `docker compose logs --tail 200 <service>` |
| Restart one service | `docker compose restart <service>` (at most twice per incident) |
| CI status | `gh run list --limit 10`, `gh run view <id> --log-failed` |
| Tests | `pytest -q` in a checkout or worktree |
| Report | `gh issue create --label ops --title ... --body ...` |

## Allowed via pull request only

- Fixes to `src/`, `tests/`, `deploy/`: work in a git worktree on a `fix/` or `ops/` branch,
  follow `CLAUDE.md` (test first, `ruff`, CHANGELOG entry), open a PR, and ask for review by
  commenting `@claude review` on it.
- Rebuilding the image (`docker compose build`) after such a PR is merged.

## Never

- Merge PRs, push to `main`, push tags, or edit `.github/workflows/`.
- Read, print, copy or rotate anything in `.env` or `secrets/`.
- `docker compose down -v`, deleting `data/`, or pruning images/volumes.
- Retry a failing restart in a loop. After two failed restarts, open an issue and stop.

## Incident routine

1. Check health and `ps`. If healthy, say so and stop.
2. Read the last 200 log lines of the failing service; find the first error.
3. Restart once if the cause is transient (network, rate limit, OOM). Recheck health.
4. Otherwise open an `ops` issue: symptom, first error line, what you tried, suspected cause.
   If the fix is obvious and small, open a PR for it and link the issue.

## Reporting

Keep reports short: status, cause, action taken, link. No log dumps longer than 30 lines.
