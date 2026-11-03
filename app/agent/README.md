# StateGraph inicial

El flujo inicial usa LangGraph sin proveedores externos:

```text
START -> Router -> RAG_Retrieval -> Reasoning -> END
                 \-> Reasoning ---------> END
```

`Router` elige RAG solo cuando `retrieval_requested` es verdadero. `RAG_Retrieval`
recibe un recuperador inyectable; el valor por defecto no consulta infraestructura y
devuelve una lista vacía. `Reasoning` produce una respuesta determinista hasta que se
incorpore un proveedor de modelos.

Toda invocación debe indicar `recursion_limit`. Las pruebas validan que los límites
usados completan tanto la ruta directa como la ruta con recuperación.
