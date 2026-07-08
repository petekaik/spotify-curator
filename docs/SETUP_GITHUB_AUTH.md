# Setting up GitHub authentication on macOS

**Status:** As of 2026-07-08, no credential store is configured yet. Your
PAT needs to go somewhere so that `git push` and `gh` commands work without
prompting for a token every time.

## Recommendation: gh CLI + macOS keychain (safest option)

`gh` stores the credential in the macOS **Keychain Access**, from which
both `gh` and `git` can read it. The token never appears in a plaintext
file — only in the keychain.

```bash
# 1. Login with gh (asks for token once)
gh auth login --with-token
#   (Paste your PAT, Ctrl-D, Enter)
#
# Or interactive:
gh auth login
#   → GitHub.com
#   → HTTPS
#   → Paste authentication token

# 2. Verify it works
gh auth status
#   Should say: "Logged in to github.com as <your-username>"

# 3. Configure git to use gh's credential helper
git config --global credential.helper '!gh auth git-credential'

# 4. Test push
cd ~/projects/spotify-curator
git push origin main
```

After this, `git push` works without prompting, because `gh auth git-credential`
returns the token from the keychain automatically.

## Option B: macOS keychain directly with git

```bash
git config --global credential.helper osxkeychain
```

The next `git push` will ask for credentials **once**, store them in the
keychain, and then every subsequent push is automatic.

**Note:** This was the original configuration that worked for the first
push, but the credential helper stopped responding in subsequent shells.
Possible causes:
- `osxkeychain` binary not in PATH
- GUI prompt not visible in headless terminal

## Option C: `~/.netrc` (old-school)

```bash
cat >> ~/.netrc << 'EOF'
machine github.com
login YOUR_GITHUB_USERNAME
password ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
EOF

chmod 600 ~/.netrc  # IMPORTANT: file readable only by you
```

`git` and `curl` will read the credential from this file automatically.

**Security risks:**
- Stored in plaintext (but chmod 600 prevents other users)
- Backup scripts can accidentally copy it
- Root user on the same machine can read it

## Option D: environment variable (temporary use only)

```bash
export GH_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
# or
export GITHUB_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

`gh` and `git` (GitHub Actions-compatible) read this automatically.

**NOT recommended for permanent use** — visible in process listings, lands
in shell history, doesn't survive reboot.

## What we chose here

For this project, we use **Option D (environment variable)** loaded from
`~/.hermes/.env` via `~/.zshrc`:

```bash
# In ~/.zshrc:
export $(grep "^GITHUB_TOKEN=" "$HOME/.hermes/.env" | xargs) 2>/dev/null
```

This works in our environment because the Hermes integration already
manages a `GITHUB_TOKEN` in `~/.hermes/.env`. The `git` CLI picks it up
automatically (no credential helper needed) and `gh` falls back to it
when no keychain credential is present.

If you start a fresh machine or a new terminal tool that doesn't read
`.zshrc`, switch to **Option A** for a permanent solution.

## PAT permissions (check on GitHub)

Your token needs:
- `repo` — read/write repository contents
- `read:org` — if you use org-scoped commands
- `workflow` — if you want to modify CI workflow files

NOT needed:
- `delete_repo` (dangerous)
- `admin:org` (admin-level access)

## How to revoke

If your token leaks:
1. **GitHub.com → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click the token → "Delete" or "Regenerate"
3. If you used Option A: `gh auth logout` to remove from keychain
4. If you used Option C: `rm ~/.netrc`

## See also

- [GitHub PAT docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
- [gh auth login](https://cli.github.com/manual/gh_auth_login)
- macOS Keychain Access.app — view/delete stored credentials
