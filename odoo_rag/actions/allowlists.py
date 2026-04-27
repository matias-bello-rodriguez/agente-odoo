from __future__ import annotations

ALLOWED_CREATE_FIELDS: dict[str, frozenset[str]] = {
    "res.partner": frozenset(
        {
            "name",
            "email",
            "phone",
            "street",
            "city",
            "zip",
            "vat",
            "is_company",
            "comment",
        }
    ),
    "product.product": frozenset(
        {
            "name",
            "default_code",
            "list_price",
            "standard_price",
            "type",
        }
    ),
    "account.move": frozenset(
        {
            "move_kind",
            "partner_name",
            "invoice_line_name",
            "invoice_line_price_unit",
            "invoice_line_qty",
            "invoice_date",
            "invoice_date_due",
            "ref",
            "narration",
        }
    ),
    "sale.order": frozenset(
        {
            "partner_name",
            "order_line_name",
            "order_line_qty",
            "order_line_price_unit",
            "order_line_discount",
            "client_order_ref",
            "note",
        }
    ),
    "purchase.order": frozenset(
        {
            "vendor_name",
            "order_line_name",
            "order_line_qty",
            "order_line_price_unit",
            "partner_ref",
            "notes",
        }
    ),
    "stock.picking": frozenset(
        {
            "picking_type_code",
            "partner_name",
            "origin",
            "move_line_name",
            "product_name",
            "move_line_qty",
        }
    ),
}

ALLOWED_MODELS = frozenset(ALLOWED_CREATE_FIELDS.keys())

ALLOWED_LIST_QUERIES = frozenset(
    {
        "delivery_orders",
        "users_roles",
        "accounting_recent_actions",
        "accounting_missing_key_data",
        "users_last_login",
        "dirty_data_overview",
        "invoice_from_order_check",
        "overdue_invoices",
        "low_stock_products",
        "best_vendor_for_product",
        "payroll_preview",
        "dashboard_overview",
        "latest_product",
        "sales_quarter_compare",
        "customers_drop_with_active_contracts",
        "demand_forecast_purchase_hints",
        "sales_last_month_total",
        "issued_invoices_month_total",
    }
)

ALLOWED_EMAIL_TARGETS = frozenset({"partner", "invoice", "sale_order", "purchase_order"})
ALLOWED_WORKFLOWS = frozenset({"lead_to_payment"})

