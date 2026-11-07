BEGIN;

DO $$
BEGIN
    IF to_regclass('inmobiliaria_pruebas.conversation_threads') IS NOT NULL
       AND ((SELECT count(*) FROM information_schema.columns
             WHERE table_schema = 'inmobiliaria_pruebas' AND table_name = 'conversation_threads') <> 7
            OR EXISTS (
                SELECT 1 FROM (VALUES
                    ('id', 'uuid'), ('channel', 'text'), ('external_conversation_id', 'text'),
                    ('langgraph_thread_id', 'text'), ('status', 'text'),
                    ('created_at', 'timestamp with time zone'), ('updated_at', 'timestamp with time zone')
                ) AS expected(column_name, data_type)
                LEFT JOIN information_schema.columns actual
                  ON actual.table_schema = 'inmobiliaria_pruebas'
                 AND actual.table_name = 'conversation_threads'
                 AND actual.column_name = expected.column_name
                 AND actual.data_type = expected.data_type
                WHERE actual.column_name IS NULL
            )) THEN
        RAISE EXCEPTION 'conversation_threads existe con columnas incompatibles';
    END IF;
    IF to_regclass('inmobiliaria_pruebas.messages') IS NOT NULL
       AND ((SELECT count(*) FROM information_schema.columns
             WHERE table_schema = 'inmobiliaria_pruebas' AND table_name = 'messages') <> 9
            OR EXISTS (
                SELECT 1 FROM (VALUES
                    ('id', 'uuid'), ('thread_id', 'uuid'), ('turn_id', 'uuid'), ('role', 'text'),
                    ('content', 'text'), ('raw_payload', 'jsonb'), ('idempotency_key', 'text'),
                    ('created_at', 'timestamp with time zone'), ('updated_at', 'timestamp with time zone')
                ) AS expected(column_name, data_type)
                LEFT JOIN information_schema.columns actual
                  ON actual.table_schema = 'inmobiliaria_pruebas'
                 AND actual.table_name = 'messages'
                 AND actual.column_name = expected.column_name
                 AND actual.data_type = expected.data_type
                WHERE actual.column_name IS NULL
            )) THEN
        RAISE EXCEPTION 'messages existe con columnas incompatibles';
    END IF;

    IF to_regclass('inmobiliaria_pruebas.conversation_threads') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM (VALUES
               ('id', 'NO', ''), ('channel', 'NO', ''),
               ('external_conversation_id', 'NO', ''), ('langgraph_thread_id', 'NO', ''),
               ('status', 'NO', '''active''::text'),
               ('created_at', 'NO', 'CURRENT_TIMESTAMP'),
               ('updated_at', 'NO', 'CURRENT_TIMESTAMP')
           ) AS expected(column_name, is_nullable, column_default)
           LEFT JOIN information_schema.columns actual
             ON actual.table_schema = 'inmobiliaria_pruebas'
            AND actual.table_name = 'conversation_threads'
            AND actual.column_name = expected.column_name
           WHERE actual.column_name IS NULL
              OR actual.is_nullable <> expected.is_nullable
              OR COALESCE(actual.column_default, '') <> expected.column_default
       ) THEN
        RAISE EXCEPTION 'conversation_threads existe con nulabilidad o defaults incompatibles';
    END IF;

    IF to_regclass('inmobiliaria_pruebas.messages') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM (VALUES
               ('id', 'NO', ''), ('thread_id', 'NO', ''), ('turn_id', 'NO', ''),
               ('role', 'NO', ''), ('content', 'NO', ''),
               ('raw_payload', 'NO', '''{}''::jsonb'), ('idempotency_key', 'NO', ''),
               ('created_at', 'NO', 'CURRENT_TIMESTAMP'), ('updated_at', 'NO', 'CURRENT_TIMESTAMP')
           ) AS expected(column_name, is_nullable, column_default)
           LEFT JOIN information_schema.columns actual
             ON actual.table_schema = 'inmobiliaria_pruebas'
            AND actual.table_name = 'messages'
            AND actual.column_name = expected.column_name
           WHERE actual.column_name IS NULL
              OR actual.is_nullable <> expected.is_nullable
              OR COALESCE(actual.column_default, '') <> expected.column_default
       ) THEN
        RAISE EXCEPTION 'messages existe con nulabilidad o defaults incompatibles';
    END IF;

    IF to_regclass('inmobiliaria_pruebas.conversation_threads') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
              AND contype = 'p'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'id'
                                    AND NOT attisdropped)]::smallint[]
        ) THEN
            RAISE EXCEPTION 'conversation_threads no tiene la clave primaria esperada';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
              AND contype = 'u'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'langgraph_thread_id'
                                    AND NOT attisdropped)]::smallint[]
        ) THEN
            RAISE EXCEPTION 'conversation_threads no tiene unicidad en langgraph_thread_id';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
              AND contype = 'u'
              AND conkey = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'channel' AND NOT attisdropped),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'external_conversation_id'
                     AND NOT attisdropped)
              ]::smallint[]
        ) THEN
            RAISE EXCEPTION 'conversation_threads no tiene unicidad por canal y conversación externa';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
              AND contype = 'c'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'channel'
                                    AND NOT attisdropped)]::smallint[]
              AND ARRAY(SELECT literal[1]
                        FROM regexp_matches(
                            pg_get_constraintdef(oid), '''([^'']*)''', 'g'
                        ) AS literal
                        ORDER BY literal[1]) = ARRAY['meta', 'web_widget', 'whatsapp']
        ) THEN
            RAISE EXCEPTION 'conversation_threads no restringe los canales esperados';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
              AND contype = 'c'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'status'
                                    AND NOT attisdropped)]::smallint[]
              AND ARRAY(SELECT literal[1]
                        FROM regexp_matches(
                            pg_get_constraintdef(oid), '''([^'']*)''', 'g'
                        ) AS literal
                        ORDER BY literal[1]) = ARRAY['active', 'closed']
        ) THEN
            RAISE EXCEPTION 'conversation_threads no restringe los estados esperados';
        END IF;
    END IF;

    IF to_regclass('inmobiliaria_pruebas.messages') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.messages'::regclass
              AND contype = 'p'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'id'
                                    AND NOT attisdropped)]::smallint[]
        ) THEN
            RAISE EXCEPTION 'messages no tiene la clave primaria esperada';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.messages'::regclass
              AND contype = 'f'
              AND confrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'thread_id'
                                    AND NOT attisdropped)]::smallint[]
              AND confkey = ARRAY[(SELECT attnum FROM pg_attribute
                                   WHERE attrelid = confrelid AND attname = 'id'
                                     AND NOT attisdropped)]::smallint[]
              AND confdeltype = 'a' AND confupdtype = 'a'
        ) THEN
            RAISE EXCEPTION 'messages no tiene la clave foránea thread_id esperada';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.messages'::regclass
              AND contype = 'u'
              AND conkey = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'thread_id' AND NOT attisdropped),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'idempotency_key'
                     AND NOT attisdropped)
              ]::smallint[]
        ) THEN
            RAISE EXCEPTION 'messages no tiene unicidad por hilo e idempotency_key';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.messages'::regclass
              AND contype = 'c'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'role'
                                    AND NOT attisdropped)]::smallint[]
              AND ARRAY(SELECT literal[1]
                        FROM regexp_matches(
                            pg_get_constraintdef(oid), '''([^'']*)''', 'g'
                        ) AS literal
                        ORDER BY literal[1]) = ARRAY['agent', 'human_operator', 'system', 'user']
        ) THEN
            RAISE EXCEPTION 'messages no restringe los roles esperados';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_index index_row
            JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
            JOIN pg_am access_method ON access_method.oid = index_class.relam
            WHERE index_class.oid = 'inmobiliaria_pruebas.messages_thread_created_idx'::regclass
              AND index_row.indrelid = 'inmobiliaria_pruebas.messages'::regclass
              AND access_method.amname = 'btree'
              AND NOT index_row.indisunique
              AND index_row.indnkeyatts = 3 AND index_row.indnatts = 3
              AND ARRAY(SELECT key_column.attnum::smallint
                        FROM unnest(index_row.indkey) WITH ORDINALITY
                             AS key_column(attnum, position)
                        ORDER BY key_column.position) = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = index_row.indrelid AND attname = 'thread_id'),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = index_row.indrelid AND attname = 'created_at'),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = index_row.indrelid AND attname = 'id')
              ]::smallint[]
              AND ARRAY(SELECT option_value::smallint
                        FROM unnest(index_row.indoption) AS option_value) =
                  ARRAY[0, 0, 0]::smallint[]
        ) THEN
            RAISE EXCEPTION 'messages_thread_created_idx no tiene la definición esperada';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_index index_row
            JOIN pg_class index_class ON index_class.oid = index_row.indexrelid
            JOIN pg_am access_method ON access_method.oid = index_class.relam
            WHERE index_class.oid = 'inmobiliaria_pruebas.messages_thread_turn_idx'::regclass
              AND index_row.indrelid = 'inmobiliaria_pruebas.messages'::regclass
              AND access_method.amname = 'btree'
              AND NOT index_row.indisunique
              AND index_row.indnkeyatts = 2 AND index_row.indnatts = 2
              AND ARRAY(SELECT key_column.attnum::smallint
                        FROM unnest(index_row.indkey) WITH ORDINALITY
                             AS key_column(attnum, position)
                        ORDER BY key_column.position) = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = index_row.indrelid AND attname = 'thread_id'),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = index_row.indrelid AND attname = 'turn_id')
              ]::smallint[]
              AND ARRAY(SELECT option_value::smallint
                        FROM unnest(index_row.indoption) AS option_value) =
                  ARRAY[0, 0]::smallint[]
        ) THEN
            RAISE EXCEPTION 'messages_thread_turn_idx no tiene la definición esperada';
        END IF;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS inmobiliaria_pruebas.conversation_threads (
    id uuid PRIMARY KEY,
    channel text NOT NULL CHECK (channel IN ('web_widget', 'whatsapp', 'meta')),
    external_conversation_id text NOT NULL,
    langgraph_thread_id text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (channel, external_conversation_id)
);

CREATE TABLE IF NOT EXISTS inmobiliaria_pruebas.messages (
    id uuid PRIMARY KEY,
    thread_id uuid NOT NULL REFERENCES inmobiliaria_pruebas.conversation_threads(id),
    turn_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('user', 'agent', 'system', 'human_operator')),
    content text NOT NULL,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (thread_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS messages_thread_created_idx
    ON inmobiliaria_pruebas.messages (thread_id, created_at, id);
CREATE INDEX IF NOT EXISTS messages_thread_turn_idx
    ON inmobiliaria_pruebas.messages (thread_id, turn_id);

COMMIT;
