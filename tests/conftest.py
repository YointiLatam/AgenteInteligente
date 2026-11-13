import pytest


@pytest.fixture
def anyio_backend() -> str:
    # Los tests async usan el plugin de anyio (ya viene con FastAPI) y solo asyncio.
    return "asyncio"
