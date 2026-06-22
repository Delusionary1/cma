"""Application entry point.

Run with:  python run.py
"""
from app import create_app

# Create the Flask application using the factory function.
app = create_app()


if __name__ == "__main__":
    # debug=True enables auto-reload and the interactive debugger.
    # Turn this off in production.
    app.run(debug=True)
