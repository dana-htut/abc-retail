/** @odoo-module */

// Task 2.1: IMPORTS & CORE DEPENDENCIES
import {registry} from "@web/core/registry";
import {Component, useState, onWillStart} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";

export class RetailDashboard extends Component {
    setup() {
        // Initialize odoo framework services for backend communication
        this.orm = useService("orm"); // For calling model methods, searchRead, searchCount
        this.actionService = useService("action"); // For opening view dynamically
        this.notification = useService("notification"); // For popup toast alerts

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
            // DEBUGGER 1: Check if the method starts and ORM service is ready
            debugger;

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
            debugger;

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
            debugger;

        } catch (error) {
            // DEBUGGER 4: If an ORM error occurs, code jumps straight here.
            // Inspect the `error` object in your browser console!
            debugger;
            console.error("Dashboard Load Error:", error);

            // Display an error notification toast if the ORM call fails
            this.notification.add("Failed to load dashboard data from backend.", { type: "danger" });
        }
    }
}

// TEMPLATE MAPPING & ACTION REGISTRATION
RetailDashboard.template = "retail_store.RetailDashboard";
registry.category("actions").add("retail_store.dashboard_client_action", RetailDashboard);