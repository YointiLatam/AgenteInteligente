-- Base transaccional de ejemplo para la herramienta de consulta de pedidos (solo desarrollo).
CREATE TABLE IF NOT EXISTS orders (
    order_number VARCHAR(40) PRIMARY KEY,
    status VARCHAR(30) NOT NULL,
    total NUMERIC(12, 2) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO orders (order_number, status, total) VALUES
    ('ORD-1001', 'processing', 59.90),
    ('ORD-1002', 'shipped', 120.00),
    ('ORD-1003', 'delivered', 15.50)
ON CONFLICT DO NOTHING;

-- Rol de solo lectura que usa el agente: aunque la herramienta fallara, no puede escribir.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agente_ro') THEN
        CREATE ROLE agente_ro LOGIN PASSWORD 'agente_ro';  -- solo desarrollo local
    END IF;
END
$$;
GRANT SELECT ON orders TO agente_ro;
