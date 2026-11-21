"""Run from the repository root with python -m examples.v08_demo."""
import json
import os
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from investment_engine.api import app
from investment_engine.evidence import EvidenceStore, Observation
from investment_engine.market_data import Bar, MarketDataStore

AS_OF = "2025-12-31T12:30:00+00:00"
WINDOWS = {
    "is_start": "2025-01-01", "is_end": "2025-06-30",
    "oos_start": "2025-07-01", "oos_end": "2025-12-30",
}


def seed_demo(root: Path, symbol: str, asset_class: str, *, reviewed: bool) -> str:
    """Seed explicitly synthetic daily bars and one linked research observation."""
    cache_symbol = symbol.split(".")[0]
    bars = []
    start = date.fromisoformat(WINDOWS["is_start"])
    for index in range(364):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        price = round(4 + index * 0.001 + (index % 80) * 0.004, 3)
        bars.append(Bar(
            symbol=cache_symbol, exchange="SSE", asset_type=asset_class, interval="1d",
            timestamp=day.isoformat(), open=price, high=price + 0.05, low=price - 0.05,
            close=price, volume=1000, amount=None, source="synthetic.v08.demo",
            fetched_at="2025-12-31T00:00:00+00:00", adjustment="none",
        ))
    MarketDataStore(root).save(bars)
    observation = Observation.create(
        series_id=f"fixture.{cache_symbol}.observation", subject=cache_symbol, factor="fixture",
        metric="nav_per_share" if asset_class == "etf" else "industry_inventory",
        value=4.2 if asset_class == "etf" else 1800,
        unit="CNY/share" if asset_class == "etf" else "tonnes",
        effective_at="2025-12-30T07:00:00+00:00", published_at="2025-12-30T09:00:00+00:00",
        retrieved_at="2025-12-31T00:00:00+00:00", source_name="synthetic.v08.demo",
        source_url=f"https://example.com/synthetic/{cache_symbol}",
        series_definition="Synthetic research observation for API verification",
        assessment="supporting", reviewed_by_human=reviewed, unit_verified=reviewed,
        metadata={"entity_id": cache_symbol, "reviewed_at": "2025-12-31T01:00:00+00:00"} if reviewed else {},
    )
    EvidenceStore(root).save_observations([observation])
    return observation.observation_id


def main() -> None:
    with TemporaryDirectory(prefix="engine-v08-demo-") as directory:
        root = Path(directory)
        env = {"ENGINE_DATA_DIR": directory, "ENGINE_API_TOKEN": "local-synthetic-demo"}
        headers = {"Authorization": f"Bearer {env['ENGINE_API_TOKEN']}"}
        with patch.dict(os.environ, env, clear=True), TestClient(app) as client:
            for symbol, asset_class in [("601118.SH", "stock"), ("510300", "etf")]:
                seed_demo(root, symbol, asset_class, reviewed=False)
                response = client.post(
                    "/v1/research/analyze", headers=headers,
                    json={
                        "entity_id": symbol, "asset_class": asset_class, "as_of": AS_OF,
                        "experiment": WINDOWS,
                    },
                )
                response.raise_for_status()
                audit = response.json()["audit"]
                saved = client.get(f"/v1/reports/{audit['report_id']}", headers=headers)
                saved.raise_for_status()
                print(json.dumps(saved.json(), ensure_ascii=False))


if __name__ == "__main__":
    main()
