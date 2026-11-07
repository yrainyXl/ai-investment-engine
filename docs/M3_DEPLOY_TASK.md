# DSH deployment task: AI Investment Engine v0.4.0 / M3 Provider v1

Target repository: `yrainyXl/ai-investment-engine`
Target directory: `/opt/apps/ai-investment-engine`
Target branch: `deploy/v0.2`
Target image: `ai-investment-engine:0.4`
Target API version: `0.4.0`
Target SHA: **ChatGPT must supply the exact merged SHA after the M3 v0.4 PR is merged. Do not infer it from this document.**

## Allowed actions

- Read current branch/SHA/worktree and current container health.
- `git fetch origin`; fast-forward `deploy/v0.2` to the exact target SHA only if it is a clean fast-forward and worktree is clean.
- Rebuild and restart only the `engine` service in this Compose project.
- Keep all existing `.env` values unchanged.
- Write new provider data only under the existing persistent `/data/evidence/` directory by running the explicit CLI commands below.
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
6. Confirm the image is `ai-investment-engine:0.4`, container healthy, `/healthz` returns HTTP 200 with `version=0.4.0`, and port remains bound only to `127.0.0.1:8018`. Confirm `read_only`, `cap_drop: ALL`, and `no-new-privileges` remain enabled.

## Public data sync

Run the following once each. These write only local metadata files in `/data/evidence`:

```bash
docker compose exec -T engine python -m investment_engine.market_cli sync-disclosures \
  --symbol 601118 --start-date 2020-01-01 --end-date "$(date +%F)" --max-pages 5

docker compose exec -T engine python -m investment_engine.market_cli sync-industry

# Parse the already-indexed 2026 Q3 report once
docker compose exec -T engine python -m investment_engine.market_cli sync-financials --symbol 601118 --period 2026-Q3
```

Do not retry a failed provider call without ChatGPT review. A source failure must not create synthetic rows.

## Validation and report

Read local `/data/evidence/source_documents.json`, `/data/evidence/industry_observations.json`, and `/data/evidence/financial_facts.json` metadata only (do not dump full files or raw PDF text).

Report:
- actual Git SHA, branch, image tag, health and local bind;
- CNINFO rows saved, first/last published_at, source name, whether source URLs are HTTPS, and confirm `content_status=metadata_only_pdf_not_parsed`;
- inventory rows saved, first/last effective date, series_id and source; confirm its statistical scope is **unknown**;
- explicitly confirm inventory unit is `source_native_unverified`, `published_at=null`, `unit_verified=false`, so it is not eligible for thesis assessment and is not verified as exchange delivery or social inventory;
- financial fact count, metrics, period labels, source pages, audit status, and confirm every record remains `machine_extracted_pending_human_review`, `reviewed_by_human=false`, `unit_verified=false`, `eligible_for_assessment=false`;
- original 601118/RU0 market CSV counts and report files remain present;
- confirm OTW state was not read/written by this task, no orders and no schedules were touched.

Do not output any secret, account/holding details, complete report contents or raw announcement PDFs.

## Failure handling

If build or health fails, stop the deployment. If one independent public-source sync fails, record its sanitized error and continue only with the other explicitly listed independent sync once; retain old data and do not retry either provider. Do not downgrade, reset or edit code. ChatGPT will prepare a new PR if code changes are required.
