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

La evaluación real requiere una autorización operativa, `DATABASE_URL` y una base local con
`knowledge_embeddings`. Ejecuta el nodo de recuperación y el nodo de razonamiento reales, sin
pasar por el Router, para garantizar que todos los casos midan RAG. Gemini es el proveedor
predeterminado de generación y Judge, pero ambos pueden usar los modelos configurados mediante
LiteLLM. Los embeddings continúan usando Gemini. Por tanto, las credenciales requeridas dependen
de los proveedores seleccionados; el Judge conserva su configuración sin fallback. Los chunks
recuperados se envían únicamente al proveedor configurado para cada generación o juicio.

```bash
poetry run python -m app.knowledge.evaluation \
  --live \
  --cases /ruta/autorizada/rag_cases.json \
  --output /ruta/autorizada/rag_results.json
```

El resultado guarda solo identificadores de chunks, configuración, hash del conjunto, métricas y
categorías de error; no guarda texto de documentos, prompts, respuestas ni secretos.

## Comparación de recuperación híbrida

La comparación real se solicita explícitamente con `--live --compare-retrieval`. Ejecuta las
variantes semántica, híbrida con RRF e híbrida con reranking con el mismo conjunto, configuración
de chunking y `retrieval_top_k`. El informe conserva métricas RAG existentes y añade, a nivel de
documento, Recall@K, Precision@K, MRR, NDCG@K y latencia media de recuperación.

```bash
poetry run python -m app.knowledge.evaluation \
  --live \
  --compare-retrieval \
  --cases /ruta/autorizada/rag_cases.json \
  --output /ruta/autorizada/rag_hybrid_results.json
```

Un documento se deriva del identificador de chunk quitando el último `:indice`; varios chunks del
mismo documento se cuentan una sola vez para las métricas de recuperación. Los chunks originales
no se alteran antes de la generación. Las abstenciones se informan separadamente y no participan
en estas métricas. No se deben versionar el corpus, resultados reales ni información personal.

La comparación híbrida real requiere que la migración FTS se haya aplicado satisfactoriamente.
El orden operativo es:

1. Inspeccionar el catálogo de PostgreSQL en modo solo lectura.
2. Verificar la compatibilidad de `knowledge_embeddings`, sus índices y la configuración `spanish`.
3. Obtener una autorización operativa independiente para modificar la base local.
4. Aplicar la migración de forma controlada, considerando el bloqueo y almacenamiento requeridos.
5. Verificar `search_vector`, el índice GIN y una consulta FTS parametrizada.
6. Ejecutar explícitamente la comparación con `--live --compare-retrieval`.

No se debe ejecutar la comparación híbrida ni afirmar resultados mientras esos pasos no hayan
concluido correctamente.

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
