import importlib
import pkgutil

from fastapi import FastAPI

from app.api import routes
from app.core.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name)
    # Cada módulo de app/api/routes que exponga `router` se registra solo:
    # agregar un endpoint nuevo no obliga a editar este archivo.
    for module in pkgutil.iter_modules(routes.__path__):
        app.include_router(importlib.import_module(f"{routes.__name__}.{module.name}").router)
    return app


app = create_app()
