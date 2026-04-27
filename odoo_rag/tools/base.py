from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from odoo_rag.config import Settings as AppSettings

InputT = TypeVar("InputT", bound=BaseModel)


@dataclass(frozen=True)
class Tool(Generic[InputT]):
    """Definición de una herramienta ejecutable con input validado (Pydantic)."""

    name: str
    description: str
    input_model: type[InputT]

    def run(self, app: AppSettings, inp: InputT) -> dict[str, Any]:
        raise NotImplementedError

