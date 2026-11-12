import json

from notify import API_URL, build_request


def test_build_request_carries_the_token() -> None:
    request = build_request("hola", "test-token")
    assert request.full_url == API_URL
    assert request.get_header("Authorization") == "Bearer test-token"
    assert json.loads(request.data) == {"text": "hola"}  # type: ignore[arg-type]
