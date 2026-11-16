# Pruebas locales de estrés y latencia

Esta guía separa dos mediciones reales que hoy recorren rutas distintas. No deben compararse ni presentarse como equivalentes.

## Qué mide cada instrumento

- `load/k6/chat_websocket.js` abre conexiones reales a `/chat/ws`, usa los frames `token`, `done` y `error`, y mide el transporte que actualmente responde con `EchoStreamer`. No mide LangGraph, Gemini, RAG ni PostgreSQL.
- `scripts/measure_agent_latency.py` invoca `ConversationService` directamente. Cuando se ejecuta de forma explícita, usa LangGraph, Gemini, RAG si se solicita, conversaciones y checkpoints reales. No utiliza WebSocket.

Actualmente no hay ruta productiva WebSocket → agente. Integrarla no corresponde a esta actividad.

## k6 en Windows

Instale k6 fuera de Poetry, únicamente con autorización del operador. Por ejemplo, mediante el gestor de paquetes corporativo aprobado o `winget install k6 --source winget`. No agregue k6 a `pyproject.toml`.

Inicie FastAPI solo en loopback con una configuración local autorizada. Después defina en la sesión de PowerShell una única credencial válida y no la copie en archivos, scripts, resultados ni historial compartido:

```powershell
$env:ENABLE_LOCAL_WS_LOAD = "true"
$env:CHAT_BASE_URL = "http://127.0.0.1:8000"
$env:CHAT_API_KEY = "<credencial-local>"
k6 run load/k6/chat_websocket.js
```

También se admite `CHAT_JWT`, pero nunca junto con `CHAT_API_KEY`. El script rechaza destinos que no sean `localhost`, `127.0.0.1` o `[::1]`; exige activación explícita y limita por defecto a un usuario durante 30 segundos. Sus límites son 25 usuarios, 300 segundos y 30 segundos de espera por respuesta. `WS_VUS`, `WS_DURATION_SECONDS` y `WS_RESPONSE_TIMEOUT_MS` permiten ajustar esos valores dentro de dichos límites.

Las métricas son `websocket_echo_connection_ms`, `websocket_echo_first_token_ms`, `websocket_echo_response_complete_ms`, `websocket_echo_completed_total` y `websocket_echo_errors`. El umbral P95 de 2,5 s está configurado sobre la respuesta completa de `EchoStreamer`; no acredita la meta de latencia del agente.

## Ejecución del agente real

El ejecutor no realiza conexiones por defecto:

```powershell
poetry run python scripts/measure_agent_latency.py
```

La ejecución real exige dos confirmaciones y solo admite un `DATABASE_URL` PostgreSQL con host de loopback. Antes de ejecutarla, confirme que la base es local, que su esquema de conversaciones, tablas de `PostgresSaver` y embeddings autorizados existen, y que puede recibir escrituras de benchmark. Cada solicitud crea una conversación y checkpoint nuevos; no se eliminan datos automáticamente. Gemini consumirá cuota.

```powershell
poetry run python scripts/measure_agent_latency.py `
  --execute --confirm-local-agent-benchmark `
  --concurrency 1 --requests-per-worker 1
```

Use `--retrieval` para medir la ruta que solicita RAG y ejecútela como un escenario separado del caso sin RAG. El prompt se persiste por el contrato normal de `ConversationService`, pero el reporte JSON no contiene prompts, respuestas, documentos, URL de base de datos, identificadores de hilo ni credenciales.

## Resultados y criterio

Los dos instrumentos producen percentiles P50, P95 y P99, errores y throughput. Para el agente, la meta del tablero se evalúa exclusivamente sobre `latency_ms.p95` de una ejecución real completa: debe ser estrictamente menor que 2500 ms. Una prueba automatizada o una medición de `EchoStreamer` no puede demostrarla.

Conserve los reportes reales fuera del repositorio hasta confirmar que no contienen datos operativos. Registre junto a cada resultado solo la fecha, escenario, concurrencia, duración, versión de código, P50/P95/P99, throughput y porcentaje de errores.

## Diagnóstico de cuellos de botella

1. Compare primero los escenarios del agente con y sin `--retrieval`.
2. Si Langfuse está habilitado con credenciales no productivas autorizadas, use los spans de nodo y `gemini.generate` para separar la duración de Gemini de la recuperación agregada.
3. Para recuperación, el nodo RAG incluye embedding de consulta y pgvector; las estadísticas ya disponibles de PostgreSQL pueden complementar el diagnóstico sin cambiar el esquema.
4. Compare el tiempo de servicio total con las métricas de checkpoints y conversaciones disponibles en PostgreSQL local. No atribuya esa duración a Redis: Redis no participa en esta ruta actual.
5. Solo proponga una optimización después de conservar una medición real repetible que demuestre el cuello de botella. No cambie modelos, LangGraph, RAG, HITL ni el esquema de forma preventiva.

## Limitaciones

Los scripts no ejecutan carga contra producción, no habilitan Langfuse, no crean tablas ni migraciones y no limpian registros existentes. El instrumento WebSocket verifica transporte local; el instrumento del agente requiere autorización separada para Gemini y PostgreSQL local.
