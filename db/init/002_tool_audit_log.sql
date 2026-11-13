-- Auditoría de ejecuciones de herramientas del agente.
CREATE TABLE IF NOT EXISTS tool_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Sin FK por ahora: conversation_threads y users aún no existen en este esquema.
    -- Cuando lleguen, se agrega la restricción en una migración.
    thread_id UUID,
    tool_name VARCHAR(100) NOT NULL,
    input_parameters JSONB NOT NULL,
    output_result JSONB,
    status VARCHAR(30) NOT NULL
        CHECK (status IN ('pending_approval', 'executed', 'failed', 'rejected')),
    approved_by UUID,
    execution_time_ms INTEGER CHECK (execution_time_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tool_audit_log_thread_id ON tool_audit_log (thread_id);
CREATE INDEX IF NOT EXISTS idx_tool_audit_log_tool_created
    ON tool_audit_log (tool_name, created_at DESC);
