"""WSGI entry point for cPanel / Passenger (shared hosting).

cPanel's "Setup Python App" runs the app through Phusion Passenger, which
imports a module-level WSGI callable named ``application`` from this file.

Startup file:  passenger_wsgi.py
Entry point:   application

Configuration (SECRET_KEY, DATABASE_URL, ...) is read from environment
variables, which you set in the cPanel "Setup Python App" screen. Never run
the development server (run.py / debug=True) in production.
"""
import os
import sys

# Make sure the project root is importable regardless of how Passenger
# launches the process.
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app

application = create_app()
