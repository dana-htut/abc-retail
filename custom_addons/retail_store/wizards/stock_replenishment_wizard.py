import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockReplenishmentWizard(models.TransientModel):
    _name = "stock.replenishment.wizard"
    _description = "Batch Stock Replenishment Wizard"

    store_id = fields.Many2one(
        "retail.store",
        string="Target Store",
        help="Leave empty to scan reorder rules across all stores.",
    )
    destination_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Supply Warehouse",
        required=True,
    )
    line_ids = fields.One2many(
        "stock.replenishment.wizard.line",
        "wizard_id",
        string="Replenishment Lines",
    )

    @api.model
    def default_get(self, fields_list):
        """Autopopulates Central Warehouse and low-stock lines on initial load."""
        res = super().default_get(fields_list)

        # 1. Autopopulate Central Warehouse (CDC)
        cw_warehouse = self.env["stock.warehouse"].search(
            [("code", "=", "CDC")], limit=1
        ) or self.env["stock.warehouse"].search([], limit=1)

        if cw_warehouse:
            res["destination_warehouse_id"] = cw_warehouse.id

        # 2. Fetch all low stock rules initially
        # res["line_ids"] = self._get_replenishment_lines(store=False)
        return res

    def _get_replenishment_lines(self, store=False):
        """Helper method to construct line commands safely."""
        domain = []
        if store:
            domain.append(("store_id", "=", store.id))

        rules = self.env["store.reorder.rule"].search(domain)
        rules._compute_current_qty()
        rules._compute_stock_status()

        # Catch rules requiring reorder
        low_rules = rules.filtered(lambda r: r.state in ["low", "reorder"])

        lines = []
        for rule in low_rules:
            pending_qty = rule._get_pending_qty()
            effective_stock = rule.current_qty + pending_qty
            suggested_qty = max(0.0, rule.max_qty - effective_stock)

            _logger.info(
                "RULE: %s | STATE: %s | STORE: %s | SUGGESTED: %s",
                rule.display_name,
                rule.state,
                rule.store_id,
                suggested_qty,
            )

            if suggested_qty > 0 and rule.store_id:
                lines.append(
                    (
                        0,
                        0,
                        {
                            "rule_id": rule.id,
                            "store_id": rule.store_id.id,
                            "product_id": rule.product_id.id,
                            "min_qty": rule.min_qty,
                            "max_qty": rule.max_qty,
                            "current_qty": rule.current_qty,
                            "pending_qty": pending_qty,
                            "suggested_qty": suggested_qty,
                            "qty_to_request": suggested_qty,
                        },
                    )
                )
        return lines

    def action_populate_lines(self):
        """Manual action to refresh list view based on Target Store selection."""
        self.ensure_one()
        # Cleanly wipe old lines and replace with fresh matching lines
        self.write(
            {
                "line_ids": [(5, 0, 0)]
                + self._get_replenishment_lines(store=self.store_id)
            }
        )

        # Keep wizard open
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.replenishment.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_generate_requests(self):
        """Groups wizard lines by store and generates Draft Stock Requests."""
        self.ensure_one()
        _logger.info("--- WIZARD LINE COUNT: %s ---", len(self.line_ids))
        valid_lines = self.line_ids.filtered(lambda l: l.qty_to_request > 0)
        _logger.info("--- VALID LINES (qty > 0): %s ---", len(valid_lines))
        if not valid_lines:
            raise UserError(_("No lines with positive requested quantity to process."))

        stores = valid_lines.mapped("store_id")
        created_requests = self.env["store.stock.request"]

        for store in stores:
            store_lines = valid_lines.filtered(lambda l: l.store_id == store)

            existing_draft = self.env["store.stock.request"].search(
                [
                    ("store_id", "=", store.id),
                    ("state", "=", "draft"),
                ],
                limit=1,
            )

            if existing_draft:
                for line in store_lines:
                    existing_line = existing_draft.line_ids.filtered(
                        lambda l: l.product_id == line.product_id
                    )
                    if existing_line:
                        existing_line.write({"qty_requested": line.qty_to_request})
                    else:
                        existing_draft.write(
                            {
                                "line_ids": [
                                    (
                                        0,
                                        0,
                                        {
                                            "product_id": line.product_id.id,
                                            "qty_requested": line.qty_to_request,
                                        },
                                    )
                                ]
                            }
                        )
                target_req = existing_draft
            else:
                request_lines = [
                    (
                        0,
                        0,
                        {
                            "product_id": line.product_id.id,
                            "qty_requested": line.qty_to_request,
                        },
                    )
                    for line in store_lines
                ]

                target_req = self.env["store.stock.request"].create(
                    {
                        "store_id": store.id,
                        "destination_warehouse_id": self.destination_warehouse_id.id,
                        "request_date": fields.Date.context_today(self),
                        "state": "draft",
                        "line_ids": request_lines,
                    }
                )

            target_req.message_post(
                body=_(
                    "Draft request created/updated via Batch Replenishment Wizard by %s."
                )
                % self.env.user.name
            )
            created_requests |= target_req

        # Load action directly from XML to retain full view definitions
        action = self.env["ir.actions.actions"]._for_xml_id(
            "retail_store.action_store_stock_request"
        )

        if len(created_requests) == 1:
            action.update(
                {
                    "views": [(False, "form")],
                    "res_id": created_requests.id,
                    "target": "current",
                }
            )
        else:
            action.update(
                {
                    "domain": [("id", "in", created_requests.ids)],
                    "target": "current",
                }
            )

        return action

    @api.model
    def run_automated_replenishment(self):
        """Cron job method to automatically scan reorder rules and create draft requests."""
        _logger.info("--- STARTING AUTOMATED NIGHTLY REPLENISHMENT CRON---")

        cw_warehouse = self.env["stock.warehouse"].search(
            [("code", "=", "CDC")], limit=1
        ) or self.env["stock.warehouse"].search([], limit=1)

        if not cw_warehouse:
            _logger.warning(
                "Automated Replenishment Aborted: No destination warehouse (CDC) found."
            )
            return False

        lines_data = self._get_replenishment_lines(store=False)
        if not lines_data:
            _logger.info("Automated Replenishment: No low-stock items found.")
            return True

        # wizard means temporary record
        wizard = self.create(
            {
                "destination_warehouse_id": cw_warehouse.id,
            }
        )

        for cmd in lines_data:
            vals = cmd[2]
            vals["wizard_id"] = wizard.id
            self.env["stock.replenishment.wizard.line"].create(vals)

        valid_lines = wizard.line_ids.filtered(lambda l: l.qty_to_request > 0)
        if not valid_lines:
            _logger.info(
                "Automated Replenishment: No lines with positive request quantities."
            )
            return True

        stores = valid_lines.mapped("store_id")
        created_requests = self.env["store.stock.request"]

        for store in stores:
            store_lines = valid_lines.filtered(lambda l: l.store_id == store)

            existing_draft = self.env["store.stock.request"].search(
                [
                    ("store_id", "=", store.id),
                    ("state", "=", "draft"),
                ],
                limit=1,
            )

            if existing_draft:
                for line in store_lines:
                    existing_line = existing_draft.line_ids.filtered(
                        lambda l: l.product_id == line.product_id
                    )
                    if existing_line:
                        existing_line.write({"qty_requested": line.qty_to_request})
                    else:
                        existing_draft.write(
                            {
                                "line_ids": [
                                    (
                                        0,
                                        0,
                                        {
                                            "product_id": line.product_id.id,
                                            "qty_requested": line.qty_to_request,
                                        },
                                    )
                                ]
                            }
                        )
                target_req = existing_draft
            else:
                request_lines = [
                    (
                        0,
                        0,
                        {
                            "product_id": line.product_id.id,
                            "qty_requested": line.qty_to_request,
                        },
                    )
                    for line in store_lines
                ]

                target_req = self.env["store.stock.request"].create(
                    {
                        "store_id": store.id,
                        "destination_warehouse_id": wizard.destination_warehouse_id.id,
                        "request_date": fields.Date.context_today(wizard),
                        "state": "draft",
                        "line_ids": request_lines,
                    }
                )

            target_req.message_post(
                body=_(
                    "Draft stock request automatically generated via Nightly Scheduled Replenishment Cron."
                )
            )
            created_requests |= target_req

        _logger.info(
            "--- AUTOMATED REPLENISHMENT FINISHED: Created/Updated %s requests ---",
            len(created_requests),
        )
        return True


class StockReplenishmentWizardLine(models.TransientModel):
    _name = "stock.replenishment.wizard.line"
    _description = "Batch Stock Replenishment Wizard Line"

    wizard_id = fields.Many2one("stock.replenishment.wizard", ondelete="cascade")
    rule_id = fields.Many2one("store.reorder.rule", string="Reorder Rule")
    store_id = fields.Many2one("retail.store", string="Store")
    product_id = fields.Many2one("product.product", string="Product")
    min_qty = fields.Float(string="Min Qty")
    max_qty = fields.Float(string="Max Qty")
    current_qty = fields.Float(string="Current Stock")
    pending_qty = fields.Float(string="Pending Qty")
    suggested_qty = fields.Float(string="Suggested Stock")
    qty_to_request = fields.Float(string="Qty to Request", default=0.0)
