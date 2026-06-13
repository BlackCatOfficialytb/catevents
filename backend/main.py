# main.py
import os
# Import the configured Flask 'app' instance from scraper.py
from scraper import app

if __name__ == "__main__":
    # Get port dynamically from the environment, defaulting to 5000
    port = int(os.getenv("PORT", 5000))
    print(f"Starting self-hosted server on port {port}...")
    
    # Run the application
    app.run(host="0.0.0.0", port=port, debug=True)