"""Standalone database seeding script.

Usage:  python seed.py

This does the same thing as `flask seed`, but as a plain script. It is safe to
run multiple times: existing records are detected and never duplicated.
"""
from app import create_app
from app.core.seed import seed_data
from app.auth.seed import seed_auth_data
from app.accounting.seed import seed_accounting_data
from app.petrol_pumps.seed import seed_pump_setup_data

app = create_app()

with app.app_context():
    created = {
        **seed_data(),
        **seed_auth_data(),
        **seed_accounting_data(),
        **seed_pump_setup_data(),
    }
    summary = ", ".join(f"{key}: {count}" for key, count in created.items())
    print(f"Seeding complete. New records -> {summary}")
