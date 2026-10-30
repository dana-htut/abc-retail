from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    store_stock_request_id = fields.Many2one(
        "store.stock.request",
        string="Origin Store Request",
        readonly=True,
        copy=False,
        help="The store stock request that triggered the creation of this purchase order.",
    )
