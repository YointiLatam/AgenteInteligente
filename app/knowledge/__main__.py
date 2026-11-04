"""Ejecuta explícitamente la ingesta local."""

from app.knowledge.ingestion import run_ingestion

if __name__ == "__main__":
    result = run_ingestion()
    print(f"documents={result.documents} chunks={result.chunks} stored={result.stored}")
