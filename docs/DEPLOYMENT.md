# Deployment notes for AI Investment Engine v0.2

Production-like deployment is performed by the user's DeepSeek Harness on the user's server. Do not copy server secrets into ChatGPT, GitHub, Issues, logs or this repository.

## Runtime configuration

Keep the existing server-only OTW and engine tokens. The deployed OTW transport is MCP:

~~~dotenv
OTW_TRANSPORT=mcp
ENGINE_DATA_DIR=/data
MARKET_DATA_PROVIDER=akshare
MARKET_DATA_DEFAULT_START_DATE=20180101
MARKET_DATA_DEFAULT_ADJUSTMENT=qfq
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
~~~

PIP_INDEX_URL is passed only as a Docker build argument. Dockerfile defaults to official PyPI; the mirror is configurable per environment.

## Persistent data directory

Compose mounts ./data to /data. The container runs as nobody (UID 65534), so create and assign the host directory before deployment:

~~~bash
mkdir -p data
sudo chown 65534:65534 data
chmod 750 data
~~~

The data directory is gitignored and must not be committed.

## Deploy

Read and follow [HARNESS_DEPLOY_TASK.md](HARNESS_DEPLOY_TASK.md). Merge PR #1 first, then deploy from origin/main. Do not overwrite the deployed v0.1.1 local branch or discard its local commits; preserve a backup branch and create a separate deploy/v0.2 branch from origin/main.

~~~bash
docker compose config --quiet
docker compose up -d --build engine
docker compose ps
curl --fail http://127.0.0.1:8018/healthz
~~~

The service must remain bound to 127.0.0.1:8018.

## First data sync

~~~bash
docker compose exec -T engine python -m investment_engine.market_cli sync \
  --asset-type stock --symbol 601118 --start-date 20180101 \
  --end-date 20261105 --adjustment qfq

docker compose exec -T engine python -m investment_engine.market_cli sync \
  --asset-type future --symbol RU0 --start-date 20180101 \
  --end-date 20261105 --adjustment continuous
~~~

AKShare public endpoints can change. If a provider call fails, report it and leave the series unavailable; never replace it with synthetic or unverified data.

## Safety boundary

This version only reads OTW through otw_read. It writes market data to the engine's own ./data directory and local backtest reports. It does not create OTW runs, orders or document updates, does not enable trading, and does not schedule recurring jobs.
