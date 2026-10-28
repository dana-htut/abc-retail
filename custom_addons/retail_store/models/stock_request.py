import shelve

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class StoreStockRequest(models.Model):
    _name = "store.stock.request"
    _description = "Stored Stock Request"
    _order = "id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    @api.model
    def _default_store_id(self):
        """Default to the current logged-in user's store."""
        return self.env.user.store_id if hasattr(self.env.user, 'store_id') else False

    name = fields.Char(
        string="Request Reference",
        required=True,
        copy=False,
        readonly=True,
        default="New",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        help="Company associated with this stock request."
    )
    store_id = fields.Many2one(
        "retail.store",
        string="Requesting Store",
        required=True,
        default=_default_store_id,
        domain="[('state', '=', 'open')]",
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Source Store Warehouse",
        compute="_compute_warehouse_id",
        store=True,
        readonly=True,
    )
    destination_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Supply Warehouse (CDC)",
        required=True,
        help="Warehouse that will fulfill this request."
    )
    user_id = fields.Many2one(
        "res.users",
        string="Requested By",
        default=lambda self: self.env.user,
        required=True,
        readonly=True
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        compute="_compute_employee_id",
        store=True,
        readonly=False,
        tracking=True,
        help="HR Employee record linked to the requesting user."
    )
    request_date = fields.Date(
        string="Request Date",
        default=fields.Date.today,
        required=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ('rejected', 'Rejected'),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        string="Status",
        default="draft",
        tracking=True,
    )
    line_ids = fields.One2many(
        "store.stock.request.line",
        "request_id",
        string="Request Lines",
    )

    # ==========
    # PHASE 4 ADDITIONS: Native Inventory Linkage & Smart Button Fields
    # ==========
    # 1. Store direct Many2one reference to the generated stock.picking transfer order
    picking_id = fields.Many2one(
        "stock.picking",
        string="Stock Transfer",
        readonly=True,
        copy=False,
    )

    # 2. Compute field used for UI Stat Button counter (returns 1 if picking exists, 0 if not)
    picking_count = fields.Integer(
        string="Transfers",
        compute="_compute_picking_count",
    )

    # =====
    # Phase 7
    # =====
    purchase_order_id = fields.Many2one(
        "purchase.order",
        string="Purchase Order",
        readonly=True,
        copy=False,
        help="The external purchase order generated when supply warehouse stock was insufficient."
    )
    purchase_count = fields.Integer(
        string="Purchase Orders Count",
        compute="_compute_purchase_count",
    )

    @api.depends("purchase_order_id")
    def _compute_purchase_count(self):
        for record in self:
            record.purchase_count = 1 if record.purchase_order_id else 0

    def action_show_purchase_order(self):
        "Action behind the Smart Button to navigate directly to the linked purchase order."
        self.ensure_one()
        if not self.purchase_order_id:
            return {}

        return {
            "name": _("Purchase Order"),
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "res_id": self.purchase_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def _get_default_vendor(self):
        "Help method to find the specific demo vendor partner for purchase orders."
        # 1. Specifically look for demo general supplier first
        vendor = self.env["res.partner"].search([
            ("name", "ilike", "Demo General Supplier")
        ], limit=1)

        # 2. If not found, look for any partner with supplier rank > 0
        if not vendor:
            vendor = self.env["res.partner"].search([
                ("supplier_rank", ">", 0)
            ], limit=1)

        if not vendor:
            raise UserError(
                "Configuration Error: Could not find 'Demo General Supplier'."
                "Please make sure a vendor contact with that name exists in the Contacts app."
            )
        return vendor

    def action_create_purchase_order(self):
        "Generate a draft Purchase Order for items with insufficient stock in the supply warehouse."
        self.ensure_one()

        if self.purchase_order_id:
            raise UserError(_("A Purchase Order has already been created for this request."))

        if self.state not in ["submitted", "approved"]:
            raise UserError(_("You can only create a purchase order for submitted or approved requests."))

        # 1. Get default vendor using our helper method
        vendor = self._get_default_vendor()

        # 2. Check stock availability and build purchase lines for shortages
        source_location = self.destination_warehouse_id.lot_stock_id
        po_lines = []

        for line in self.line_ids.filtered(lambda l: l.qty_requested > 0.0):
            # Check available stock in supplying warehouse
            quants = self.env["stock.quant"].sudo().search([
                ("product_id", "=", line.product_id.id),
                ("location_id", "child_of", source_location.id)
            ])
            available_qty = sum(quants.mapped("quantity")) - sum(quants.mapped("reserved_quantity")) # reserved_quantity = unvalidated ones

            # Calculate Shortage Qty = Requested Qty - Available Stock
            shortage_qty = line.qty_requested - available_qty

            if shortage_qty > 0.0:
                po_lines.append((0, 0, {
                    "product_id": line.product_id.id,
                    "product_qty": shortage_qty,
                    "product_uom_id": line.uom_id.id,
                    "name": line.product_id.display_name,
                    "date_planned": fields.Datetime.now(),
                    "price_unit": line.product_id.standard_price or 0.0,
                }))

        if not po_lines:
            raise UserError(_("There are no stock shortages for this request. All request quantities are available in the supply warehouse."))

        # 3. Create the Purchase Order record
        po_vals = {
            "partner_id": vendor.id,
            "company_id": self.company_id.id,
            "store_stock_request_id": self.id,
            "origin": self.name,
            "order_line": po_lines,
        }
        purchase_order = self.env["purchase.order"].create(po_vals)

        # 4. Link PO back to stock request and post chatter notification
        self.write({"purchase_order_id": purchase_order.id})

        self.message_post(
            body=_(
                "Purchase Order <b><a href='#' data-oe-model='purchase.order' data-oe-id='%s'>%s</a></b> "
                "was successfully created for stock shortages."
            ) % (purchase_order.id, purchase_order.name)
        )

        # 5. Return action window to open the newly created PO automatically
        return {
            "name": _("Generated Purchase Order"),
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "res_id": purchase_order.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.depends("picking_id")
    def _compute_picking_count(self):
        for record in self:
            record.picking_count = 1 if record.picking_id else 0

    def action_show_picking(self):
        """Action behind the Smart Button to navigate directly to the stock transfer."""
        self.ensure_one()
        if not self.picking_id:
            return {}

        return {
            "name": _("Stock Transfer"),
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "res_id": self.picking_id.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.depends("store_id")
    def _compute_warehouse_id(self):
        """Auto-populate the source warehouse based on the selected open store."""
        for record in self:
            record.warehouse_id = record.store_id.warehouse_id if record.store_id else False

    @api.depends("user_id")
    def _compute_employee_id(self):
        """Auto populate the HR Employee record linked to the requesting system user."""
        for record in self:
            if record.user_id:
                # Odoo's native shortcut to find the employee profile linked to a user
                record.employee_id = record.user_id.employee_id or False
            else:
                record.employee_id = False

    @api.model_create_multi
    def create(self, vals_list):
        """Generate unique sequence reference number upon record creation."""
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("store.stock.request") or "New"
        return super().create(vals_list)

    # === ACTION METHODS ===
    def _validate_manager_access(self, record):
        """Enforce that non-admin managers can only act on their own or target branch requests."""
        if self.env.user.has_group("retail_store.group_retail_administrator"):
            return

        user_store = getattr(self.env.user, "store_id", False)
        if not user_store:
            raise UserError("Access Denied: Your user profile is not assigned to any retail store.")

        is_request_manager = record.store_id == user_store
        is_supply_manager = user_store.warehouse_id and record.destination_warehouse_id == user_store.warehouse_id

        if not (is_request_manager or is_supply_manager):
            raise UserError(
                f"Access Denied: As manager of {user_store.name}, you are not authorized "
                f"to process requests for {record.store_id.name}."
            )

    def action_submit(self):
        """Store Employee & Managers can submit draft requests and generate the internal transfer."""
        if not (
                self.env.user.has_group("retail_store.group_retail_store_employee")
                or self.env.user.has_group("retail_store.group_retail_store_manager")
                or self.env.user.has_group("retail_store.group_retail_administrator")
        ):
            raise UserError("You do not have permission to submit stock requests.")

        for record in self:
            if record.state != "draft":
                raise UserError("Only draft requests can be submitted.")
            if not record.line_ids:
                raise UserError("You cannot submit a stock request without any product lines.")

            # 1. Fetch Internal Transfer Operation Type from Supplying Warehouse
            picking_type = record.destination_warehouse_id.int_type_id
            if not picking_type:
                raise UserError(
                    f"Configuration Error: No Internal Transfer operation type configured for supply warehouse: "
                    f"'{record.destination_warehouse_id.name if record.destination_warehouse_id else 'Unknown'}'"
                )

            # 2. Build header dictionary for stock.picking
            picking_vals = {
                "picking_type_id": picking_type.id,
                "location_id": record.destination_warehouse_id.lot_stock_id.id,
                "location_dest_id": record.warehouse_id.lot_stock_id.id,
                "origin": record.name,
            }
            picking = self.env["stock.picking"].sudo().create(picking_vals)

            # 3. Build move lines (stock.move) for each line with requested qty > 0
            move_lines = []
            for line in record.line_ids.filtered(lambda l: l.qty_requested > 0.0):
                move_lines.append((0, 0, {
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.qty_requested,
                    "product_uom": line.uom_id.id,
                    "picking_id": picking.id,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                }))

            if not move_lines:
                raise UserError("Cannot submit request because no items have a requested quantity greater than 0.")

            # 4. Attach move lines to picking and confirm transfer reservation
            picking.sudo().write({"move_ids": move_lines})
            picking.sudo().action_confirm()

            # 5. Link generated picking to stock request record and update state
            record.write({
                "picking_id": picking.id,
                "state": "submitted",
            })

    def action_reject(self):
        """Store Managers & Administrators can reject submitted requests for their store."""
        if not (
                self.env.user.has_group("retail_store.group_retail_store_manager")
                or self.env.user.has_group("retail_store.group_retail_administrator")
        ):
            raise UserError("Only Store Managers or Administrators can reject stock requests.")

        for record in self:
            self._validate_manager_access(record)
            if record.state != "submitted":
                raise UserError("Only submitted requests can be rejected.")
            record.write({"state": "rejected"})

    def action_approve(self):
        """Store Managers & Admins can approve submitted requests ONLY AFTER warehouse validation."""
        if not (
                self.env.user.has_group("retail_store.group_retail_store_manager")
                or self.env.user.has_group("retail_store.group_retail_administrator")
        ):
            raise UserError("Only Store Managers or Administrators can approve stock requests.")

        current_user = self.env.user
        is_admin = current_user.has_group("retail_store.group_retail_administrator") or current_user.has_group(
            "base.group_system")

        for record in self:
            self._validate_manager_access(record)

            # BUG FIX 1: Enforce Supplying Warehouse Manager Check
            user_warehouse = current_user.store_id.warehouse_id if hasattr(current_user,
                                                                           "store_id") and current_user.store_id else False
            supplying_warehouse = record.destination_warehouse_id
            if not is_admin:
                if not user_warehouse or user_warehouse != supplying_warehouse:
                    raise UserError(
                        f"Permission Denied: Only managers belonging to the supplying warehouse "
                        f"'{supplying_warehouse.name if supplying_warehouse else 'Unknown'}' "
                        f"can approve this request."
                    )

            if record.state != "submitted":
                raise UserError("Only submitted requests can be approved.")

            # BUG FIX 2: Require Warehouse Staff Validation First
            # Elevate picking evaluation with .sudo() so managers without Inventory privileges can read picking state
            picking = record.picking_id.sudo() if record.picking_id else False

            if not picking:
                raise UserError(
                    f"Cannot approve: No internal transfer (picking) exists for request '{record.name}' yet."
                )
            if picking.state != "done":
                raise UserError(
                    f"Validation Required: Internal transfer '{picking.name}' is currently in state '{picking.state}'.\n"
                    f"Warehouse staff at '{supplying_warehouse.name if supplying_warehouse else 'Unknown'}' must validate the transfer in the Inventory app before manager approval."
                )

            # Sync line approved quantities
            for line in record.line_ids:
                if line.qty_approved <= 0.0:
                    line.qty_approved = line.qty_requested

            # Check Available Stock in Supplying Warehouse
            if not record.purchase_order_id:
                source_location = record.destination_warehouse_id.lot_stock_id
                for line in record.line_ids.filtered(lambda l: l.qty_approved > 0.0):
                    quants = self.env["stock.quant"].sudo().search([
                        ("product_id", "=", line.product_id.id),
                        ("location_id", "child_of", source_location.id)
                    ])
                    available_qty = sum(quants.mapped("quantity")) - sum(quants.mapped("reserved_quantity"))

                    if available_qty < line.qty_approved:
                        raise UserError(
                            f"Insufficient stock in supply warehouse '{record.destination_warehouse_id.name}'!\n"
                            f"Product: {line.product_id.display_name}\n"
                            f"Available: {available_qty}{line.uom_id.name} | Requested Approved: {line.qty_approved}{line.uom_id.name}"
                        )

            # Update stock request status to approved
            record.write({"state": "approved"})

    def action_done(self):
        """Warehouse Staff & Administrators can mark approved requests as Done."""
        if not (
                self.env.user.has_group("retail_store.group_retail_warehouse_staff")
                or self.env.user.has_group("retail_store.group_retail_administrator")
        ):
            raise UserError("Only Warehouse Staff or Administrators can complete stock requests.")

        is_admin = self.env.user.has_group("retail_store.group_retail_administrator")
        user_warehouse = self.env.user.store_id.warehouse_id

        for record in self:
            # Enforce warehouse staff scope check
            if not is_admin:
                if not user_warehouse:
                    raise UserError(
                        "Access Denied: Your user account is not assigned to any store or warehouse."
                    )
                if record.destination_warehouse_id != user_warehouse:
                    raise UserError(
                        f"Access Denied: You are assigned to 'user_warehouse.name',"
                        f"but this request is being supplied by 'record.destination_warehouse_id.name'."
                    )

            if record.state != "approved":
                raise UserError("Only approved requests can be marked as done.")


            # =====
            # PHASE 4 LOGIC: State Synchronization Check
            # Prevent completing request if underlying stock transfer isn't validated yet
            # =====
            if record.picking_id and record.picking_id.state != "done":
                raise UserError(
                    f"Cannot complete request! The linked stock transfer ({record.picking_id.name})"
                    f"is currently in state '{record.picking_id.state}'."
                    f"Please validate the stock transfer in the Inventory App first."
                )

            record.write({"state": "done"})

    def action_cancel(self):
        """Store Managers & Administrators can cancel requests for their store."""
        if not (
                self.env.user.has_group("retail_store.group_retail_store_manager")
                or self.env.user.has_group("retail_store.group_retail_administrator")
        ):
            raise UserError("Only Store Managers or Administrators can cancel stock requests.")

        for record in self:
            self._validate_manager_access(record)
            if record.state == "done":
                raise UserError("You cannot cancel a request that is already marked as Done.")
            record.write({"state": "cancel"})

    def action_draft(self):
        """Store Managers & Administrators can reset requests back to draft state for their store."""
        if not (
                self.env.user.has_group("retail_store.group_retail_store_manager")
                or self.env.user.has_group("retail_store.group_retail_administrator")
        ):
            raise UserError("Only Store Managers or Administrators can reset requests to draft.")

        for record in self:
            self._validate_manager_access(record)
            record.write({"state": "draft"})

    # =====
    # PHASE 4 ADDITION: Smart Button Navigation Action
    # =====

    def action_show_picking(self):
        """Opens the single linked stock.picking record in standard Odoo form view."""
        self.ensure_one()
        if not self.picking_id:
            raise UserError("No stock transfer order is linked to this request.")

        return {
            "name": "Stock Transfer",
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "res_id": self.picking_id.id,
            "view_mode": "form",
            "target": "current",
        }

class StoreStockRequestLine(models.Model):
    _name = "store.stock.request.line"
    _description = "Store Stock Request Line"

    request_id = fields.Many2one(
        "store.stock.request",
        string="Stock Request",
        ondelete="cascade",
        required=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        domain="[('type', 'in', ['product', 'consu'])]",
    )
    qty_requested = fields.Float(
        string="Requested Qty",
        default=1.0,
        required=True,
    )
    qty_approved = fields.Float(
        string="Approved Qty",
        default=0.0,
    )
    allowed_uom_ids = fields.Many2many(
        'uom.uom',
        compute='_compute_allowed_uom_ids'
    )
    uom_id = fields.Many2one(
        "uom.uom",
        string="Unit of Measure",
        compute="_compute_uom_id",
        store=True,
        readonly=False,
        domain="[('id', 'in', allowed_uom_ids)]",
    )

    @api.depends("product_id")
    def _compute_allowed_uom_ids(self):
        """Automatically find all compatible units based on the product's base unit."""
        for line in self:
            if line.product_id and line.product_id.uom_id:
                base_uom = line.product_id.uom_id

                # Find the root reference unit for this product's unit
                # (If base_uom is a reference unit itself, use it; other follow relative_uom_id))
                reference_uom = base_uom.relative_uom_id or base_uom

                # Find all units in the system that share this same reference unit family
                line.allowed_uom_ids = self.env['uom.uom'].search([
                    '|',
                    ('id', '=', reference_uom.id),
                    ('relative_uom_id', '=', reference_uom.id)
                ])
            else:
                line.allowed_uom_ids = self.env['uom.uom'].browse()


    @api.depends("product_id")
    def _compute_uom_id(self):
        """Auto-populate the uom based on the selected product type."""
        for line in self:
            if line.product_id:
                line.uom_id = line.product_id.uom_id
            else:
                line.uom_id = False

    @api.constrains("uom_id", "product_id")
    def _check_uom_category(self):
        """Enforces Unit Safety Check: blocks mismatched units on save/submit."""
        for line in self:
            if line.product_id and line.uom_id:
                if line.uom_id not in line.allowed_uom_ids:
                    raise ValidationError(
                        _("The Unit of Measure '%s' is not compatible with product '%s'.")
                        % (line.uom_id.name, line.product_id.name)
                    )