import asyncio
from typing import Protocol

from pydantic import BaseModel, Field

from app.tools.base import BaseTool, ToolError
from app.tools.orders import OrdersSettings, PgOrderRepository


class OrderRepository(Protocol):
    def get(self, order_number: str) -> dict | None: ...


class GetOrderStatusInput(BaseModel):
    order_number: str = Field(
        pattern=r"^[A-Za-z0-9-]{1,40}$", description="Número de pedido, por ejemplo ORD-1001"
    )


class GetOrderStatus(BaseTool):
    name = "get_order_status"
    description = "Consulta el estado actual de un pedido por su número. Solo lectura."
    input_model = GetOrderStatusInput

    def __init__(self, repository: OrderRepository | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._repository = repository

    def _repo(self) -> OrderRepository:
        if self._repository is None:
            self._repository = PgOrderRepository(OrdersSettings().orders_database_url)
        return self._repository

    async def execute(self, args: GetOrderStatusInput) -> dict:
        # psycopg es síncrono: se delega a un hilo para no bloquear el event loop
        order = await asyncio.to_thread(self._repo().get, args.order_number)
        if order is None:
            raise ToolError("not_found", f"No existe el pedido {args.order_number}")
        return {
            "order_number": order["order_number"],
            "status": order["status"],
            "total": str(order["total"]),
            "updated_at": order["updated_at"].isoformat(),
        }


TOOLS = [GetOrderStatus]
