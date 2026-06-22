"""One-shot production setup script (for hosts without terminal/SSH access).

On Namecheap shared hosting you can run this from cPanel:
  Setup Python App  ->  your app  ->  "Execute python script"
  Script path:  deploy.py

It does two things, both safe to run more than once:
  1. Applies all database migrations (same as `flask db upgrade`).
  2. Seeds the starter data (same as `python seed.py`).

If you DO have terminal/SSH access, you can just run the two commands
manually instead; this script is only a convenience for the web-only flow.
"""
from app import create_app
from flask_migrate import upgrade

app = create_app()

with app.app_context():
    # 1) Bring the database schema up to the latest migration.
    print("Applying database migrations...")
    upgrade()
    print("Migrations applied.")

    # 2) Seed the starter data (idempotent — existing rows are skipped).
    from app.core.seed import seed_data
    from app.auth.seed import seed_auth_data
    from app.accounting.seed import seed_accounting_data
    from app.petrol_pumps.seed import seed_pump_setup_data

    created = {
        **seed_data(),
        **seed_auth_data(),
        **seed_accounting_data(),
        **seed_pump_setup_data(),
    }
    summary = ", ".join(f"{key}: {count}" for key, count in created.items())
    print(f"Seeding complete. New records -> {summary}")
