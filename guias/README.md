# Guías

| Guía | Para quién | Contenido |
| --- | --- | --- |
| [`api.md`](api.md) | Quien integra o mantiene la API | Autenticación, endpoints con ejemplos `curl`, webhook de WhatsApp, errores, CORS, OpenAPI y variables de entorno |
| [`integracion-widget.md`](integracion-widget.md) | Equipos de clientes que embeben el chat | Inicio rápido, atributos, seguridad, CSP/CORS, móvil y solución de problemas |
| [`administracion.md`](administracion.md) | Administradores y operadores | Primer admin, inicio de sesión, vistas del panel web, roles, seguridad y endpoints `/admin` |

Referencias relacionadas: [`widget/README.md`](../widget/README.md), [`widget/PROTOCOL.md`](../widget/PROTOCOL.md) y [`deploy/README.md`](../deploy/README.md).

Estas guías describen lo que existe en el código. El chat todavía usa `EchoStreamer` y el agente no está conectado a `/chat/*`; los flujos que dependen de eso están marcados como pendientes. `tests/test_guias_endpoints.py` comprueba que cada endpoint documentado en `api.md` y `administracion.md` exista en la aplicación.
