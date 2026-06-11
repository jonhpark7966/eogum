# Frontend Deployment

This project deploys the web frontend through Vercel from git. The production site is:

- Web: `https://eogum.sudoremove.com`
- API used by production web: `https://api-eogum.sudoremove.com/api/v1`

## Production Path

The frontend source lives in `apps/web`.

Production deploy flow:

1. Make the frontend change.
2. Verify locally from `apps/web`.
3. Commit only the intended frontend/docs files.
4. Push `main` to `origin`.
5. Vercel deploys the pushed commit to production.

Do not use the Vercel CLI from Codex sessions unless explicitly needed. This machine may not have Vercel credentials or a local `.vercel` project link, so `npx vercel` can block on interactive login. Git push is the normal deploy path.

## Commands

Run verification from the web app directory:

```bash
cd /home/jonhpark/workspace/eogum/apps/web
npm run lint
npm run build
```

Then commit from the repository root:

```bash
cd /home/jonhpark/workspace/eogum
git status -sb
git add apps/web/src/app/dashboard/page.tsx apps/web/src/lib/api.ts docs/frontend-deployment.md
git commit -m "Optimize dashboard project detail loading"
git push origin main
```

Adjust the `git add` paths to match the actual frontend files changed in the session.

## Dirty Worktree Rule

This repository is often used with unrelated backend, docs, and runtime changes already present. Do not run broad staging commands such as:

```bash
git add .
git commit -am ...
```

Always stage exact paths. Before committing, inspect:

```bash
git diff --cached
```

The frontend deploy commit should not include unrelated API, Supabase, runtime, or `auto-video-edit` changes unless the user explicitly asks for them.

## After Push

After `git push origin main`, check the pushed commit hash:

```bash
git rev-parse HEAD
```

Then verify the production site in a browser or with targeted requests. For this app, a useful API-side smoke check is:

```bash
curl -i https://api-eogum.sudoremove.com/api/v1/health \
  -H 'Origin: https://eogum.sudoremove.com'
```

Frontend behavior should be checked from `https://eogum.sudoremove.com` after Vercel finishes deploying.
