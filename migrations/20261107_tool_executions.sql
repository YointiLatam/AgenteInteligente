BEGIN;

DO $$
BEGIN
    IF to_regclass('inmobiliaria_pruebas.tool_executions') IS NOT NULL THEN
        IF (
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = 'inmobiliaria_pruebas'
              AND table_name = 'tool_executions'
        ) <> 13 OR EXISTS (
            SELECT 1
            FROM (VALUES
                ('id', 'uuid'), ('thread_id', 'uuid'), ('turn_id', 'uuid'),
                ('call_id', 'text'), ('tool_name', 'text'), ('decision_id', 'text'),
                ('decision', 'text'), ('status', 'text'), ('approved_by', 'text'),
                ('result', 'jsonb'), ('created_at', 'timestamp with time zone'),
                ('updated_at', 'timestamp with time zone'),
                ('completed_at', 'timestamp with time zone')
            ) AS expected(column_name, data_type)
            LEFT JOIN information_schema.columns actual
              ON actual.table_schema = 'inmobiliaria_pruebas'
             AND actual.table_name = 'tool_executions'
             AND actual.column_name = expected.column_name
             AND actual.data_type = expected.data_type
            WHERE actual.column_name IS NULL
        ) THEN
            RAISE EXCEPTION 'tool_executions existe con columnas incompatibles';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'inmobiliaria_pruebas.tool_executions'::regclass
              AND contype = 'p'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'id'
                                    AND NOT attisdropped)]::smallint[]
        ) OR NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'inmobiliaria_pruebas.tool_executions'::regclass
              AND contype = 'f'
              AND confrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'thread_id'
                                    AND NOT attisdropped)]::smallint[]
        ) THEN
            RAISE EXCEPTION 'tool_executions no tiene las claves esperadas';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'inmobiliaria_pruebas.tool_executions'::regclass
              AND contype = 'u'
              AND conkey = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'thread_id' AND NOT attisdropped),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'call_id' AND NOT attisdropped)
              ]::smallint[]
        ) OR NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'inmobiliaria_pruebas.tool_executions'::regclass
              AND contype = 'u'
              AND conkey = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'thread_id' AND NOT attisdropped),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = conrelid AND attname = 'decision_id' AND NOT attisdropped)
              ]::smallint[]
        ) THEN
            RAISE EXCEPTION 'tool_executions no tiene la unicidad esperada';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.tool_executions'::regclass
              AND contype = 'c'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'decision'
                                    AND NOT attisdropped)]::smallint[]
              AND ARRAY(SELECT literal[1]
                        FROM regexp_matches(
                            pg_get_constraintdef(constraint_row.oid), '''([^'']*)''', 'g'
                        ) AS literal ORDER BY literal[1]) = ARRAY['approved', 'rejected']
        ) OR NOT EXISTS (
            SELECT 1 FROM pg_constraint constraint_row
            WHERE conrelid = 'inmobiliaria_pruebas.tool_executions'::regclass
              AND contype = 'c'
              AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                  WHERE attrelid = conrelid AND attname = 'status'
                                    AND NOT attisdropped)]::smallint[]
              AND ARRAY(SELECT literal[1]
                        FROM regexp_matches(
                            pg_get_constraintdef(constraint_row.oid), '''([^'']*)''', 'g'
                        ) AS literal ORDER BY literal[1]) =
                  ARRAY['completed', 'failed', 'processing']
        ) THEN
            RAISE EXCEPTION 'tool_executions no restringe decisiones y estados esperados';
        END IF;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS inmobiliaria_pruebas.tool_executions (
    id uuid PRIMARY KEY,
    thread_id uuid NOT NULL REFERENCES inmobiliaria_pruebas.conversation_threads(id),
    turn_id uuid NOT NULL,
    call_id text NOT NULL,
    tool_name text NOT NULL,
    decision_id text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    status text NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    approved_by text NOT NULL,
    result jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz,
    UNIQUE (thread_id, call_id),
    UNIQUE (thread_id, decision_id)
);

COMMIT;
