-- Actividad 15: índice léxico para recuperación híbrida.
--
-- Verificaciones obligatorias antes de ejecutar:
-- 1. Confirmar en el catálogo la estructura e índices de knowledge_embeddings.
-- 2. Confirmar que PostgreSQL es >= 12 y que existe la configuración FTS spanish.
-- 3. Planificar ventana de mantenimiento: una columna GENERATED STORED calcula valores
--    para las filas existentes y CREATE INDEX sin CONCURRENTLY puede tomar bloqueos.
-- 4. Validar después la búsqueda semántica existente con modelo_embedding y pgvector.
-- Esta migración no crea tablas ni modifica embeddings o contenido de chunks.

DO $$
BEGIN
    IF current_setting('server_version_num')::integer < 120000 THEN
        RAISE EXCEPTION 'PostgreSQL >= 12 es requerido para columnas generadas almacenadas';
    END IF;
    IF to_regclass('inmobiliaria_pruebas.knowledge_embeddings') IS NULL THEN
        RAISE EXCEPTION 'No existe inmobiliaria_pruebas.knowledge_embeddings';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_ts_config AS config
        JOIN pg_catalog.pg_namespace AS namespace
          ON namespace.oid = config.cfgnamespace
        WHERE config.cfgname = 'spanish'
          AND namespace.nspname = 'pg_catalog'
    ) THEN
        RAISE EXCEPTION 'La configuración FTS spanish no está disponible';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'inmobiliaria_pruebas'
          AND table_name = 'knowledge_embeddings'
          AND column_name = 'content_chunk'
          AND data_type = 'text'
    ) THEN
        RAISE EXCEPTION 'knowledge_embeddings.content_chunk debe ser text';
    END IF;
END
$$;

ALTER TABLE inmobiliaria_pruebas.knowledge_embeddings
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('spanish'::regconfig, coalesce(content_chunk, ''))
    ) STORED;

DO $$
DECLARE
    target_relation regclass := 'inmobiliaria_pruebas.knowledge_embeddings'::regclass;
    generated_expression text;
BEGIN
    SELECT pg_get_expr(definition.adbin, definition.adrelid, true)
    INTO generated_expression
    FROM pg_attribute AS attribute
    JOIN pg_type AS type ON type.oid = attribute.atttypid
    JOIN pg_attrdef AS definition
      ON definition.adrelid = attribute.attrelid
     AND definition.adnum = attribute.attnum
    WHERE attribute.attrelid = target_relation
      AND attribute.attname = 'search_vector'
      AND NOT attribute.attisdropped
      AND type.typname = 'tsvector'
      AND type.typnamespace = 'pg_catalog'::regnamespace
      AND attribute.attgenerated = 's';

    IF generated_expression IS NULL THEN
        RAISE EXCEPTION 'search_vector existente no es tsvector generado almacenado compatible';
    END IF;

    -- pg_get_expr produce una representación reconstruida y canónica del árbol almacenado.
    -- Normalizar solo espacios, comillas y el prefijo pg_catalog evita rechazar deparses
    -- equivalentes, aunque no permite demostrar equivalencia semántica arbitraria.
    IF lower(
        regexp_replace(
            replace(replace(generated_expression, 'pg_catalog.', ''), '"', ''),
            '\s+',
            '',
            'g'
        )
    ) <> 'to_tsvector(''spanish''::regconfig,coalesce(content_chunk,''''::text))' THEN
        RAISE EXCEPTION 'search_vector no usa la expresión FTS spanish compatible';
    END IF;

END
$$;

CREATE INDEX IF NOT EXISTS knowledge_embeddings_search_vector_gin_idx
    ON inmobiliaria_pruebas.knowledge_embeddings
    USING GIN (search_vector);

DO $$
DECLARE
    target_relation regclass := 'inmobiliaria_pruebas.knowledge_embeddings'::regclass;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class AS index_relation
        JOIN pg_namespace AS index_schema ON index_schema.oid = index_relation.relnamespace
        JOIN pg_index AS index_definition ON index_definition.indexrelid = index_relation.oid
        JOIN pg_am AS access_method ON access_method.oid = index_relation.relam
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = index_definition.indrelid
         AND attribute.attname = 'search_vector'
         AND NOT attribute.attisdropped
        JOIN LATERAL unnest(index_definition.indkey::smallint[])
             WITH ORDINALITY AS key_column(attnum, position)
          ON key_column.position = 1
        JOIN LATERAL unnest(index_definition.indclass::oid[])
             WITH ORDINALITY AS key_operator_class(opclass_oid, position)
          ON key_operator_class.position = 1
        JOIN pg_opclass AS operator_class ON operator_class.oid = key_operator_class.opclass_oid
        WHERE index_schema.nspname = 'inmobiliaria_pruebas'
          AND index_relation.relname = 'knowledge_embeddings_search_vector_gin_idx'
          AND index_definition.indrelid = target_relation
          AND index_definition.indisvalid
          AND index_definition.indisready
          AND access_method.amname = 'gin'
          AND index_definition.indnkeyatts = 1
          AND key_column.attnum = attribute.attnum
          AND index_definition.indexprs IS NULL
          AND index_definition.indpred IS NULL
          AND operator_class.opcname = 'tsvector_ops'
          AND operator_class.opcnamespace = 'pg_catalog'::regnamespace
    ) THEN
        RAISE EXCEPTION 'knowledge_embeddings_search_vector_gin_idx no es un índice GIN compatible';
    END IF;
END
$$;
