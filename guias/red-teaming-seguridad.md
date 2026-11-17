# Red teaming de seguridad

## Alcance

Esta guía documenta pruebas reproducibles de la Actividad 16. Todas usan mocks, datos
ficticios y `InMemorySaver`; no llaman a Gemini, LiteLLM, Langfuse, PostgreSQL ni APIs
externas. Superar estos casos no demuestra seguridad absoluta ni resistencia de un LLM
real frente a instrucciones adversarias.

## Matriz y estado

| ID | Superficie | Evidencia automatizada | Estado |
| --- | --- | --- | --- |
| PI-01 | Anulación explícita de instrucciones | `test_red_team_input_attacks_are_blocked_before_business_effects` | Control verificado |
| PI-02 | Solicitudes educativas legítimas | `test_red_team_educational_requests_remain_allowed` | Control verificado |
| PI-03 | Chunk RAG malicioso | `test_pi_03_reasoning_marks_rag_chunks_as_untrusted_data` | Protección estructural verificada |
| PI-04 | Clasificación Router manipulada | `test_pi_04_router_contract_limits_an_injected_classifier_response` | Degradación acotada verificada |
| PI-05 | Memoria conversacional | `test_pi_05_memory_prompt_currently_treats_history_as_plain_text` | Riesgo potencial documentado |
| JB-01 | Rol privilegiado simulado | prueba parametrizada PI/JB | Control verificado |
| JB-02 | Bypass multilingüe explícito | prueba parametrizada PI/JB | Control verificado |
| DF-01 | Secretos estructurados en salida | `test_df_01_guard_blocks_structured_secrets_from_public_output` | Control verificado |
| DF-02 | Payload de negocio tras bloqueo | `test_df_02_blocked_output_drops_generation_payload_from_business_message` | Control verificado |
| DF-03 | Metadatos de telemetría | `test_df_03_and_df_04_are_covered_by_the_safe_telemetry_contract` | Control verificado |
| DF-04 | Aislamiento documental | inspección del código de recuperación; no tiene prueba automatizada de aislamiento | Limitación arquitectónica conocida |
| HT-01 | Herramienta crítica antes de aprobar | `test_ht_01_critical_handler_does_not_run_before_checkpoint_approval` | Control verificado |
| HT-02 | Rechazo | `test_ht_02_rejection_through_service_completes_without_running_handler` | Control verificado |
| HT-03 | Reanudación repetida | `test_ht_03_service_approval_executes_once_and_rejects_incompatible_replay` | Control verificado |
| HT-04 | Cruce de thread, turno o llamada | `test_ht_04_checkpoint_rejects_crossed_turn_or_call_before_execution` | Control de checkpoint verificado; política de propiedad pendiente |
| DB-01 | SQL injection en pgvector | `test_db_queries_keep_malicious_values_as_parameters` | Control verificado |
| DB-02 | SQL injection en FTS | `test_db_queries_keep_malicious_values_as_parameters` | Control verificado |
| DB-03 | Error PostgreSQL | `test_db_03_retrieval_hides_database_error_details` | Control verificado |
| RS-01 | Error recuperable / máximo dos intentos | `test_gemini.py` y `test_rs_01_and_rs_02_existing_fallback_contract_is_preserved` | Control verificado |
| RS-02 | Bloqueo de seguridad y fallback | `test_gemini.py` y `test_rs_01_and_rs_02_existing_fallback_contract_is_preserved` | Control verificado |
| RS-03 | Fallos de recuperación y Judge sin fallback | `test_rs_03_retrieval_degrades_when_an_embedding_or_database_branch_fails`, `test_judge_task_never_uses_reasoning_fallback` | Control verificado |

## Hallazgos

### RT-01 — Chat WebSocket no atraviesa ContentGuard

- Clasificación: limitación arquitectónica conocida.
- Componente: `app/api/routes/chat.py`.
- Severidad: media si el endpoint deja de ser un eco y se conecta al agente sin integrar
  `ConversationService`.
- Evidencia: la ruta usa `TokenStreamer`; el streamer predeterminado es `EchoStreamer`.
- Corrección propuesta: si el WebSocket pasa a invocar el agente, usar el mismo flujo de
  `ConversationService` antes de llegar al grafo.
- Estado: fuera del alcance de esta actividad.

### RT-02 — Checkpoints pueden retener salida bloqueada

- Clasificación: limitación arquitectónica conocida.
- Componente: `ConversationService` y `PostgresSaver`.
- Severidad: media; la respuesta pública y el mensaje de negocio se limpian, pero el checkpoint
  se crea antes de aplicar ContentGuard a la salida.
- Evidencia: DF-02 verifica la limpieza de persistencia de negocio; no afirma saneamiento del
  checkpoint.
- Corrección propuesta: definir una política de retención y redacción de checkpoints antes de
  alterar PostgresSaver.
- Estado: requiere decisión arquitectónica.

### RT-03 — Sin autorización documental en RAG

- Clasificación: limitación arquitectónica conocida.
- Componente: `PostgresVectorRetriever` y `PostgresHybridRetriever`.
- Severidad: alta cuando existan documentos con diferentes permisos.
- Evidencia: las consultas filtran por `modelo_embedding`, no por identidad de usuario o permiso.
- Corrección propuesta: diseñar autorización documental separada; no se simula aislamiento que
  el esquema actual no implementa.
- Estado: fuera del alcance de guardrails.

### RT-04 — Herramientas de catálogo y registro HITL separados

- Clasificación: limitación arquitectónica conocida.
- Componente: `app/conversations/wiring.py`, `app.agent.tools`, `app.tools`.
- Severidad: media; hoy no existen herramientas reales conectadas al grafo de conversación.
- Evidencia: HT-01 a HT-03 usan exclusivamente herramientas simuladas.
- Corrección propuesta: definir un adaptador y revisión de seguridad específica antes de enlazar
  ambos registros.
- Estado: no se modifica en esta actividad.

### RT-05 — Memoria y Router sin delimitación equivalente a Reasoning

- Clasificación: riesgo potencial reproducible.
- Componente: `app/agent/nodes/memory.py` y `app/agent/nodes/router.py`.
- Severidad: media; PI-05 confirma que el contenido histórico llega como texto plano al modelo.
- Evidencia: los casos no prueban obediencia de un LLM real, solo ausencia de delimitación
  estructural en esos prompts.
- Corrección propuesta: aprobar primero un endurecimiento mínimo y uniforme de prompts.
- Estado: no corregido automáticamente porque afecta prompts de generación.

### RT-06 — Política de aprobación sin pertenencia del thread

- Clasificación: riesgo potencial de política.
- Componente: `app/chat/approvals.py`.
- Severidad: depende de si operadores/admin deben poder aprobar cualquier conversación.
- Evidencia: la identidad se autentica y su rol se valida, pero la solicitud de decisión no lleva
  ni comprueba un propietario de conversación.
- Corrección propuesta: definir una política de alcance para aprobadores antes de añadir controles.
- Estado: pendiente de decisión.

## Ejecución y evidencia

```powershell
poetry run pytest -q
poetry run ruff check .
git diff --check
```

Los hallazgos deben mantenerse con identificador, componente, evidencia sanitizada, severidad,
corrección propuesta y estado. Las pruebas reales contra proveedores, documentos autorizados o
PostgreSQL requieren autorización independiente.
