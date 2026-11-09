"""Validación determinista de entradas y salidas sin proveedores externos."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class GuardDecision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED_INPUT = "blocked_input"
    BLOCKED_OUTPUT = "blocked_output"


@dataclass(frozen=True)
class GuardResult:
    decision: GuardDecision
    category: str | None
    public_message: str | None
    policy_version: str


class ContentGuard(Protocol):
    def validate_input(self, content: str) -> GuardResult: ...

    def validate_output(self, content: str) -> GuardResult: ...


class DeterministicContentGuard:
    """Bloquea señales explícitas conocidas sin interpretar consultas legítimas."""

    POLICY_VERSION = "v1"
    MAX_INPUT_LENGTH = 12_000
    INPUT_BLOCKED_MESSAGE = "No puedo procesar esta solicitud."
    OUTPUT_BLOCKED_MESSAGE = "No puedo compartir información sensible."

    _INSTRUCTION_OVERRIDE = re.compile(
        r"\b(?:ignora|omite|anula|elude|bypassea|s[aá]ltate|ignore|disregard|bypass)\b"
        r".{0,80}\b(?:instrucciones?|reglas?|pol[ií]ticas?|directivas?|"
        r"previous|prior|system|developer)\b",
        re.IGNORECASE,
    )
    _INTERNAL_INSTRUCTION_EXTRACTION = re.compile(
        r"\b(?:muestra|revela|imprime|devuelve|extrae|dime|show|reveal|print|"
        r"extract)\b.{0,100}\b(?:prompt|instrucciones?|instructions?)\b.{0,60}"
        r"\b(?:sistema|system|desarrollador|developer|internas?|ocultas?|hidden|internal)\b",
        re.IGNORECASE,
    )
    _EDUCATIONAL_TEMPLATE = re.compile(
        r"\b(?:ejemplo|modelo|plantilla|example|template)\b", re.IGNORECASE
    )
    _DIRECT_INTERNAL_REFERENCE = re.compile(
        r"\b(?:tu|tus|propio(?:s|as)?|real(?:es)?|internas?|ocultas?|"
        r"que\s+(?:usas|utilizas)|de\s+(?:este|tu)\s+(?:agente|asistente)|"
        r"del\s+(?:agente|asistente)\s+actual)\b",
        re.IGNORECASE,
    )
    _PRIVILEGED_ROLE_IMPERSONATION = re.compile(
        r"\b(?:soy|act[uú]a como|simula|finge ser|hazte pasar por|i am|act as|"
        r"pretend to be)\b.{0,60}\b(?:system|sistema|developer|desarrollador|"
        r"admin(?:istrador)?)\b.{0,120}\b(?:ignora|omite|anula|revela|muestra|"
        r"ejecuta|autoriza|ignore|bypass|reveal|show|execute|authorize)\b",
        re.IGNORECASE,
    )
    _PRIVATE_KEY = re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE
    )
    _SECRET_ASSIGNMENT = re.compile(
        r"\b(?:gemini_api_key|auth_jwt_secret|database_url|neon_database_url|"
        r"whatsapp_access_token|meta_access_token|x_bearer_token)\b\s*[:=]\s*"
        r"(?!\[(?:redacted|oculto)\])\S+",
        re.IGNORECASE,
    )
    _CREDENTIALLED_CONNECTION_URI = re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s/:]+:[^@\s]+@",
        re.IGNORECASE,
    )
    _ACCESS_TOKEN = re.compile(r"\b(?:AIza[\w-]{20,}|ghp_[A-Za-z0-9]{20,}|sk-[\w-]{20,})\b")

    def validate_input(self, content: str) -> GuardResult:
        normalized = self._normalize(content)
        if len(content) > self.MAX_INPUT_LENGTH:
            return self._blocked_input("input_too_large")
        if self._has_disallowed_control_characters(content):
            return self._blocked_input("input_control_characters")
        if self._PRIVILEGED_ROLE_IMPERSONATION.search(normalized):
            return self._blocked_input("privileged_role_impersonation")
        if self._INSTRUCTION_OVERRIDE.search(normalized):
            return self._blocked_input("instruction_override")
        if self._INTERNAL_INSTRUCTION_EXTRACTION.search(
            normalized
        ) and not self._is_educational_template_request(normalized):
            return self._blocked_input("internal_instruction_extraction")
        return self._allowed()

    def validate_output(self, content: str) -> GuardResult:
        normalized = self._normalize(content)
        if self._PRIVATE_KEY.search(normalized):
            return self._blocked_output("private_key")
        if self._SECRET_ASSIGNMENT.search(normalized):
            return self._blocked_output("secret_assignment")
        if self._CREDENTIALLED_CONNECTION_URI.search(normalized):
            return self._blocked_output("credentialed_connection_uri")
        if self._ACCESS_TOKEN.search(normalized):
            return self._blocked_output("access_token")
        return self._allowed()

    @classmethod
    def _normalize(cls, content: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", content)).strip()

    @staticmethod
    def _has_disallowed_control_characters(content: str) -> bool:
        return any(
            unicodedata.category(character) == "Cc" and character not in "\n\r\t"
            for character in content
        )

    def _is_educational_template_request(self, content: str) -> bool:
        return bool(
            self._EDUCATIONAL_TEMPLATE.search(content)
            and not self._DIRECT_INTERNAL_REFERENCE.search(content)
        )

    def _allowed(self) -> GuardResult:
        return GuardResult(
            decision=GuardDecision.ALLOWED,
            category=None,
            public_message=None,
            policy_version=self.POLICY_VERSION,
        )

    def _blocked_input(self, category: str) -> GuardResult:
        return GuardResult(
            decision=GuardDecision.BLOCKED_INPUT,
            category=category,
            public_message=self.INPUT_BLOCKED_MESSAGE,
            policy_version=self.POLICY_VERSION,
        )

    def _blocked_output(self, category: str) -> GuardResult:
        return GuardResult(
            decision=GuardDecision.BLOCKED_OUTPUT,
            category=category,
            public_message=self.OUTPUT_BLOCKED_MESSAGE,
            policy_version=self.POLICY_VERSION,
        )
