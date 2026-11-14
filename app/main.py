import importlib
import pkgutil

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    if settings.cors_allowed_origins:
        # Only the configured widget embedders; no wildcard and no cookies/credentials.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key"],
            max_age=settings.cors_max_age,
        )
    # Cada módulo de app/api/routes que exponga `router` se registra solo:
    # agregar un endpoint nuevo no obliga a editar este archivo.
    for module in pkgutil.iter_modules(routes.__path__):
        app.include_router(importlib.import_module(f"{routes.__name__}.{module.name}").router)
    return app


app = create_app()
