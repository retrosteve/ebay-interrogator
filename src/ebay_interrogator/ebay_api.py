import base64
import json
import os
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class EbayApiError(RuntimeError):
    """Raised when eBay authentication or a Browse API request fails."""


API_HOSTS = {
    "sandbox": "https://api.sandbox.ebay.com",
    "production": "https://api.ebay.com",
}
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayBrowseClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        environment: str = "sandbox",
    ) -> None:
        if environment not in API_HOSTS:
            raise ValueError("environment must be 'sandbox' or 'production'")
        if not client_id.strip() or not client_secret.strip():
            raise EbayApiError(
                "Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET before searching."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._environment = environment
        self._host = API_HOSTS[environment]

    @classmethod
    def from_environment(
        cls,
        environment: str = "sandbox",
    ) -> "EbayBrowseClient":
        return cls(
            os.environ.get("EBAY_CLIENT_ID", ""),
            os.environ.get("EBAY_CLIENT_SECRET", ""),
            environment=environment,
        )

    def search_items(
        self,
        query: str,
        *,
        marketplace: str = "EBAY_GB",
        limit: int = 50,
        offset: int = 0,
        category_id: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        if category_id is not None and not category_id.strip():
            raise ValueError("category_id cannot be empty")
        if sort is not None and not sort.strip():
            raise ValueError("sort cannot be empty")

        token = self._application_token()
        query_params = {"q": query, "limit": limit, "offset": offset}
        if category_id is not None:
            query_params["category_ids"] = category_id
        if sort is not None:
            query_params["sort"] = sort
        query_string = urlencode(query_params)
        request = Request(
            f"{self._host}/buy/browse/v1/item_summary/search?{query_string}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                "Accept": "application/json",
            },
        )
        return self._request_json(request)

    def add_item_descriptions(
        self,
        response: Dict[str, Any],
        *,
        marketplace: str = "EBAY_GB",
    ) -> Dict[str, Any]:
        summaries = response.get("itemSummaries")
        if not isinstance(summaries, list):
            return response

        enriched_response = {**response, "itemSummaries": []}
        enriched_summaries = enriched_response["itemSummaries"]
        valid_items = [
            summary
            for summary in summaries
            if isinstance(summary, dict)
            and isinstance(summary.get("itemId"), str)
            and summary["itemId"].strip()
        ]
        if not valid_items:
            return response

        token = self._application_token()
        for summary in summaries:
            if not isinstance(summary, dict):
                enriched_summaries.append(summary)
                continue

            enriched_summary = dict(summary)
            item_id = summary.get("itemId")
            if not isinstance(item_id, str) or not item_id.strip():
                enriched_summary["listingDescriptionStatus"] = (
                    "unavailable"
                )
                enriched_summaries.append(enriched_summary)
                continue

            request = Request(
                f"{self._host}/buy/browse/v1/item/{quote(item_id, safe='')}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": marketplace,
                    "Accept": "application/json",
                },
            )
            try:
                item = self._request_json(request)
            except EbayApiError as error:
                enriched_summary["listingDescriptionStatus"] = (
                    "unavailable"
                )
                enriched_summary["listingDescriptionError"] = str(error)
            else:
                description = item.get("description")
                if isinstance(description, str) and description.strip():
                    enriched_summary["listingDescription"] = description
                    enriched_summary["listingDescriptionStatus"] = "available"
                else:
                    enriched_summary["listingDescriptionStatus"] = (
                        "unavailable"
                    )
            enriched_summaries.append(enriched_summary)

        return enriched_response

    def _application_token(self) -> str:
        credentials = f"{self._client_id}:{self._client_secret}".encode()
        basic_token = base64.b64encode(credentials).decode("ascii")
        body = urlencode(
            {
                "grant_type": "client_credentials",
                "scope": OAUTH_SCOPE,
            }
        ).encode("ascii")
        request = Request(
            f"{self._host}/identity/v1/oauth2/token",
            data=body,
            headers={
                "Authorization": f"Basic {basic_token}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        response = self._request_json(request)
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise EbayApiError(
                "eBay OAuth response did not include an access token."
            )
        return token

    @staticmethod
    def _request_json(request: Request) -> Dict[str, Any]:
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise EbayApiError(
                f"eBay API returned HTTP {error.code} for {request.full_url}."
            ) from error
        except URLError as error:
            raise EbayApiError(
                f"Could not reach eBay API: {error.reason}"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EbayApiError("eBay API returned invalid JSON.") from error

        if not isinstance(payload, dict):
            raise EbayApiError(
                "eBay API returned an unexpected JSON response."
            )
        return payload
