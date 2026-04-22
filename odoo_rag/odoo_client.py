from __future__ import annotations

import xmlrpc.client
from typing import Any

from odoo_rag.config import Settings

# Equivalente seguro a dominio vacío []; evita errores Odoo 19 cuando el RPC arma `[[]]`
# (lista con una «cláusula» []) → ValueError: Domain() invalid item in domain: []
_DOMAIN_MATCH_ALL: list[Any] = [(1, "=", 1)]

_METHODS_DOMAIN_FIRST_ARG = frozenset({"search", "search_read", "search_count"})


def _normalize_rpc_domain(domain: Any) -> Any:
    if not isinstance(domain, list):
        return domain
    # Dominio vacío explícito
    if domain == []:
        return list(_DOMAIN_MATCH_ALL)
    # Dominio deforme típico: una sola cláusula vacía (equivale a mal anidar [[[]]])
    if domain == [[]]:
        return list(_DOMAIN_MATCH_ALL)
    # Cualquier elemento top-level [] es inválido como condición
    if domain and any(item == [] for item in domain):
        return list(_DOMAIN_MATCH_ALL)
    return domain


class OdooXmlRpc:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._common = xmlrpc.client.ServerProxy(settings.odoo_xmlrpc_common_url())
        self._models = xmlrpc.client.ServerProxy(settings.odoo_xmlrpc_object_url())
        self._uid: int | None = None

    @property
    def uid(self) -> int:
        if self._uid is None:
            uid = self._common.authenticate(
                self._settings.odoo_db,
                self._settings.odoo_username,
                self._settings.odoo_password,
                {},
            )
            if not uid:
                raise RuntimeError(
                    "Autenticación Odoo fallida: revisa ODOO_URL, ODOO_DB, ODOO_USERNAME y ODOO_PASSWORD."
                )
            self._uid = int(uid)
        return self._uid

    def execute_kw(
        self,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        kw = kwargs or {}
        args_out = list(args)
        if method in _METHODS_DOMAIN_FIRST_ARG and args_out:
            args_out[0] = _normalize_rpc_domain(args_out[0])
        return self._models.execute_kw(
            self._settings.odoo_db,
            self.uid,
            self._settings.odoo_password,
            model,
            method,
            args_out,
            kw,
        )

    def search_read(
        self,
        model: str,
        domain: list[Any],
        fields: list[str],
        *,
        limit: int,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        kw: dict[str, Any] = {"fields": fields, "limit": limit}
        if order:
            kw["order"] = order
        return self.execute_kw(model, "search_read", [domain], kw)
