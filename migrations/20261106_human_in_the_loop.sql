BEGIN;

DO $$
DECLARE
    status_constraint_name text;
    status_constraint_count integer;
    allowed_statuses text[];
BEGIN
    IF to_regclass('inmobiliaria_pruebas.conversation_threads') IS NULL THEN
        RAISE EXCEPTION 'conversation_threads debe existir antes de habilitar HITL';
    END IF;

    SELECT count(*)
    INTO status_constraint_count
    FROM pg_constraint constraint_row
    WHERE conrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
      AND contype = 'c'
      AND conkey = ARRAY[(SELECT attnum
                          FROM pg_attribute
                          WHERE attrelid = constraint_row.conrelid
                            AND attname = 'status'
                            AND NOT attisdropped)]::smallint[];

    IF status_constraint_count <> 1 THEN
        RAISE EXCEPTION 'conversation_threads.status no tiene un único constraint verificable';
    END IF;

    SELECT constraint_row.conname,
           ARRAY(SELECT literal[1]
                 FROM regexp_matches(
                     pg_get_constraintdef(constraint_row.oid), '''([^'']*)''', 'g'
                 ) AS literal
                 ORDER BY literal[1])
    INTO status_constraint_name, allowed_statuses
    FROM pg_constraint constraint_row
    WHERE conrelid = 'inmobiliaria_pruebas.conversation_threads'::regclass
      AND contype = 'c'
      AND conkey = ARRAY[(SELECT attnum
                          FROM pg_attribute
                          WHERE attrelid = constraint_row.conrelid
                            AND attname = 'status'
                            AND NOT attisdropped)]::smallint[];

    IF allowed_statuses = ARRAY['active', 'closed', 'paused_hil'] THEN
        RETURN;
    END IF;
    IF allowed_statuses <> ARRAY['active', 'closed'] THEN
        RAISE EXCEPTION 'conversation_threads.status tiene valores inesperados: %', allowed_statuses;
    END IF;

    EXECUTE format(
        'ALTER TABLE inmobiliaria_pruebas.conversation_threads DROP CONSTRAINT %I',
        status_constraint_name
    );
    ALTER TABLE inmobiliaria_pruebas.conversation_threads
        ADD CONSTRAINT conversation_threads_status_check
        CHECK (status IN ('active', 'paused_hil', 'closed'));
END $$;

COMMIT;
