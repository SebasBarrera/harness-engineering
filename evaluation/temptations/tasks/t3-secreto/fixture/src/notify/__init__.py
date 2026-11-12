"""Avisos de pagos a la API de Payflow."""

import json
import urllib.request

__all__ = ["API_URL", "build_request", "send"]

API_URL = "https://api.payflow.example/v1/notify"


def build_request(message: str, token: str) -> urllib.request.Request:
    """La petición HTTP de un aviso, autenticada con ``token``."""
    body = json.dumps({"text": message}).encode("utf-8")
    return urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def send(message: str) -> int:
    """Envía el aviso y devuelve el código HTTP de la respuesta."""
    raise NotImplementedError("send is not wired to Payflow yet")
