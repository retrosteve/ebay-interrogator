import base64
from io import BytesIO
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from ebay_interrogator.ebay_api import EbayApiError, EbayBrowseClient


class EbayBrowseClientTests(unittest.TestCase):
    @patch("ebay_interrogator.ebay_api.urlopen")
    def test_requests_token_then_searches_uk_browse_api(
        self,
        mock_urlopen,
    ) -> None:
        mock_urlopen.side_effect = [
            BytesIO(b'{"access_token":"sandbox-token"}'),
            BytesIO(b'{"total":1,"itemSummaries":[{"title":"Demo"}]}'),
        ]
        client = EbayBrowseClient(
            "test-client-id",
            "test-client-secret",
            environment="sandbox",
        )

        response = client.search_items("Switch OLED HEG-001", limit=10)

        self.assertEqual(response["total"], 1)
        token_request = mock_urlopen.call_args_list[0].args[0]
        self.assertEqual(token_request.get_method(), "POST")
        self.assertEqual(
            token_request.full_url,
            "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        )
        auth_value = next(
            value
            for key, value in token_request.header_items()
            if key.lower() == "authorization"
        )
        expected_auth = base64.b64encode(
            b"test-client-id:test-client-secret"
        ).decode("ascii")
        self.assertEqual(auth_value, f"Basic {expected_auth}")
        form_data = parse_qs(token_request.data.decode("ascii"))
        self.assertEqual(form_data["grant_type"], ["client_credentials"])

        search_request = mock_urlopen.call_args_list[1].args[0]
        self.assertEqual(
            urlparse(search_request.full_url).path,
            "/buy/browse/v1/item_summary/search",
        )
        search_params = parse_qs(urlparse(search_request.full_url).query)
        self.assertEqual(search_params["q"], ["Switch OLED HEG-001"])
        self.assertEqual(
            search_request.get_header("Authorization"),
            "Bearer sandbox-token",
        )
        self.assertEqual(
            search_request.get_header("X-ebay-c-marketplace-id"),
            "EBAY_GB",
        )

    def test_requires_credentials(self) -> None:
        with self.assertRaisesRegex(EbayApiError, "EBAY_CLIENT_ID"):
            EbayBrowseClient("", "", environment="sandbox")

    def test_reports_http_errors_without_exposing_credentials(self) -> None:
        client = EbayBrowseClient("client-id", "client-secret")
        error = HTTPError(
            "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
            401,
            "Unauthorized",
            {},
            BytesIO(b""),
        )
        with patch("ebay_interrogator.ebay_api.urlopen", side_effect=error):
            with self.assertRaisesRegex(EbayApiError, "HTTP 401") as context:
                client.search_items("Switch OLED")
        self.assertNotIn("client-secret", str(context.exception))

    def test_rejects_invalid_page_size(self) -> None:
        client = EbayBrowseClient("client-id", "client-secret")
        with self.assertRaisesRegex(ValueError, "between 1 and 200"):
            client.search_items("Switch OLED", limit=201)


if __name__ == "__main__":
    unittest.main()
