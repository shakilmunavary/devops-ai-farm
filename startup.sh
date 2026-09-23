#!/bin/bash
echo "=== Starting DevOps Autonomous Agent Farm Portal ==="

if [ -f "/antenv/bin/activate" ]; then
    echo "Activating /antenv virtual environment..."
    source /antenv/bin/activate
elif [ -f "/home/site/wwwroot/antenv/bin/activate" ]; then
    echo "Activating local antenv virtual environment..."
    source /home/site/wwwroot/antenv/bin/activate
fi

# Ensure critical packages are available
python -c "import flask, httpx, dotenv" 2>/dev/null || pip install -r requirements.txt --prefer-binary --no-cache-dir

echo "Starting Flask web application on port ${PORT:-5000}..."
exec python app.py
