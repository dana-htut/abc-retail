from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    store_id = fields.Many2one(
        "retail.store",
        string="Assigned Store",
        help="Retail store branch this user belongs to.",
    )
