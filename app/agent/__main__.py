"""Prepara de forma explicita la persistencia PostgreSQL del agente."""

from app.agent.checkpointer import setup_postgres_checkpointer

if __name__ == "__main__":
    setup_postgres_checkpointer()
    print("LangGraph checkpoints preparados.")
