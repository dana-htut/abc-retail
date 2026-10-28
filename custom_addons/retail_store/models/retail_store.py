from bdb import effective

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.http import request


class RetailStore(models.Model):
  _name = "retail.store"
  _description = "Retail Store"
  _inherit = ["mail.thread"]

  name = fields.Char(string="Store Name", required=True, tracking=True)
  code = fields.Char(
      string="Store Code",
      required=True,
      copy=False,
      readonly=True,
      default=lambda self: "New",
  )
  manager_id = fields.Many2one(
      "hr.employee",
      string="Store Manager",
      ondelete="set null",
      help="Store Manager responsible for stock requests.",
      tracking=True
  )
  company_id = fields.Many2one(
      "res.company", string="Company", required=True, default=lambda self: self.env.company
  )
  warehouse_id = fields.Many2one("stock.warehouse", string="Warehouse")
  location = fields.Char(string="Location / Address")
  opening_date = fields.Date(string="Opening Date", default=fields.Date.today)
  state = fields.Selection(
      [
          ("draft", "Draft"),
          ("open", "Open"),
          ("closed", "Closed"),
      ],
      string="Status",
      default="draft",
  )


  def action_open(self):
    """Transition store state to open (Administrators only)."""
    # Security check: Restrict execution to Administrators
    if not self.env.user.has_group("retail_store.group_retail_administrator"):
      raise UserError("Only Administrators can open retail stores.")

    for record in self:
      if record.state != "draft":
        raise UserError("Only draft stores can be opened.")
      record.state = "open"

  def action_close(self):
    """Transition store state to closed (Administrators only)."""
    # Security check: Restrict execution to Administrators
    if not self.env.user.has_group("retail_store.group_retail_administrator"):
      raise UserError("Only Administrators can close retail stores.")

    for record in self:
      if record.state != "open":
        raise UserError("Only open stores can be closed.")
      record.state = "closed"

  def action_draft(self):
    """Reset store state back to draft (Administrators only)."""
    # Security check: Restrict execution to Administrators
    if not self.env.user.has_group("retail_store.group_retail_administrator"):
      raise UserError("Only Administrators can reset stores to draft.")

    for record in self:
      if record.state != "closed":
        raise UserError("Only closed stores can be reset to draft.")
      record.state = "draft"

  def unlink(self):
    """Prevent deletion of active or closed retail stores."""
    for record in self:
      if record.state != "draft":
        raise UserError(
            "You cannot delete a store that is active or closed. Reset it to"
            " draft first."
        )
    return super().unlink()

  def action_run_replenishment_check(self):
      """
      Triggers a store_specific replenishment evaluation:
      1. Scans reorder rules linked strictly to this store in 'low' or 'reorder' state.
      2. Calculates required quantities accounting for existing pending quantities.
      3. Creates or updates a consolidated Draft Stock Request document.
      4. Returns an action window to display the generated/updated request.
      """
      self.ensure_one()

      # Check if the logged-in user matches the manager's linked user account
      if self.manager_id and self.manager_id.user_id != self.env.user and not self.env.is_admin():
          raise UserError(
              _("Access Denied: Only the assigned Store Manager (%s) can trigger replenishment for %s.")
              % (self.manager_id.name, self.name)
          )

      # Step 1: Scan Low Stock Rules for this store
      all_store_rules = self.env["store.reorder.rule"].search([
          ("store_id", "=", self.id),
      ])
      all_store_rules._compute_current_qty()
      all_store_rules._compute_stock_status()
      reorder_rules = all_store_rules.filtered(
          lambda r: r.state in ["low", "reorder"]
      )

      # Step 2: Handle Edge Case (No rules require replenishment)
      if not reorder_rules:
          raise UserError(
              _("All inventory levels for store '%s' are currently sufficient.") % self.name
          )

      # Step 3: Locate Central Warehouse (Default Source Warehouse)
      cdc_warehouse = self.env["stock.warehouse"].search(
          [("code", "=", "CW")], limit=1
      )
      if not cdc_warehouse:
          cdc_warehouse = self.env["stock.warehouse"].search([], limit=1)

      if not cdc_warehouse:
          raise UserError(_("No Central Supply Warehouse found in the system."))

      # Step 4: Check for an existing Draft Request for this store
      existing_draft = self.env["store.stock.request"].search([
          ("store_id", "=", self.id),
          ("state", "=", "draft"),
      ], limit=1)

      request_lines = []
      for rule in reorder_rules:
          # Calculate Effective Stock = Current Stock + Pending Qty
          pending_qty = rule._get_pending_qty()
          effective_stock = rule.current_qty + pending_qty

          # Calculate Required Qty = Max Stock - Effective Stock
          required_qty = rule.max_qty - effective_stock

          if required_qty <= 0:
              continue

          # Update existing line if product already exists in current draft
          if existing_draft:
              existing_line = existing_draft.line_ids.filtered(
                  lambda l: l.product_id== rule.product_id
              )
              if existing_line:
                  existing_line.write({"qty_requested": required_qty})
                  continue

          # Command tuple (0, 0, values) to add new line item
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

      # Step 5: Create or Update Stock Request Record
      if existing_draft:
          if request_lines:
              existing_draft.write({"line_ids": request_lines})
          target_request = existing_draft
      elif request_lines:
          target_request = self.env["store.stock.request"].create({
              "store_id": self.id,
              "company_id": getattr(self, "company_id", self.env.company).id,
              "destination_warehouse_id": cdc_warehouse.id,
              "request_date": fields.Date.context_today(self),
              "state": "draft",
              "line_ids": request_lines,
          })
      else:
          raise UserError(
              _("All low stock items for store '%s' already have active pending requests.") % self.name
          )

      # Step 6: Return Action Window targeting the generated/updated Draft Request
      return {
          "name": _("Stock Request - %s") % self.name,
          "type": "ir.actions.act_window",
          "res_model": "store.stock.request",
          "view_mode": "form",
          "res_id": target_request.id,
          "target": "current",
      }