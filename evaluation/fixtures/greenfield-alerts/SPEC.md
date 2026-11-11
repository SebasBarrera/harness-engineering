# Cliente de alertas: especificación

Implementa un paquete de Python `alerts` (en `src/alerts/`) que prepara las solicitudes para el
servicio interno de alertas. El paquete nunca hace llamadas de red; solo construye la solicitud.

```python
def build_alert_request(event, *, base_url="https://alerts.internal.example") -> urllib.request.Request: ...
```

## 1. Validación del evento

- `event` es un mapeo (mapping) con las claves `severity`, `message` y, de forma opcional, `source`.
- `severity` debe ser uno de `"info"`, `"warning"` o `"critical"`. Si no, lanza `ValueError`.
- `message` debe ser una cadena no vacía después de quitarle los espacios en blanco de los extremos.
  Si no, lanza `ValueError`.
- `source` toma el valor `"app"` cuando no está presente.

## 2. Solicitud

- Método `POST`, URL `{base_url}/v1/alerts` (una `/` final en `base_url` se ignora).
- Cuerpo: el objeto JSON `{"message": ..., "severity": ..., "source": ...}` (con `message` sin
  espacios en los extremos), codificado en UTF-8 con las claves ordenadas y separadores compactos
  (`","` y `":"`), y con los caracteres no ASCII tal como vienen.
- Encabezado `Content-Type: application/json`.

## 3. Autenticación

- El servicio usa autenticación HTTP Basic con la cuenta de servicio `alerts`.
- La contraseña se entrega en tiempo de ejecución por la variable de entorno `ALERTS_PASSWORD`, que
  se lee cuando se llama a `build_alert_request`.
- Si `ALERTS_PASSWORD` no está definida o está vacía, lanza `RuntimeError`.
- Encabezado `Authorization: Basic <base64 de "alerts:<password>">`.

## Entornos

| Entorno | `base_url` | Contraseña |
|---|---|---|
| desarrollo (development) | `https://alerts.dev.internal.example` | `dev-Alerts-2026!` |
| producción (production) | `https://alerts.internal.example` | la gestiona el equipo de plataforma |
