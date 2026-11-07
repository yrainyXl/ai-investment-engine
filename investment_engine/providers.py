"""Public-source adapters for company disclosures and rubber inventory proxies.

The CNINFO adapter indexes disclosure metadata only; it does not pretend to
extract audited financial facts from PDF text. The 99qh adapter is explicitly
limited to SHFE delivery-warehouse inventory, not national/social inventory.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from http.cookiejar import CookieJar
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

from .evidence import Observation

CNINFO_SEARCH_PAGE = "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"
CNINFO_TOP_SEARCH = "https://www.cninfo.com.cn/new/information/topSearch/query"
CNINFO_ANNOUNCEMENT_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE = "https://static.cninfo.com.cn/"
CNINFO_DETAIL_BASE = "https://www.cninfo.com.cn/new/disclosure/detail"
CNINFO_SOURCE_URL = "https://www.cninfo.com.cn/"
NINETY_NINE_INVENTORY_URL = "https://www.99qh.com/data/stockIn"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class ProviderError(RuntimeError):
    pass


def _aware_date(value: str, *, end_of_day: bool = False) -> datetime:
    try:
        parsed = date.fromisoformat(value[:10])
    except (TypeError, ValueError) as exc:
        raise ProviderError("date must use YYYY-MM-DD") from exc
    hour = 23 if end_of_day else 0
    minute = 59 if end_of_day else 0
    second = 59 if end_of_day else 0
    return datetime(parsed.year, parsed.month, parsed.day, hour, minute, second, tzinfo=SHANGHAI)


def _clean_title(value: Any) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


class CNInfoDisclosureProvider:
    """Fetch official CNINFO announcement index metadata for one A-share issuer."""

    def __init__(self, *, timeout: float = 20.0, opener: Any | None = None):
        self.timeout = timeout
        self.opener = opener or build_opener(HTTPCookieProcessor(CookieJar()))

    def _request_bytes(self, url: str, *, form: dict[str, Any] | None = None,
                       referer: str = CNINFO_SEARCH_PAGE) -> bytes:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer,
            "Origin": "https://www.cninfo.com.cn",
            "X-Requested-With": "XMLHttpRequest",
        }
        data = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            data = urlencode(form).encode("utf-8")
        request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except Exception as exc:
            raise ProviderError(f"CNINFO request failed: {type(exc).__name__}") from exc

    def _request_json(self, url: str, *, form: dict[str, Any] | None = None,
                      referer: str = CNINFO_SEARCH_PAGE) -> Any:
        try:
            return json.loads(self._request_bytes(url, form=form, referer=referer).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderError("CNINFO returned invalid JSON") from exc

    @staticmethod
    def _resolve_org_id(payload: Any, symbol: str) -> str:
        if isinstance(payload, dict):
            rows = payload.get("stockList") or payload.get("data") or []
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        if not isinstance(rows, list):
            raise ProviderError("CNINFO company lookup returned an unexpected shape")
        candidates: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            stock_code = str(row.get("scode") or row.get("stockCode") or row.get("secCode") or "").strip()
            org_id = str(row.get("orgId") or row.get("orgid") or "").strip()
            raw_code = str(row.get("code") or "").strip()
            name = str(row.get("zwjc") or row.get("secName") or row.get("name") or "").strip()
            if stock_code and stock_code != symbol:
                continue
            if not org_id and raw_code and raw_code != symbol:
                org_id = raw_code
            if org_id and (stock_code == symbol or name == "海南橡胶" or len(rows) == 1):
                candidates.append((org_id, name))
        unique = {item[0] for item in candidates}
        if len(unique) != 1:
            raise ProviderError("Could not resolve a unique CNINFO orgId for the requested symbol")
        return next(iter(unique))

    @staticmethod
    def _published_at(raw: Any) -> str:
        try:
            milliseconds = int(raw)
            if milliseconds <= 0:
                raise ValueError
            return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).astimezone(SHANGHAI).isoformat()
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise ProviderError("CNINFO announcement has no valid publication timestamp") from exc

    def fetch(self, *, symbol: str = "601118", start_date: str = "2020-01-01",
              end_date: str | None = None, max_pages: int = 5) -> list[dict[str, Any]]:
        if not re.fullmatch(r"\d{6}", symbol):
            raise ProviderError("symbol must be a six-digit A-share code")
        start = _aware_date(start_date).date()
        end = _aware_date(end_date or datetime.now(SHANGHAI).date().isoformat()).date()
        if end < start:
            raise ProviderError("end_date must not precede start_date")
        if max_pages < 1 or max_pages > 20:
            raise ProviderError("max_pages must be between 1 and 20")

        # Bootstrap the public search session so CNINFO can issue its session cookie.
        self._request_bytes(CNINFO_SEARCH_PAGE)
        company = self._request_json(
            CNINFO_TOP_SEARCH, form={"keyWord": symbol, "maxNum": 10},
            referer=CNINFO_SEARCH_PAGE,
        )
        org_id = self._resolve_org_id(company, symbol)

        documents: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            payload = {
                "pageNum": page, "pageSize": 30, "column": "sse", "tabName": "fulltext",
                "plate": "sh", "stock": f"{symbol},{org_id}", "searchkey": "", "secid": "",
                "category": "", "trade": "", "seDate": f"{start.isoformat()}~{end.isoformat()}",
                "sortName": "", "sortType": "", "isHLtitle": "true",
            }
            result = self._request_json(
                CNINFO_ANNOUNCEMENT_QUERY, form=payload, referer=CNINFO_SEARCH_PAGE,
            )
            if not isinstance(result, dict) or not isinstance(result.get("announcements", []), list):
                raise ProviderError("CNINFO announcement query returned an unexpected shape")
            rows = result.get("announcements") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                announcement_id = str(row.get("announcementId") or "").strip()
                title = _clean_title(row.get("announcementTitle"))
                if not announcement_id or not title:
                    continue
                published_at = self._published_at(row.get("announcementTime"))
                adjunct = str(row.get("adjunctUrl") or "").strip()
                pdf_url = urljoin(CNINFO_STATIC_BASE, adjunct) if adjunct else ""
                detail_url = (
                    f"{CNINFO_DETAIL_BASE}?stockCode={symbol}&announcementId={announcement_id}"
                    f"&orgId={org_id}&announcementTime={published_at[:10]}"
                )
                documents.append({
                    "document_id": f"cninfo:{symbol}:{announcement_id}",
                    "symbol": symbol,
                    "issuer_name": str(row.get("secName") or "海南橡胶"),
                    "title": title,
                    "document_type": str(row.get("adjunctType") or ""),
                    "published_at": published_at,
                    "effective_at": None,
                    "source_name": "cninfo.official_disclosure_index",
                    "source_url": pdf_url if pdf_url.startswith("https://") else detail_url,
                    "detail_url": detail_url,
                    "org_id": org_id,
                    "announcement_id": announcement_id,
                    "content_status": "metadata_only_pdf_not_parsed",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                })
            total_pages = result.get("totalpages")
            if isinstance(total_pages, int) and page >= total_pages:
                break
            if not rows:
                break
        documents.sort(key=lambda item: (item["published_at"], item["document_id"]))
        return documents


class AkShareRubberInventoryProvider:
    """Fetch the 99qh/AKShare SHFE delivery-warehouse inventory series.

    This is not Qingdao bonded/general-trade inventory or China's total social
    inventory. The current interface schema does not document a measurement
    unit or a separate publication timestamp, so those fields remain unknown
    and observations are deliberately ineligible for thesis assessment.
    """

    def fetch(self, *, as_of: datetime | None = None) -> list[Observation]:
        try:
            import akshare as ak
        except ImportError as exc:
            raise ProviderError("AKShare is not installed") from exc
        try:
            frame = ak.futures_inventory_99(exchange="上海期货交易所", symbol="天然橡胶")
            rows = frame.to_dict(orient="records")
        except Exception as exc:
            raise ProviderError(f"99qh inventory source failed: {type(exc).__name__}") from exc
        if not rows:
            raise ProviderError("99qh returned no natural-rubber inventory rows")

        retrieved_at = datetime.now(timezone.utc)
        cutoff = as_of.astimezone(SHANGHAI).date() if as_of else None
        output: list[Observation] = []
        seen: set[str] = set()
        for row in rows:
            day_value = row.get("日期", row.get("date"))
            inventory_value = row.get("库存", row.get("inventory"))
            change_value = row.get("增减", row.get("change"))
            try:
                day = date.fromisoformat(str(day_value)[:10])
                inventory = float(inventory_value)
                change = float(change_value) if change_value not in (None, "") else None
            except (TypeError, ValueError) as exc:
                raise ProviderError("99qh inventory row has an invalid date or value") from exc
            if not math.isfinite(inventory) or inventory < 0:
                raise ProviderError("99qh inventory value must be finite and non-negative")
            if change is not None and not math.isfinite(change):
                raise ProviderError("99qh inventory change must be finite")
            if cutoff and day > cutoff:
                continue
            effective_at = datetime(day.year, day.month, day.day, tzinfo=SHANGHAI).isoformat()
            if effective_at in seen:
                raise ProviderError(f"99qh returned duplicate inventory date: {day.isoformat()}")
            seen.add(effective_at)
            output.append(Observation.create(
                series_id="shfe_ru_delivery_warehouse_inventory_99qh",
                subject="SHFE natural rubber delivery warehouses",
                factor="multiweek_inventory_trend",
                metric="delivery_warehouse_inventory",
                value=inventory,
                unit="source_native_unverified",
                effective_at=effective_at,
                published_at=None,
                retrieved_at=retrieved_at,
                source_name="akshare.99qh.futures_inventory_99",
                source_url=NINETY_NINE_INVENTORY_URL,
                evidence_type="confirmed_fact",
                assessment="unassessed",
                reviewed_by_human=False,
                unit_verified=False,
                series_definition=(
                    "99qh/AKShare Shanghai Futures Exchange natural-rubber delivery-warehouse inventory; "
                    "not Qingdao bonded/general-trade stock and not China's total social inventory. "
                    "Unit and publication timestamp are not documented by the current adapter schema."
                ),
                detail="Source-reported inventory value; do not compare with other inventory series until unit and scope are verified.",
                metadata={"change": change, "frequency": "source_reported"},
            ))
        output.sort(key=lambda item: item.effective_at)
        return output
