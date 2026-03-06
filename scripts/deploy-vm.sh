#!/usr/bin/env bash
# Deploy Yes Chef API on a VM (AWS EC2, DigitalOcean, GCP, etc.).
# Use this when not using Railway, Fly.io, or other PaaS.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CONTAINER_NAME="yes-chef-api"
IMAGE_NAME="yes-chef"
DATA_DIR="${REPO_ROOT}/data"

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Deploy Yes Chef API on a VM. Sets up .env, builds Docker image, runs container."
    echo ""
    echo "Options:"
    echo "  --systemd    Install as systemd service (persists across reboots)"
    echo "  --skip-env   Skip .env setup (use existing .env)"
    echo "  -h, --help   Show this help"
    exit 0
}

SKIP_ENV=false
INSTALL_SYSTEMD=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --systemd)   INSTALL_SYSTEMD=true; shift ;;
        --skip-env)   SKIP_ENV=true; shift ;;
        -h|--help)    usage ;;
        *)            echo "Unknown option: $1"; usage ;;
    esac
done

# Check Docker
if ! command -v docker &>/dev/null; then
    echo "Error: Docker is required. Install: https://docs.docker.com/engine/install/"
    exit 1
fi

# Setup .env
if [[ "$SKIP_ENV" != "true" ]]; then
    if [[ ! -f .env ]]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env
        echo ""
        echo "Edit .env and set OPENAI_API_KEY (required)."
        echo "  nano .env"
        echo ""
        read -p "Enter OPENAI_API_KEY now (or press Enter to edit .env manually): " API_KEY
        if [[ -n "$API_KEY" ]]; then
            if grep -q '^OPENAI_API_KEY=' .env; then
                sed -i.bak "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=$API_KEY|" .env
            else
                echo "OPENAI_API_KEY=$API_KEY" >> .env
            fi
        fi
    else
        echo "Using existing .env"
    fi

    if ! grep -q 'OPENAI_API_KEY=sk-' .env 2>/dev/null; then
        echo "Warning: OPENAI_API_KEY may not be set. The API will fail without it."
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        [[ $REPLY =~ ^[Yy]$ ]] || exit 1
    fi
fi

# Create data directory for SQLite
mkdir -p "$DATA_DIR"
echo "Data directory: $DATA_DIR"

# Stop and remove existing container (running or stopped)
if docker ps -a -q -f "name=${CONTAINER_NAME}" 2>/dev/null | grep -q .; then
    echo "Stopping and removing existing container..."
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true
fi

# Build and run
echo "Building Docker image..."
docker build -t "$IMAGE_NAME" .

echo "Starting container..."
docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p 8000:8000 \
    --env-file .env \
    -v "${DATA_DIR}:/app/data" \
    "$IMAGE_NAME"

echo ""
echo "Waiting for health check..."
MAX_WAIT=60
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        echo ""
        echo "Yes Chef API is running at http://localhost:8000"
        echo ""
        echo "Test: uv run python test_stream.py --file data/menu_spec.json --base-url http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000"
        echo ""
        echo "Logs: docker logs -f $CONTAINER_NAME"
        echo "Stop: docker stop $CONTAINER_NAME"

        if [[ "$INSTALL_SYSTEMD" == "true" ]]; then
            echo ""
            echo "Installing systemd service..."
            SVC_FILE="/etc/systemd/system/yes-chef.service"
            sudo tee "$SVC_FILE" >/dev/null << EOF
[Unit]
Description=Yes Chef API
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$REPO_ROOT
ExecStart=/usr/bin/docker start $CONTAINER_NAME
ExecStop=/usr/bin/docker stop $CONTAINER_NAME
EOF
            sudo systemctl daemon-reload
            sudo systemctl enable yes-chef.service
            echo "Systemd service installed. Container will start on boot."
        fi

        exit 0
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

echo "Timeout. Check logs: docker logs $CONTAINER_NAME"
exit 1
