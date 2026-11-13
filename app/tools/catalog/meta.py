from pydantic import BaseModel, Field

from app.integrations.clients import Provider
from app.tools.base import BaseTool
from app.tools.social import require_page_id


class ReadPostsInput(BaseModel):
    limit: int = Field(default=5, ge=1, le=25, description="Cantidad de publicaciones a leer")


class MetaReadPagePosts(BaseTool):
    name = "meta_read_page_posts"
    description = "Lee las últimas publicaciones de la página de Facebook configurada."
    input_model = ReadPostsInput
    provider = Provider.META

    async def execute(self, args: ReadPostsInput) -> dict:
        page_id = require_page_id()
        async with self.http_client() as client:
            response = await client.get(
                f"/{page_id}/posts",
                params={"fields": "id,message,created_time", "limit": args.limit},
            )
            response.raise_for_status()
        return {"posts": response.json().get("data", [])}


class PublishPostInput(BaseModel):
    message: str = Field(min_length=1, max_length=5000, description="Texto de la publicación")


class MetaPublishPagePost(BaseTool):
    name = "meta_publish_page_post"
    description = "Publica un mensaje en la página de Facebook configurada. Requiere aprobación."
    input_model = PublishPostInput
    provider = Provider.META
    critical = True

    async def execute(self, args: PublishPostInput) -> dict:
        page_id = require_page_id()
        async with self.http_client() as client:
            response = await client.post(f"/{page_id}/feed", data={"message": args.message})
            response.raise_for_status()
        return {"post_id": response.json()["id"]}


TOOLS = [MetaReadPagePosts, MetaPublishPagePost]
