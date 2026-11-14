from pydantic import BaseModel, Field

from app.integrations.clients import Provider, ProviderNotConfigured, build_client
from app.integrations.oauth import TokenHub, get_token_hub
from app.tools.base import BaseTool, ToolError
from app.tools.social import require_x_user_id


class _XTool(BaseTool):
    """Shared client wiring: uses the OAuth2 Tool Hub only when its credentials are set."""

    provider = Provider.X
    _hub: TokenHub | None = None  # tests may inject one

    def _hub_enabled(self) -> bool:
        s = self._settings_or_default()
        return bool(s.x_refresh_token and s.x_client_id and s.x_client_secret)

    def http_client(self):
        if not self._hub_enabled():
            return super().http_client()
        if self._hub is None:
            self._hub = TokenHub(self._settings) if self._settings else get_token_hub()
        try:
            return build_client(
                Provider.X, self._settings_or_default(), self.timeout, hub=self._hub
            )
        except ProviderNotConfigured as exc:
            raise ToolError("not_configured", str(exc)) from exc

    def _user_id(self) -> str:
        return self._settings_or_default().x_user_id or require_x_user_id()


class ReadRecentPostsInput(BaseModel):
    max_results: int = Field(default=10, ge=5, le=100, description="Cantidad de posts a leer")


class XReadRecentPosts(_XTool):
    name = "x_read_recent_posts"
    description = "Lee los posts más recientes de la cuenta de X configurada."
    input_model = ReadRecentPostsInput

    async def execute(self, args: ReadRecentPostsInput) -> dict:
        user_id = self._user_id()
        async with self.http_client() as client:
            response = await client.get(
                f"/users/{user_id}/tweets",
                params={"max_results": args.max_results, "tweet.fields": "created_at"},
            )
            response.raise_for_status()
        posts = [
            {"id": p.get("id"), "text": p.get("text"), "created_at": p.get("created_at")}
            for p in response.json().get("data", [])
        ]
        return {"posts": posts}


class PublishPostInput(BaseModel):
    text: str = Field(min_length=1, max_length=280, description="Texto del post (máx. 280)")


class XPublishPost(_XTool):
    name = "x_publish_post"
    description = "Publica un post en la cuenta de X configurada. Requiere aprobación."
    input_model = PublishPostInput
    critical = True
    retry_safe = False  # a retry after a possible send could publish twice

    async def execute(self, args: PublishPostInput) -> dict:
        async with self.http_client() as client:
            response = await client.post("/tweets", json={"text": args.text})
            response.raise_for_status()
        return {"post_id": response.json()["data"]["id"]}


TOOLS = [XReadRecentPosts, XPublishPost]
