from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from odoo_rag.config import Settings as AppSettings
from odoo_rag.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise RuntimeError(f"Tool duplicada: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as ex:
            raise ValueError(f"Tool no registrada: {name}") from ex

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def execute(self, app: AppSettings, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        try:
            inp: BaseModel = tool.input_model.model_validate(payload)
        except ValidationError as ex:
            raise ValueError(f"Input inválido para tool '{name}': {ex}") from ex
        return tool.run(app, inp)  # type: ignore[arg-type]


_REGISTRY: ToolRegistry | None = None


def registry() -> ToolRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry()
    return _REGISTRY

