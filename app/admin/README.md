# API del panel de administración

Backend del panel: bandeja de aprobaciones HITL, historial de conversaciones y auditoría de
`tool_executions`, con control de acceso por roles (RBAC). Todas las rutas viven bajo `/admin`.

## Acceso y roles

1. El usuario del panel tiene una **API key** (ver `app/auth/README.md`).
2. La canjea en `POST /auth/token` (cabecera `X-API-Key`) y recibe un JWT.
3. Llama a `/admin/*` con `Authorization: Bearer <jwt>`. La API key sola **no** sirve en `/admin`.

El `sub` del JWT (nombre del cliente de la API key) se busca en `users.external_id`. El rol se
lee de la base de datos **en cada petición**: quitar o cambiar un rol surte efecto de inmediato,
aunque el token siga vigente. No hay contraseñas.

| Endpoint                                      | operator | admin |
| --------------------------------------------- | :------: | :---: |
| `GET /admin/me`                               |    si    |  si   |
| `GET /admin/approvals`                        |    si    |  si   |
| `POST /admin/approvals/{call_id}/decision`    |    si    |  si   |
| `GET /admin/conversations`                    |    si    |  si   |
| `GET /admin/conversations/{thread_id}/messages` |  si    |  si   |
| `GET /admin/audit/tool-executions`            |    no    |  si   |
| `GET /admin/users`                            |    no    |  si   |

`end_user` y los usuarios inexistentes en `users` no acceden a nada.

### Crear el primer admin

Aplicar `migrations/20261114_users_roles.sql` (idempotente) y luego, con `external_id` igual al
nombre del cliente en `AUTH_API_KEYS`:

```sql
INSERT INTO inmobiliaria_pruebas.users (external_id, email, role)
VALUES ('demo', 'admin@example.com', 'admin')
ON CONFLICT (external_id) DO UPDATE SET role = EXCLUDED.role;
```

Revocar: `UPDATE inmobiliaria_pruebas.users SET role = 'end_user' WHERE external_id = 'demo';`

## Convenciones

- Listados paginados: `limit` (1-200, por defecto 50) y `offset` (>= 0). Valores fuera de rango
  responden `422`. Respuesta: `{"items": [...], "total": N, "limit": N, "offset": N}`.
- Fechas en ISO 8601. Los filtros usan valores validados; un valor inválido responde `422`.
- Errores: `{"detail": "<mensaje en español>"}` sin datos internos.

| Código | Significado                                                                  |
| ------ | ---------------------------------------------------------------------------- |
| 401    | Sin token Bearer, o token inválido/expirado                                  |
| 403    | Usuario desconocido, `end_user` o sin el rol requerido                       |
| 404    | Conversación inexistente                                                     |
| 409    | La acción ya no está pendiente, o ya tiene otra decisión                     |
| 422    | Parámetros o cuerpo inválidos                                                |
| 502    | La herramienta aprobada falló al ejecutarse                                  |
| 503    | Base de datos o servicio de conversación no disponible/configurado           |

## Endpoints

### `GET /admin/me`
`{"id": "uuid", "external_id": "demo", "email": null, "role": "operator|admin"}`

### `GET /admin/users` (admin)
Lectura solamente. Items: `{id, external_id, email, role, created_at}`.

### `GET /admin/approvals`
Hilos en `paused_hil` con su llamada pendiente, leída del checkpoint de LangGraph (solo lectura).
Items (ordenados del más antiguo al más reciente):

```json
{
  "thread_id": "uuid", "turn_id": "uuid", "call_id": "call-1",
  "tool_name": "send_whatsapp", "parameters": {"to": "5215...", "text": "..."},
  "channel": "whatsapp", "external_conversation_id": "5215...",
  "created_at": "2026-11-14T10:00:00+00:00"
}
```

`created_at` es la última actualización del hilo (momento en que quedó pausado). `total` cuenta
los hilos pausados; un hilo cuyo checkpoint ya no tiene llamada pendiente (o no se pudo leer) se
omite del listado, por lo que `items` puede traer menos elementos que `limit`.

### `POST /admin/approvals/{call_id}/decision`
Cuerpo: `{"thread_id": "uuid", "turn_id": "uuid", "tool_name": "send_whatsapp",
"decision": "approved|rejected", "reason": "opcional"}`.
Respuesta `200`: `{"call_id": "call-1", "status": "executed|rejected"}`.
El aprobador (`approved_by`) es siempre el `external_id` del usuario en la base, nunca el cuerpo.
Es idempotente: repetir la misma decisión devuelve el mismo resultado sin volver a ejecutar.
Usa la misma lógica que `POST /chat/approval`.

### `GET /admin/conversations?channel=&user=&limit=&offset=`
`channel`: `web_widget|whatsapp|meta`. `user`: coincidencia exacta con
`external_conversation_id`. Orden: `updated_at` descendente. Items:
`{thread_id, channel, external_conversation_id, status, created_at, updated_at}`.

### `GET /admin/conversations/{thread_id}/messages?limit=&offset=`
Orden cronológico. Respuesta: `{thread_id, channel, external_conversation_id, items, total, limit,
offset}`; items: `{id, turn_id, role (user|agent|system|human_operator), content, created_at}`.

### `GET /admin/audit/tool-executions?status=&tool=&limit=&offset=` (admin)
`status`: `processing|completed|failed`. `tool`: nombre de herramienta (letras, números, `_`, `.`,
`-`; máx. 100). Orden: `created_at` descendente. Items: `{id, thread_id, turn_id, call_id,
tool_name, decision, status, approved_by, created_at, completed_at}`. El `result` de la
herramienta no se expone.

## Panel web

Interfaz estática (HTML, CSS y módulos ES, sin dependencias ni paso de build) servida por la propia
API en `GET /admin/ui`. Los archivos están en `app/admin/static/` y se exponen solo desde una lista
explícita en `app/api/routes/admin_ui.py` (`/admin/ui/assets/<archivo>`).

- `index.html`: estructura, diálogo de confirmación y vistas. Sin scripts ni estilos en línea.
- `admin.js` (entrada), `api.js` (fetch con JWT en memoria), `render.js` (helpers puros) y `state.js`.
- El cascarón estático no exige autenticación; la API sigue siendo quien autoriza cada petición.
- Cabeceras: CSP estricta (`default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; ...`),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` y `Cache-Control: no-store` en el HTML.
- Seguridad del cliente: la API key se canjea en `POST /auth/token` y se descarta; el JWT vive solo en
  memoria (no `localStorage` ni cookies); todo dato dinámico se pinta con `textContent`; `401` vuelve al
  login y `403` muestra "Sin permiso". Decidir una aprobación exige confirmar viendo herramienta y parámetros.
- Vistas: Aprobaciones (todos los roles), Conversaciones (todos los roles) y Auditoría (solo `admin`).

Pruebas: `poetry run pytest tests/test_admin_ui.py` y `node --test tests/admin_ui` (Node 20+).
Guía de uso: `guias/administracion.md`.
