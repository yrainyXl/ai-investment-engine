# DSH deployment task: AI Investment Engine v0.3.0 / M3 Provider v1

Target repository: `yrainyXl/ai-investment-engine`
Target directory: `/opt/apps/ai-investment-engine`
Target branch: `deploy/v0.2`
Target image: `ai-investment-engine:0.3`
Target API version: `0.3.0`
Target SHA: **ChatGPT must supply the exact merged SHA after PR #6 is merged. Do not infer it from this document.**

## Allowed actions

- Read current branch/SHA/worktree and current container health.
- `git fetch origin`; fast-forward `deploy/v0.2` to the exact target SHA only if it is a clean fast-forward and worktree is clean.
- Rebuild and restart only the `engine` service in this Compose project.
- Keep all existing `.env` values unchanged.
- Write new provider data only under the existing persistent `/data/evidence/` directory by running the two explicit CLI commands below.
- Read local metadata for validation.

## Forbidden actions

- Do not print, read out, modify or rotate .env secrets or tokens.
- Do not touch OTW documents, runs, orders, portfolios, accounts, strategy files or permissions; do not call any OTW write tool.
- Do not create, edit or enable schedules.
- Do not create, cancel or submit any real or paper orders.
- Do not touch other containers, services, firewall, proxy or host security configuration.
- Do not run `docker compose down -v`, `git reset --hard`, force push, cleanup of the worktree or data directory.
- Do not change code or provider logic on the server. If a provider or source fails, return the exact error class and sanitized summary to ChatGPT.

## Preconditions and rollout

1. Record current git branch, full SHA, worktree state, engine image/container status, /healthz and local-only bind. Do not output environment variables.
2. If the worktree is dirty, stop. If branch is not `deploy/v0.2`, report it and stop.
3. `git fetch origin`; verify the exact ChatGPT-supplied SHA is an ancestor of `origin/main`.
4. Use `git merge --ff-only <EXACT_TARGET_SHA>` from `deploy/v0.2`. Do not reset or rebase.
5. Run `docker compose config --quiet`, then `docker compose up -d --build engine`. This rebuilds only the project engine container. Preserve the existing `./data:/data` volume.
6. Confirm the image is `ai-investment-engine:0.3`, container healthy, `/healthz` returns HTTP 200 with `version=0.3.0`, and port remains bound only to `127.0.0.1:8018`. Confirm `read_only`, `cap_drop: ALL`, and `no-new-privileges` remain enabled.

## Public data sync

Run the following once each. These write only local metadata files in `/data/evidence`:

```bash
docker compose exec -T engine python -m investment_engine.market_cli sync-disclosures \
  --symbol 601118 --start-date 2020-01-01 --end-date "$(date +%F)" --max-pages 5

docker compose exec -T engine python -m investment_engine.market_cli sync-industry
```

Do not retry a failed provider call without ChatGPT review. A source failure must not create synthetic rows.

## Validation and report

Read local `/data/evidence/source_documents.json` and `/data/evidence/industry_observations.json` metadata only (do not dump full files).

Report:
- actual Git SHA, branch, image tag, health and local bind;
- CNINFO rows saved, first/last published_at, source name, whether source URLs are HTTPS, and confirm `content_status=metadata_only_pdf_not_parsed`;
- inventory rows saved, first/last effective date, series_id and source; confirm its statistical scope is **unknown**;
- explicitly confirm unit is `source_native_unverified`, `published_at=null`, `unit_verified=false`, so this series is not eligible for thesis assessment, and it is not verified as exchange delivery or social inventory;
- original 601118/RU0 market CSV counts and report files remain present;
- confirm OTW state was not read/written by this task, no orders and no schedules were touched.

Do not output any secret, account/holding details, complete report contents or raw announcement PDFs.

## Failure handling

If build, health, source sync or validation fails, stop at the first failing step for that provider, retain old /data, and report the sanitized error. Do not downgrade, reset or edit code. ChatGPT will prepare a new PR if code changes are required.
