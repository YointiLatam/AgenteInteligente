# Evaluación de precisión RAG

Esta guía define la ejecución explícita de la actividad 12. No contiene preguntas, respuestas,
documentos ni resultados: esos artefactos solo pueden añadirse tras revisar información real y
autorizada de la base de conocimiento.

## Preparación del conjunto

El archivo JSON de casos se mantiene fuera del código hasta que sus preguntas y referencias sean
verificadas contra documentos autorizados. Cada caso usa este contrato:

```json
{
  "id": "identificador-estable",
  "question": "Pregunta respaldada por documentos autorizados",
  "reference_answer": "Respuesta de referencia autorizada",
  "evidence_document_ids": ["documento:identificador"],
  "expects_no_answer": false
}
```

Las preguntas sin evidencia usan una lista de evidencia vacía y
`"expects_no_answer": true`. El conjunto debe incluir preguntas directas, de localización,
de combinación de evidencia y preguntas sin respuesta documental. No se versionan documentos
completos ni datos personales en el repositorio.

## Ejecución

Sin `--live`, el comando solo valida el conjunto y muestra su hash SHA-256; no construye clientes
ni se conecta a Gemini o PostgreSQL:

```bash
poetry run python -m app.knowledge.evaluation --cases /ruta/autorizada/rag_cases.json
```

La evaluación real requiere una autorización operativa, `DATABASE_URL`, `GEMINI_API_KEY` y una
base local con `knowledge_embeddings`. Ejecuta el nodo de recuperación y el nodo de razonamiento
reales, sin pasar por el Router, para garantizar que todos los casos midan RAG. Gemini actúa
también como juez; por ello los chunks recuperados se envían al proveedor igual que en la
generación RAG.

```bash
poetry run python -m app.knowledge.evaluation \
  --live \
  --cases /ruta/autorizada/rag_cases.json \
  --output /ruta/autorizada/rag_results.json
```

El resultado guarda solo identificadores de chunks, configuración, hash del conjunto, métricas y
categorías de error; no guarda texto de documentos, prompts, respuestas ni secretos.

## Métricas y aceptación

- **Fidelidad:** afirmaciones respaldadas / afirmaciones evaluables. Las abstenciones no aportan
  puntos a esta media.
- **Relevancia de respuesta:** `0`, `0.5` o `1`, conforme a la rúbrica del juez.
- **Precisión de contexto:** chunks relevantes / chunks recuperados. Sin chunks, la métrica es no
  evaluable; contexto recuperado pero irrelevante puntúa `0`.
- **Abstenciones:** se cuentan y reportan por separado para casos sin evidencia.

La configuración base es `chunk_size=800`, `chunk_overlap=120` y `retrieval_top_k=4`. El objetivo
se cumple únicamente con una fidelidad media estrictamente mayor que `0.90`. Si no se alcanza,
se ajusta una sola variable por ejecución: chunking, top-k o el prompt RAG. Los cambios se apoyan
en los resultados reales y no en ajustes preventivos.
