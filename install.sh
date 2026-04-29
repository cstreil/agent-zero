#!/usr/bin/env bash
#
# Agent Zero Docker Installer
# Builds and runs Agent Zero from the local source tree
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

print_banner() {
    printf "%b" "${BLUE}"
    cat <<'EOF'
   █████╗  ██████╗ ███████╗███╗   ██╗████████╗     ██████╗ ███████╗██████╗  ██████╗
  ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝    ██╔═══██╗██╔════╝██╔══██╗██╔═══██╗
  ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║       ██║   ██║█████╗  ██║  ██║██║   ██║
  ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║       ██║   ██║██╔══╝  ██║  ██║██║   ██║
  ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║       ╚██████╔╝███████╗██████╔╝╚██████╔╝
  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝        ╚═════╝ ╚══════╝╚═════╝  ╚═════╝
EOF
    printf "%b\n" "${NC}"
}

print_ok()    { printf " ${GREEN}✔${NC} %s\n" "$1"; }
print_info()  { printf "${GREEN}[INFO]${NC} %s\n" "$1"; }
print_warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
print_error() { printf "${RED}[ERROR]${NC} %s\n" "$1"; }

# --- Docker checks ---

check_docker() {
    if command -v docker >/dev/null 2>&1; then
        print_ok "Docker found"
    else
        print_warn "Docker not found. Installing..."
        curl -fsSL https://get.docker.com | sh
        if [ "$(uname -s)" = "Linux" ] && [ "$(id -u)" -ne 0 ]; then
            sudo usermod -aG docker "$USER"
            print_warn "Please log out and back in for group changes, then re-run this script."
            exit 1
        fi
    fi

    if ! docker info >/dev/null 2>&1; then
        print_warn "Docker daemon not running"
        if command -v systemctl >/dev/null 2>&1; then
            sudo systemctl start docker || true
        elif command -v service >/dev/null 2>&1; then
            sudo service docker start || true
        fi

        local waited=0
        while [ $waited -lt 30 ]; do
            if docker info >/dev/null 2>&1; then
                print_ok "Docker daemon is ready"
                break
            fi
            sleep 1
            waited=$((waited + 1))
            printf "."
        done
        if [ $waited -ge 30 ]; then
            print_error "Docker daemon did not start. Please start Docker manually."
            exit 1
        fi
    else
        print_ok "Docker daemon is running"
    fi

    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose"
    elif docker compose version >/dev/null 2>&1; then
        COMPOSE="docker compose"
    else
        print_error "Docker Compose not found. Please install it."
        exit 1
    fi
    print_ok "Docker Compose found ($COMPOSE)"
}

# --- Port helpers ---

is_port_in_use() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -i ":$port" -sTCP:LISTEN >/dev/null 2>&1
    elif command -v ss >/dev/null 2>&1; then
        ss -tuln 2>/dev/null | grep -q ":$port "
    elif command -v netstat >/dev/null 2>&1; then
        netstat -tuln 2>/dev/null | grep -q ":$port "
    else
        return 1
    fi
}

# --- Container name helpers ---

container_name_taken() {
    local name="$1"
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$name"
}

suggest_container_name() {
    local base="${1:-agent-zero}"
    local candidate="$base"
    local idx=2
    while container_name_taken "$candidate"; do
        candidate="${base}-${idx}"
        idx=$((idx + 1))
    done
    echo "$candidate"
}

find_free_port() {
    local base="${1:-5080}"
    local candidate="$base"
    local max=100
    local attempt=0
    while [ $attempt -lt $max ]; do
        if ! is_port_in_use "$candidate"; then
            echo "$candidate"
            return 0
        fi
        candidate=$((candidate + 1))
        attempt=$((attempt + 1))
    done
    echo "$base"
}

# --- Wait for ready ---

wait_for_ready() {
    local url="$1"
    local max_wait=120
    local waited=0
    printf "${GREEN}[INFO]${NC} Waiting for Agent Zero to become ready"
    while [ $waited -lt $max_wait ]; do
        local code
        code="$(curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
        if [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
            printf "\n"
            print_ok "Agent Zero is ready at $url"
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
        printf "."
    done
    printf "\n"
    print_warn "Agent Zero did not respond within ${max_wait}s. It may still be starting."
    return 1
}

# --- Main ---

main() {
    print_banner
    echo ""

    check_docker
    echo ""

    # Determine working directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$SCRIPT_DIR"

    # Check for existing compose file
    if [ ! -f "docker-compose.yml" ]; then
        print_error "docker-compose.yml not found in $SCRIPT_DIR"
        exit 1
    fi

    # Detect current Web UI port from compose or use default
    COMPOSE_PORT=$(grep -oP '(?<=\- ")\d+(?=:80")' docker-compose.yml 2>/dev/null || echo "5080")

    # Check if port is in use
    if is_port_in_use "$COMPOSE_PORT"; then
        local new_port
        new_port=$(find_free_port "$COMPOSE_PORT")
        if [ "$new_port" != "$COMPOSE_PORT" ]; then
            print_warn "Port $COMPOSE_PORT is already in use. Updating docker-compose.yml to use $new_port..."
            sed -i.bak "s/\"$COMPOSE_PORT:80\"/\"$new_port:80\"/" docker-compose.yml
            COMPOSE_PORT="$new_port"
        fi
    fi

    # Generate unique container name
    CONTAINER_NAME=$(suggest_container_name "agent-zero")
    if [ "$CONTAINER_NAME" != "agent-zero" ]; then
        print_warn "Container 'agent-zero' already exists. Using '$CONTAINER_NAME' instead."
    fi

    # Write compose override with dynamic container name
    cat > docker-compose.override.yml <<EOF
services:
  agent-zero:
    container_name: ${CONTAINER_NAME}
EOF

    # Build and start
    print_info "Building Agent Zero Docker image (this may take several minutes)..."
    $COMPOSE -f docker-compose.yml -f docker-compose.override.yml build --no-cache || \
        $COMPOSE -f docker-compose.yml -f docker-compose.override.yml build

    print_info "Starting Agent Zero..."
    $COMPOSE -f docker-compose.yml -f docker-compose.override.yml up -d

    # Wait for service
    wait_for_ready "http://localhost:$COMPOSE_PORT"

    echo ""
    print_ok "Agent Zero is running!"
    print_info "Web UI:     http://localhost:$COMPOSE_PORT"
    print_info "Container:  $CONTAINER_NAME"
    print_info "Data dir:   docker volume agent-zero-usr"
    print_info "Logs:       $COMPOSE -f docker-compose.yml -f docker-compose.override.yml logs -f agent-zero"
    echo ""
    print_info "Useful commands:"
    echo "  docker stop $CONTAINER_NAME                    # Stop"
    echo "  docker start $CONTAINER_NAME                   # Start"
    echo "  $COMPOSE -f docker-compose.yml -f docker-compose.override.yml down -v   # Stop and remove data"
}

main "$@"
