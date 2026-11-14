# Guía de administración

Para administradores y operadores del panel. Describe el panel web y la API `/admin` que lo respalda.

## Primer administrador

Aún no hay usuarios con rol. Aplica `migrations/20261114_users_roles.sql` (idempotente) y crea el primer
admin. El `external_id` debe ser igual al nombre del cliente de la API key en `AUTH_API_KEYS`:

```sql
INSERT INTO inmobiliaria_pruebas.users (external_id, email, role)
VALUES ('demo', 'admin@example.com', 'admin')
ON CONFLICT (external_id) DO UPDATE SET role = EXCLUDED.role;
```

Para revocar acceso: `UPDATE inmobiliaria_pruebas.users SET role = 'end_user' WHERE external_id = 'demo';`
El rol se lee de la base en cada petición, así que el cambio surte efecto de inmediato.

## Iniciar sesión

1. Abre `/admin/ui` en el navegador.
2. Escribe tu API key y pulsa **Entrar**. El panel la canjea por un token de sesión de vida corta y la descarta.
3. Arriba verás tu usuario y rol. **Cerrar sesión** borra el token y los datos mostrados.

Si el token expira, el panel vuelve al inicio de sesión. Si tu usuario no tiene rol `operator` o `admin`,
verás "Sin permiso".

## Vistas

- **Aprobaciones**: acciones de herramientas pausadas esperando decisión humana. Cada tarjeta muestra la
  herramienta, los parámetros, canal, usuario y desde cuándo está pausada. **Aprobar** o **Rechazar** abre
  una confirmación con herramienta y parámetros, y un motivo opcional; nada se decide sin confirmar.
  La lista se actualiza con **Actualizar** y cada 30 s mientras la pestaña está visible. Si otra persona
  ya decidió, el panel avisa (conflicto) y refresca.
- **Conversaciones**: historial filtrable por canal y por usuario (id de conversación exacto), paginado.
  **Ver mensajes** abre la conversación en orden cronológico.
- **Auditoría** (solo admin): ejecuciones de herramientas con filtros por estado y herramienta, paginadas.
  No muestra el resultado de la herramienta.

## Roles

| Función                          | operator | admin |
| -------------------------------- | :------: | :---: |
| Ver y decidir aprobaciones       |    si    |  si   |
| Ver conversaciones y mensajes    |    si    |  si   |
| Ver auditoría de herramientas    |    no    |  si   |
| Listar usuarios (`/admin/users`) |    no    |  si   |

El panel oculta lo que tu rol no puede usar, pero quien decide es el servidor: una petición sin permiso
responde `403` aunque se haga fuera del panel.

## Notas de seguridad

- El token vive solo en memoria: recargar la página exige iniciar sesión otra vez.
- La API key no se guarda en el navegador. Usa un equipo de confianza y cierra sesión al terminar.
- La página aplica una CSP estricta: sin scripts, estilos ni conexiones externas.
- Quien aprueba queda registrado según el usuario autenticado, nunca según lo que envíe el navegador.
- Sirve el panel solo por HTTPS fuera de desarrollo.

## Endpoints

### GET /admin/ui
Panel web (HTML). No requiere autenticación; los datos se piden a la API con token.

### GET /admin/me
Usuario y rol actuales.

### GET /admin/users
Lista de usuarios (solo admin).

### GET /admin/approvals
Aprobaciones pendientes con su herramienta y parámetros.

### POST /admin/approvals/{call_id}/decision
Aprueba o rechaza una acción. Cuerpo: `thread_id`, `turn_id`, `tool_name`, `decision` (`approved` o `rejected`) y `reason` opcional. Es idempotente.

### GET /admin/conversations
Conversaciones filtrables por `channel` y `user`, con `limit` y `offset`.

### GET /admin/conversations/{thread_id}/messages
Mensajes de una conversación, en orden cronológico.

### GET /admin/audit/tool-executions
Auditoría de ejecuciones, filtrable por `status` y `tool` (solo admin).

Detalle de respuestas y códigos de error: `app/admin/README.md`.
