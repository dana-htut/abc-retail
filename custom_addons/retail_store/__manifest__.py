{
    "name": "Retail Store Management",
    "version": "1.0",
    "category": "Retail",
    "summary": "Manage retail stores and branches",
    "author": "Retail Team",
    "license": "LGPL-3",
    "depends": ["base", "stock", "uom", "product", "hr", "mail", "purchase"],
    "data": [
        # 1. Base Security Access (Models and Access Rights First)
        "security/retail_store_security.xml",
        "security/ir.model.access.csv",
        # 2. Sequences and Data
        "data/retail_store_sequence.xml",
        "data/stock_request_sequence.xml",
        "data/ir_cron_data.xml",
        # 3. UI Views and Menus
        "views/retail_store_views.xml",
        "views/res_users_views.xml",
        "views/stock_request_views.xml",
        "views/store_reorder_rule_views.xml",
        "views/purchase_order_views.xml",
        "views/retail_dashboard_views.xml",
        "wizards/stock_replenishment_wizard_views.xml",
    ],
    "assets" : {
        "web.assets_backend": [
            "retail_store/static/src/js/store_row.js",
            "retail_store/static/src/components/retail_dashboard.js",
            "retail_store/static/src/components/retail_dashboard.xml",
            "retail_store/static/src/xml/store_row.xml",
        ]
    },
    "installable": True,
    "application": True,
}