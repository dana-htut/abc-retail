from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class StoreReorderRule(models.Model):
    _name = "store.reorder.rule"
    _description = "Store Reorder Rule"
    _order = "store_id, product_id"

    name = fields.Char(
        string="Rule Reference",
        compute="_compute_name",
        store=True,
    )
    store_id = fields.Many2one(
        "retail.store",
        string="Store",
        required=True,
        default=lambda self: self.env.user.store_id,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        domain="[('type', 'in', ['product', 'consu'])]",
    )
    min_qty = fields.Float(
        string="Minimum Stock",
        default=10.0,
        required=True,
    )
    max_qty = fields.Float(
        string="Maximum Stock",
        default=50.0,
        required=True,
    )
    current_qty = fields.Float(
        string="Current Stock",
        compute="_compute_current_qty",
        help="Real-time stock quantity in the store location.",
    )
    state = fields.Selection(
        [
            ("normal", "Sufficient Stock"),
            ("low", "Low Stock"),
            ("reorder", "Reorder Required"),
        ],
        string="Status",
        compute="_compute_stock_status",
        store=False,
    )

    _sql_constraints = [
        (
            "unique_store_product",
            "unique(store_id, product_id)",
            "A reorder rule already exists for this product at this store!",
        ),
    ]

    @api.depends("store_id", "product_id")
    def _compute_name(self):
        for rule in self:
            if rule.store_id and rule.product_id:
                rule.name = f"{rule.store_id.name} - {rule.product_id.display_name}"
            else:
                rule.name = _("New Rule")

    @api.depends("store_id", "product_id")
    def _compute_current_qty(self):
        """Computes real-time stock strictly within the store's assigned warehouse location."""
        for rule in self:
            # Resolve the store's warehouse (either via custom store_id or company context)
            warehouse = False
            if hasattr(rule.store_id, "warehouse_id") and rule.store_id.warehouse_id:
                warehouse = rule.store_id.warehouse_id
            else:
                # Fallback to searching warehouse linked to the store/company
                warehouse = self.env["stock.warehouse"].search(
                    [("company_id", "=", rule.store_id.id)], limit=1
                )

            if warehouse and warehouse.lot_stock_id and rule.product_id:
                quants = self.env["stock.quant"].search(
                    [
                        ("product_id", "=", rule.product_id.id),
                        ("location_id", "child_of", warehouse.lot_stock_id.id),
                    ]
                )
                rule.current_qty = sum(quants.mapped("quantity"))
            else:
                rule.current_qty = 0.0

    @api.depends("current_qty", "min_qty", "max_qty")
    def _compute_stock_status(self):
        """Categorizes stock status based on minimum threshold."""
        for rule in self:
            if rule.current_qty <= 0:
                rule.state = "reorder"
            elif rule.current_qty <= rule.min_qty:
                rule.state = "low"
            else:
                rule.state = "normal"

    @api.constrains("min_qty", "max_qty")
    def _check_min_max_qty(self):
        """Ensures logical stock boundaries."""
        for rule in self:
            if rule.min_qty < 0 or rule.max_qty < 0:
                raise ValidationError(_("Stock thresholds cannot be negative."))
            if rule.min_qty >= rule.max_qty:
                raise ValidationError(
                    _(
                        "Minimum stock (%s) must be strictly less than Maximum stock (%s)."
                    )
                    % (rule.min_qty, rule.max_qty)
                )

    def _get_pending_qty(self):
        """Calculates the total quantity of the product already requested or in transit for this store.
        Searches store.stock.request.line for active requests in-non-cancelled/non-done states.
        """
        self.ensure_one()
        if not self.store_id or not self.product_id:
            return 0.0

        # Active states where requested stock is currently pending/in progress
        active_states = ["draft", "submitted", "approved"]

        lines = self.env["store.stock.request.line"].search(
            [
                ("request_id.store_id", "=", self.store_id.id),
                ("request_id.state", "in", active_states),
                ("product_id", "=", self.product_id.id),
            ]
        )

        return sum(lines.mapped("qty_requested"))

    # Replenishment engine logic
    def action_generate_stock_request(self):
        """
        1. Scans rules where Current Stock <= Minimum Stock (or state in ['low', 'reorder'])
        2. Calculates Required Quantity = Max Stock - (Current Stock + Pending Quantity).
        3. Identifies Central Warehouse as the default source warehouse.
        4. Groups line creation/updating into a single Draft Stock Request per store.
        5. Returns an action window targeting the generated/updated request(s).
        """
        self._compute_current_qty()
        self._compute_stock_status()
        # Step 1: Scan active rules that need replenishment
        rules_to_process = self.filtered(lambda r: r.state in ["low", "reorder"])

        if not rules_to_process:
            raise UserError(_("No reorder rules currently require replenishment."))

        # Step 2: Locate Central Warehouse (Default Source Warehouse)
        # after all he code, come and check the code for central warehouse
        central_warehouse = self.env["stock.warehouse"].search(
            [("code", "=", "CDC")], limit=1
        )
        if not central_warehouse:
            # Fallback search if code isn't CCD
            central_warehouse = self.env["stock.warehouse"].search([], limit=1)

        # Step 3: Group rules by Store
        stores = rules_to_process.mapped("store_id")
        created_or_updated_requests = self.env["store.stock.request"]

        for store in stores:
            store_rules = rules_to_process.filtered(lambda r: r.store_id == store)

            # Check if a Draft request already exists for this store
            existing_draft = self.env["store.stock.request"].search(
                [
                    ("store_id", "=", store.id),
                    ("state", "=", "draft"),
                ],
                limit=1,
            )

            request_lines = []
            for rule in store_rules:
                # Formula: Effective Stock = Current Stock + Pending Quantity
                pending_qty = rule._get_pending_qty()
                effective_stock = rule.current_qty + pending_qty

                # Formula: Required Quantity = Maximum Stock - Effective Stock
                required_qty = rule.max_qty - effective_stock

                # Skip if required quantity is zero or negative
                if required_qty <= 0:
                    continue

                # Check if product is already in the existing draft request
                if existing_draft:
                    existing_line = existing_draft.line_ids.filtered(
                        lambda l: l.product_id == rule.product_id
                    )
                    if existing_line:
                        # update quantity on existing draft line instead of duplicating
                        existing_line.write({"qty_requested": required_qty})
                        continue

                # Prepare new line tuple (0, 0, values)
                request_lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": rule.product_id.id,
                            "qty_requested": required_qty,
                        },
                    )
                )

            # Step 4: Create or update draft request for the store
            if existing_draft:
                if request_lines:
                    existing_draft.write({"line_ids": request_lines})
                created_or_updated_requests |= existing_draft
            elif request_lines:
                new_request = self.env["store.stock.request"].create(
                    {
                        "store_id": store.id,
                        "destination_warehouse_id": central_warehouse.id
                        if central_warehouse
                        else False,
                        "state": "draft",
                        "line_ids": request_lines,
                    }
                )
                created_or_updated_requests |= new_request

        if not created_or_updated_requests:
            raise UserError(
                _("All low_stock items already have active draft/pending requests.")
            )

        # Step 5: Return action window displaying created/updated draft request(s)
        return {
            "name": _("Generated Stock Requests"),
            "type": "ir.actions.act_window",
            "res_model": "store.stock.request",
            "view_mode": "list,form"
            if len(created_or_updated_requests) > 1
            else "form",
            "res_id": (
                created_or_updated_requests.id
                if len(created_or_updated_requests) == 1
                else False
            ),
            "domain": [("id", "in", created_or_updated_requests.ids)],
        }
