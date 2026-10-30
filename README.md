![Ruff CI](https://github.com/dana-htut/abc_retail/actions/workflows/ruff.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![Odoo](https://img.shields.io/badge/odoo-19.0-purple.svg)
![License](https://img.shields.io/badge/license-LGPL--3-green.svg)

## 📌 Project Overview
Custom Odoo 19 module designed to streamline multi-store inventory replenishment, automated reorder triggers, custom business rules, and interactive OWL dashboard analytics. Built with clean, modular Python backend models, custom web views, and automated CI quality checks.

![OWL Dashboard Analytics](custom_addons/retail_store/static/description/dashboard.png)

---

## 🚀 Key Features & Technical Highlights

* **Custom OWL Dashboard Components:** Interactive, real-time analytics widgets built using Odoo's Owl (Odoo Web Library) framework.
* **Automated Stock Replenishment & Client Actions:** Configured inventory reorder rules and server actions to trigger reorder workflows dynamically.
* **ORM Architecture & PostgreSQL Integration:** Custom data models extending Odoo core functionality, leveraging optimized PostgreSQL database interactions.
* **Automated CI/CD Quality Pipeline:** Integrated GitHub Actions workflow using `ruff` for automated code formatting and static analysis checks on every commit.

---

## 🛠️ Tech Stack

* **ERP Platform:** Odoo 19.0 Community / Enterprise
* **Backend:** Python 3.12, Odoo ORM Framework
* **Frontend:** JavaScript (OWL Framework), XML, QWeb
* **Database:** PostgreSQL
* **Tooling & CI/CD:** Git, GitHub Actions, Ruff Linter/Formatter

---

## 🏗️ Architecture & Technical Decisions

* **Technology Stack:** Built on **Odoo 19**, powered by **Python 3.12** backend models and **PostgreSQL** relational data management. The user interface incorporates custom **JavaScript / OWL (Odoo Web Library)** components for responsive, real-time data visualization.
* **Automated CI/CD Pipeline:** Integrated **GitHub Actions** (`ruff.yml`) to enforce code standards automatically. Every pull request and push to integration branches runs static analysis and format validation via **Ruff**, ensuring clean, production-ready code before any merge to main.

## 💻 Installation & Local Setup

To clone and run code checks locally on your machine, follow these steps:

```bash
# Clone the repository
git clone [https://github.com/dana-htut/abc_retail.git](https://github.com/dana-htut/abc_retail.git)

# Navigate into the project directory
cd abc_retail

# Run local ruff checks
ruff check custom_addons/retail_store/
