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
import os
import sys
import traceback

# cPanel's "Execute python script" only shows the exit code, not the output.
# So we also write everything to deploy_log.txt next to this file, which you
# can open in cPanel File Manager to read what happened.
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy_log.txt")


def log(message):
    print(message)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(str(message) + "\n")


# Start a fresh log each run.
try:
    open(LOG_PATH, "w", encoding="utf-8").close()
except Exception:
    pass

try:
    from app import create_app
    from flask_migrate import upgrade

    app = create_app()

    # Show which database we're actually connected to (password hidden).
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    safe_uri = uri
    if "@" in uri and "//" in uri:
        head, tail = uri.split("//", 1)
        if "@" in tail:
            creds, host = tail.split("@", 1)
            user = creds.split(":", 1)[0]
            safe_uri = f"{head}//{user}:***@{host}"
    log(f"Using database: {safe_uri}")

    with app.app_context():
        # 1) Bring the database schema up to the latest migration.
        log("Applying database migrations...")
        upgrade()
        log("Migrations applied.")

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
        log(f"Seeding complete. New records -> {summary}")

        # 3) Seed the real opening-balance data (bank accounts, PSO payable,
        # each pump's tank/machine/nozzle setup, purchase rates, opening
        # fuel/lubricant stock). Idempotent — safe to run on every deploy.
        log("Seeding opening balance data...")
        import opening_data_seed
        opening_data_seed.run_all()
        log("Opening balance data seeded.")

        log("DONE — deployment setup succeeded.")
except Exception:
    log("ERROR — deployment setup FAILED:")
    log(traceback.format_exc())
    sys.exit(1)
