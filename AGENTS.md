# AGENTS.md

## Frontend Deployment

The web frontend is deployed by pushing `main` to `origin`. Vercel is connected to the GitHub repository and automatically deploys the pushed commit to production.

Production endpoints:

- Web: `https://eogum.sudoremove.com`
- API used by the production web app: `https://api-eogum.sudoremove.com/api/v1`

Use git push as the default frontend deploy path. Do not use the Vercel CLI from Codex sessions unless the user explicitly asks for it; this machine may not have Vercel credentials or a local `.vercel` project link, so `npx vercel` can block on interactive login.

### Frontend Verification

Run these from `apps/web` before deploying frontend changes:

```bash
npm run lint
npm run build
```

Known non-blocking warnings may exist in unrelated files. Build must pass before pushing.

### Frontend Deploy Steps

From the repository root:

```bash
git status -sb
git add <exact frontend/doc paths>
git diff --cached
git commit -m "<concise deploy commit message>"
git push origin main
```

After pushing, verify the Vercel commit status:

```bash
gh api repos/jonhpark7966/eogum/commits/$(git rev-parse HEAD)/status
```

Wait until the `Vercel` status is `success` and the description says deployment completed.

### Dirty Worktree Rules

This repository often has unrelated backend, docs, Supabase, and runtime changes in the worktree. Do not use broad staging commands for frontend deploys:

```bash
git add .
git commit -am ...
```

Always stage exact files only. Before committing, inspect `git diff --cached` and make sure the deploy commit excludes unrelated API, Supabase, runtime, and `auto-video-edit` changes unless the user explicitly asks to include them.

### Production Smoke Checks

After Vercel reports success, check the production web origin:

```bash
curl -I https://eogum.sudoremove.com --max-time 20
```

For API CORS health:

```bash
curl -i https://api-eogum.sudoremove.com/api/v1/health \
  -H 'Origin: https://eogum.sudoremove.com' \
  --max-time 20
```
