# spotify-curator deployment

## What this is

A Dockerized **Hermes Agent** instance with the **spotify-curator** skill
preloaded, running on the host Mac (or any Docker host). Replaces the
plain CLI workflow with a 24/7 service that can be controlled via:

- **`hermes chat`** commands (inside the container)
- **Hermes API server** on port 8642 (HTTP)
- **Messaging platforms** (Telegram/Discord/...) via the gateway
- **One-shot `docker compose run`** for ad-hoc operations

The container is **portable**: same image, same env_file, same volumes
work on macOS, Linux, QNAP, or any Docker host. Move from Mac → QNAP by
just copying the `deploy/` directory.

## Architecture

```
Mac host                                       Container
────────                                       ────────
deploy/
├── .env          ─── env_file ───▶          all env vars injected
├── data/         ◀── bind ───▶              /opt/data (HERMES_HOME)
│                                                ├── config.yaml
│                                                ├── sessions/
│                                                ├── memories/
│                                                ├── logs/
│                                                └── skills/  (skill cache)
├── secrets/      ◀── bind:ro ──▶            /opt/secrets/
│   └── spotify_cache  (OAuth token)              └── spotify_cache
└── scripts/      ◀── bind ───▶              /opt/project/scripts
                                                (user scripts callable by agent)

Project root (../) bind-mounted to /opt/project/
  ├── src/       (live-edit during dev)
  ├── skills/    (loaded by Hermes)
  └── docs/
```

## Key design decisions

1. **Hermes profile = `spotify-curator`** — isolated config, memories,
   sessions, skills from the host's `default` profile. Won't pollute
   your main Hermes install.
2. **Headless OAuth via host shim** — Spotify OAuth requires browser.
   We do OAuth on the Mac (`scripts/auth_on_host.sh`), write the
   token cache to `secrets/`, and mount it read-only into the container.
   Container never opens a browser.
3. **Bind mounts over COPY** — Dockerfile COPYs the code for first-build,
   but bind mounts override at runtime. Live-edit Python on host, no
   rebuild needed.
4. **No isäntäkohtaisia polkuja** — everything goes through
   `$HERMES_HOME` and `SPOTIFY_CURATOR_HOME`. Same compose works
   on QNAP, Linux server, etc.
5. **`SPOTIPY_OPEN_BROWSER=false`** — defensive: even if a code path
   tries to open a browser, it silently no-ops.

## Quick start

```bash
# 1. Configure env
cd ~/projects/spotify-curator/deploy
cp .env.example .env
nano .env  # Fill in SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, LASTFM_API_KEY, OPENROUTER_API_KEY

# 2. Run OAuth flow on host (one-time)
./scripts/auth_on_host.sh
# Browser opens, you approve, token written to secrets/spotify_cache

# 3. Build and start
docker compose build
docker compose up -d
docker compose logs -f hermes-spotify-curator
```

## Talking to the container

```bash
# Hermes chat (one-shot)
docker compose exec hermes-spotify-curator hermes chat -q "build a mood-based playlist for 'calming'"

# Interactive Hermes session (PTY)
docker compose exec -it hermes-spotify-curator hermes

# Run a spotify-curator CLI command directly
docker compose exec hermes-spotify-curator python -m src.cli.main status

# Force rebuild
docker compose build --no-cache
docker compose up -d

# Stop and remove
docker compose down

# Wipe data (destructive!)
rm -rf data/
```

## Migrating to QNAP (later)

When QNAP is reachable again:

```bash
# 1. Copy deploy/ to QNAP
scp -r deploy/ qnap:/share/Programs/spotify-curator/

# 2. SSH in and authenticate
ssh qnap
cd /share/Programs/spotify-curator/deploy
./scripts/auth_on_host.sh   # Mac-side auth, then copy cache

# 3. Start
docker compose up -d
```

The compose file works as-is on QNAP Container Station because:
- No host-specific paths in compose
- All volumes use relative paths (`./data`, `./secrets`)
- env_file is portable
- `restart: unless-stopped` survives reboots

## Security notes

- `.env` is gitignored. Never commit.
- `secrets/spotify_cache` is gitignored. Never commit.
- Ports default to `127.0.0.1:` (localhost only). Remove if you need LAN access.
- No `--privileged` flag — no special capabilities needed.
- Container runs as root by default (matches `hermes-agent` install
  expectations). For tighter security, add `user: "1000:1000"` and
  chown `data/` accordingly.
- Resource limits set: 2GB memory, 1 CPU. Adjust for your host.

## Troubleshooting

**Container exits immediately:**
```bash
docker compose logs hermes-spotify-curator
# Most common: missing API key in .env
```

**OAuth fails inside container:**
The container MUST NOT run OAuth. Run `./scripts/auth_on_host.sh` on Mac.

**Token expired:**
Re-run `./scripts/auth_on_host.sh`. The container reads the
`spotify_cache` file at startup; for long-lived containers you may
want a cron job that refreshes the token.

**"No profile found" errors:**
Hermes profile is created on first startup. If you change
`HERMES_PROFILE` in `.env`, the new profile starts fresh.

**Port 8642 in use:**
Edit `ports:` in compose.yml. Update `API_SERVER_PORT` env var too.
