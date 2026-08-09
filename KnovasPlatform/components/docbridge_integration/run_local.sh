#!/usr/bin/env bash
# Run the KnovasPlatform search UI locally (sample data, no real Knovas API needed).
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv on first run
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q flask flask-cors pyyaml requests python-dateutil tenacity python-dotenv cryptography python-json-logger
fi

export WEB_SECRET_KEY=localdevsecret123abc
export COMPANY_LOGIN_ENABLED=true
export COMPANY_LOGIN_NAME=admin
export COMPANY_LOGIN_PASSWORD=knovas2024!
export SEARCH_USE_TEST_RESULTS=true
export SEMANTIX_API_URL=http://localhost:9999   # not used in test mode

echo ""
echo "  KnovasPlatform running at http://localhost:8081"
echo "  Login: admin / knovas2024!"
echo "  Press Ctrl+C to stop."
echo ""

cd src/web_interface
../../.venv/bin/flask --app app:create_app run --port 8081
