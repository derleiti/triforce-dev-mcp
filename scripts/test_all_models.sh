#!/usr/bin/env bash
# Test all chat models with short response

echo "=== Testing All Models ==="
echo ""

# Get all models
models=$(curl -s http://localhost:9100/v1/models | jq -r '.data[] | select(.capabilities | contains(["chat"])) | .id')

for model in $models; do
    echo "Testing: $model"

    # Make non-streaming request with short timeout
    response=$(timeout 10 curl -s -X POST http://localhost:9100/v1/chat \
      -H "Content-Type: application/json" \
      -d "{\"model\": \"$model\", \"messages\": [{\"role\": \"user\", \"content\": \"Hi\"}], \"stream\": false}" 2>&1)

    # Check for errors
    if echo "$response" | jq -e '.error' > /dev/null 2>&1; then
        error_msg=$(echo "$response" | jq -r '.error.message' 2>/dev/null || echo "$response")
        echo "  ❌ ERROR: $error_msg"
    elif echo "$response" | jq -e '.text' > /dev/null 2>&1; then
        text=$(echo "$response" | jq -r '.text' | head -c 50)
        echo "  ✅ OK: ${text}..."
    else
        echo "  ⚠️  UNKNOWN: $response" | head -c 100
    fi

    echo ""
    sleep 1
done

echo "=== Testing Complete ==="
