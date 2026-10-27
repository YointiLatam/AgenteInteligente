# Agente Inteligente

Agente autónomo basado en la **Arquitectura 2** (LangGraph + PostgreSQL + integraciones).
Este repositorio contiene el API Gateway (FastAPI) y las herramientas de integración.

## Requisitos

- Python 3.12+ y [Poetry](https://python-poetry.org/) 2.x
- Docker y Docker Compose

## Desarrollo local

```bash
poetry install
poetry run uvicorn app.main:app --reload
poetry run pytest
poetry run ruff check .
```

## Con Docker

```bash
docker compose up --build
curl http://localhost:8000/health
```

## Convenciones

- Cada archivo de `app/api/routes/` que exponga un `router` se registra automáticamente.
- Commits en español con [Conventional Commits](https://www.conventionalcommits.org/es/).
- Pull requests pequeños (menos de 400 líneas) y encadenados.
