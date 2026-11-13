from collections.abc import AsyncIterator
from typing import Protocol


class TokenStreamer(Protocol):
    """Fuente de tokens del modelo. El cliente de Gemini la implementa cuando esté listo."""

    def stream(self, prompt: str) -> AsyncIterator[str]: ...


class EchoStreamer:
    """Sustituto sin modelo: devuelve el prompt palabra por palabra."""

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        for word in f"Echo: {prompt}".split():
            yield f"{word} "


def get_streamer() -> TokenStreamer:
    # Punto de enganche: reemplazar por el cliente real o usar app.dependency_overrides.
    return EchoStreamer()
