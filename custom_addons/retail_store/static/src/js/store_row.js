/** @odoo-module **/

import {Component} from "@odoo/owl";

export class StoreRow extends Component {
    static template = "retail_store.StoreRow";

    // Explicitly declare expected props for validation and clarity
    static props = {
        store: Object,
        onOpenStore: Function,
        onRunReplenishment: Function,
    }
}