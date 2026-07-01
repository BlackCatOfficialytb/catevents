# main.py
# Import the configured Flask 'app' instance from scraper.py
from scraper import app
from config import SERVER_HOST, SERVER_PORT, SERVER_DEBUG

if __name__ == "__main__":
    # Host / port / debug come from config.py (PORT env var still wins for port).
    print(f"Starting self-hosted server on {SERVER_HOST}:{SERVER_PORT}...")

    # Run the application
    app.run(host=SERVER_HOST, port=SERVER_PORT, debug=SERVER_DEBUG)