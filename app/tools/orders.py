import psycopg
from psycopg.rows import dict_row
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrdersSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Usuario con permiso SELECT únicamente (ver db/init/003_orders.sql).
    orders_database_url: str = "postgresql://agente_ro:agente_ro@localhost:5432/agente"


class PgOrderRepository:
    """Lectura de pedidos con consulta parametrizada y transacción de solo lectura."""

    _QUERY = "SELECT order_number, status, total, updated_at FROM orders WHERE order_number = %s"

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get(self, order_number: str) -> dict | None:
        with psycopg.connect(
            self._database_url,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on",
            connect_timeout=5,
        ) as connection:
            return connection.execute(self._QUERY, (order_number,)).fetchone()
