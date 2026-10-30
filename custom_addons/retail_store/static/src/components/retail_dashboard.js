/** @odoo-module */

// Task 2.1: IMPORTS & CORE DEPENDENCIES
import {registry} from "@web/core/registry";
import {Component, useState, onWillStart} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";
import {ConfirmationDialog} from "@web/core/confirmation_dialog/confirmation_dialog";

export class RetailDashboard extends Component {
    setup() {
        // Initialize odoo framework services for backend communication
        this.orm = useService("orm"); // For calling model methods, searchRead, searchCount
        this.actionService = useService("action"); // For opening view dynamically
        this.notification = useService("notification"); // For popup toast alerts

        // 5.1: Initialize the dialog service
        this.dialog = useService("dialog");

        // Define the reactive state using useState.
        // Any changes to these properties will automatically trigger a UI re-render.
        this.state = useState({
            // KPI counters for the top summary cards
            kpis: {
                total_stores: 0,
                total_requests: 0,
                pending_approvals: 0,
                low_stock: 0,
            },
            // Array to store the stock request records loaded from the backend
            request: [],
            // Filtered list displayed in the data table
            filteredRequests: [],
            // Reactive search query string bound to the search input box
            searchQuery: "",
            // Current active state filter pill ("all", "draft", "submitted", etc.)
            activeFilter: "all",
        });

        // fetch data before components mount
        onWillStart(async() => {
           await this.loadDashboardData();
        });
    } // setup

    // Asynchronous helper function to query Odoo models viat the ORM service.
    async loadDashboardData() {
        try {
            // 1. Fetch total count of retail store records
            const storeCount = await this.orm.searchCount("retail.store", []);

            // 2. Fetch total count of all stock requests across stores
            const requestCount = await this.orm.searchCount("store.stock.request", []);

            // 3. Fetch count of pending requests (where state is "submitted")
            // Note: changed from .search to .searchCount since you just want the number!
            const pendingCount = await this.orm.searchCount(
                "store.stock.request",
                [["state", "=", "submitted"]]
            );

            // 4. Fetch count of low stock rules/products needing attention
            const reorderRules = await this.orm.searchRead(
                "store.reorder.rule",
                [],
                ["state"]
            );

            const lowStockCount = reorderRules.filter(
                rule=>["low", "reorder"].includes(rule.state)
            ).length;

            // DEBUGGER 2: Check the fetched numbers before assigning to state

            // Update the reactive KPI state object with fetched numbers
            this.state.kpis = {
                total_stores: storeCount,
                total_requests: requestCount,
                pending_approvals: pendingCount,
                low_stock: lowStockCount,
            };

            // 5. Fetch actual records for the data table (Request ID, Store, Employee, State, Date)
            this.state.requests = await this.orm.searchRead(
                "store.stock.request",
                [],
                ["name", "store_id", "employee_id", "state", "request_date"]
            );

            // DEBUGGER 3: Check final state requests array
            this.applyFilters();

        } catch (error) {
            // DEBUGGER 4: If an ORM error occurs, code jumps straight here.
            // Inspect the `error` object in your browser console!
            console.error("Dashboard Load Error:", error);

            // Display an error notification toast if the ORM call fails
            this.notification.add("Failed to load dashboard data from backend.", { type: "danger" });
        }
    }

    // Task 4.1: Handle State Filter Pills ("all", "draft", "submitted", "approved", "rejected", "done", "cancel")
    filterRequests(stateName) {
        this.state.activeFilter = stateName;
        this.applyFilters();
    }

    // Task 4.2: Handle Live Search Input on Keyup
    onSearchKeyup(ev) {
        this.state.searchQuery = ev.target.value.toLowerCase();
        this.applyFilters();
    }

    // Combining filtering logic for both state pills and live search query
    applyFilters() {
        let result = this.state.requests;
        //Filter by state if not "all"
        if (this.state.activeFilter != "all") {
            result = result.filter(r => r.state && r.state.toLowerCase() === this.state.activeFilter.toLowerCase());
        }

        // Filter by search query (Name, Store Name, Employee Name)
        if (this.state.searchQuery) {
            const query = this.state.searchQuery;
            result = result.filter(r => {
                const nameMatch = r.name && r.name.toLowerCase().includes(query);
                const storeMatch = r.store_id && r.store_id[1].toLowerCase().includes(query);
                const employeeMatch = r.employee_id && r.employee_id[1].toLowerCase().includes(query);
                return nameMatch || storeMatch || employeeMatch;
            });
        }

        this.state.filteredRequests = result;
    }

    // Task 4.3: Open existing stock requests record in standard form view
    async viewRecord(recordId) {
        await this.actionService.doAction({
           type: "ir.actions.act_window",
           res_model: "store.stock.request",
           res_id: recordId,
           views: [[false, "form"]],
           target: "current",
        });
    }

    // Task 4.4: Open form view to create a brand new stock request
    async createNewRequest() {
        await this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "store.stock.request",
            views: [[false, "form"]],
            target: "current",
        });
    }

    // Task 4.5: Delete record via ORM with notification and refresh
    async deleteRecord(recordId) {
        // Trigger Odoo's built-in confirmationdialog modal service
        this.dialog.add(ConfirmationDialog, {
           // Title displayed at the top of the modal window
           title: "Delete Confirmation",

           // Warning body text explaining the consequence of the action
           body: "Are you sure you want to delete this stock request? This action cannot be undone.",

           // Callback function executed when the user clicks the confirmation button
           confirm: async() => {
               try {
                   // call orm unlink method
                   await this.orm.unlink("store.stock.request", [recordId]);
                   //display green success
                   this.notification.add("Stock request deleted successfully.", {type:"success"});
                   // Reload dashboard data to refresh KPIs, total counts, and the data table
                   await this.loadDashboardData();
               } catch (error) {
                    console.error("Delete Error:", error)
                    // Display red error
                   this.notification.add("Failed to delete record.", {type: "danger"});
               }
           },
            // callback if the user cancels the modal
            cancel: () => {},
            // custom label for primary action button
            confirmLabel: "Delete",
            // Bootstrap styling
            confirmClass: "btn-danger",
        });
    }

}

// TEMPLATE MAPPING & ACTION REGISTRATION
RetailDashboard.template = "retail_store.RetailDashboard";
registry.category("actions").add("retail_store.dashboard_client_action", RetailDashboard);