FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /srv

RUN pip install --no-cache-dir "poetry>=2.2"

# Primero las dependencias para aprovechar la caché de capas
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root --no-interaction

COPY app ./app

# Usuario sin privilegios
RUN useradd --system --no-create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
