from __future__ import annotations

import xmlrpc.client


def _format_odoo_fault(exc: xmlrpc.client.Fault) -> str:
    return str(getattr(exc, "faultString", None) or exc)

