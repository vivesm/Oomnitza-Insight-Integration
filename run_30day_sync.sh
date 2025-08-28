#!/bin/bash

# Calculate date range for last 30 days
DATE_FROM=$(date -v -30d '+%Y-%m-%d' 2>/dev/null || date -d '30 days ago' '+%Y-%m-%d')
DATE_TO=$(date '+%Y-%m-%d')

echo "================================================"
echo "Oomnitza-Insight 30-Day Sync"
echo "================================================"
echo "Date Range: $DATE_FROM to $DATE_TO"
echo "================================================"
echo ""

# Set environment variables
export OOMNITZA_URL="https://oscarhealth-dev.oomnitza.com"
export OOMNITZA_API_TOKEN="d0b7bfd7f42441439cef4c1f832a75a1"
export INSIGHT_CLIENT_ID="9668126"
export INSIGHT_CLIENT_KEY="Vyim102tJZNg2kDhjXSWHa9Ml9OQROU5"
export INSIGHT_CLIENT_SECRET="D8q6tn30lIgmhZKZ"
export INSIGHT_URL="https://insight-prod.apigee.net/GetStatus"
export INSIGHT_ORDER_CREATION_DATE_FROM="$DATE_FROM"
export INSIGHT_ORDER_CREATION_DATE_TO="$DATE_TO"

echo "Starting sync..."
echo ""

# Run the connector
./venv/bin/python connector.py upload insight

echo ""
echo "================================================"
echo "Sync completed!"
echo "================================================"
