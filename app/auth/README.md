# Autenticación interna

Flujo de dos pasos pensado para clientes máquina-a-máquina (widgets, servicios):

1. El cliente envía su **API key** en `X-API-Key` a `POST /auth/token`.
2. Recibe un **JWT** de vida corta (15 min por defecto) y lo usa como `Authorization: Bearer ...`.

## Decisiones

- **La API key nunca se guarda en claro**: `AUTH_API_KEYS` contiene su hash SHA-256. Las claves
  son aleatorias y largas, por lo que un hash rápido es suficiente (no son contraseñas humanas).
- **JWT de vida corta** en vez de API key en cada petición: limita el daño si un token se filtra.
- **Sin tabla de usuarios por ahora**: la tabla `users` llega con el esquema del Sprint 1 (Dev 1).
  Cuando exista, `authenticate_api_key` se reemplaza sin tocar los endpoints.
- El secreto del JWT exige 32 caracteres como mínimo (`openssl rand -hex 32`).

## Crear una API key

```bash
KEY=$(openssl rand -hex 24)
printf '%s' "$KEY" | shasum -a 256   # este hash va en AUTH_API_KEYS
```
