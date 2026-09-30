from __future__ import annotations

import json
import sys
from collections.abc import Callable

from governed_harness.domain.enums import ResultStatus
from governed_harness.plugins.protocol import (
    PLUGIN_EXIT_INTERNAL_ERROR,
    PLUGIN_EXIT_PROTOCOL_ERROR,
    PluginRequest,
    PluginResponse,
)

PluginHandler = Callable[[PluginRequest], PluginResponse]


def serve_once(handler: PluginHandler) -> int:
    try:
        raw = sys.stdin.readline()
        request = PluginRequest.model_validate(json.loads(raw))
    except Exception as error:
        print(json.dumps({"error": f"invalid request: {error}"}), file=sys.stderr)
        return PLUGIN_EXIT_PROTOCOL_ERROR
    try:
        response = handler(request)
        if response.request_id != request.request_id:
            raise ValueError("handler returned a response for another request")
        print(json.dumps(response.model_dump(mode="json", by_alias=True), sort_keys=True))
        return 0
    except Exception as error:
        response = PluginResponse(
            requestId=request.request_id,
            status=ResultStatus.ERROR,
            kind="PLUGIN_INTERNAL_ERROR",
            summary=str(error),
            errors=({"type": type(error).__name__, "message": str(error)},),
        )
        print(json.dumps(response.model_dump(mode="json", by_alias=True), sort_keys=True))
        return PLUGIN_EXIT_INTERNAL_ERROR


def echo_handler(request: PluginRequest) -> PluginResponse:
    return PluginResponse(
        requestId=request.request_id,
        status=ResultStatus.PASSED,
        kind="SUCCESS",
        summary=f"Operation {request.operation} accepted",
        payload=request.payload,
    )


def main() -> None:
    raise SystemExit(serve_once(echo_handler))


if __name__ == "__main__":
    main()
