#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

RESOURCE_GROUP=${AZURE_RESOURCE_GROUP:-rg-trading-mcp}
CONTAINER_APP=${AZURE_CONTAINER_APP:-tastytrade-mcp}
ACR_NAME=${AZURE_ACR_NAME:-hplintradingmcp}
IMAGE_REPOSITORY=${AZURE_IMAGE_REPOSITORY:-tastytrade-mcp}
IMAGE_TAG=${AZURE_IMAGE_TAG:-"py-$(date -u +%Y%m%d%H%M%S)"}
BUILD_MODE=${AZURE_BUILD_MODE:-acr}

for command in az curl; do
    command -v "$command" >/dev/null || {
        echo "Required command not found: $command" >&2
        exit 1
    }
done

az account show --only-show-errors >/dev/null
az containerapp show \
    --only-show-errors \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP" \
    >/dev/null

env_names=$(az containerapp show \
    --only-show-errors \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP" \
    --query 'properties.template.containers[0].env[].name' \
    --output tsv)

for name in \
    TASTYTRADE_CLIENT_SECRET \
    TASTYTRADE_REFRESH_TOKEN \
    MCP_PUBLIC_HOST \
    ENTRA_TENANT_ID \
    MCP_API_APP_ID
do
    grep -Fxq "$name" <<<"$env_names" || {
        echo "Container App is missing required environment setting: $name" >&2
        exit 1
    }
done

for name in TASTYTRADE_CLIENT_SECRET TASTYTRADE_REFRESH_TOKEN; do
    secret_ref=$(az containerapp show \
        --only-show-errors \
        --resource-group "$RESOURCE_GROUP" \
        --name "$CONTAINER_APP" \
        --query "properties.template.containers[0].env[?name=='$name'].secretRef | [0]" \
        --output tsv)
    if [[ -z "$secret_ref" ]]; then
        echo "$name must use a Container Apps secret reference." >&2
        exit 1
    fi
done

auth_enabled=$(az containerapp auth show \
    --only-show-errors \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP" \
    --query 'platform.enabled' \
    --output tsv)
if [[ "$auth_enabled" != "true" ]]; then
    echo "Azure Container Apps authentication must be enabled." >&2
    exit 1
fi

login_server=$(az acr show \
    --only-show-errors \
    --name "$ACR_NAME" \
    --query loginServer \
    --output tsv)
image="$login_server/$IMAGE_REPOSITORY:$IMAGE_TAG"

build_locally() {
    command -v docker >/dev/null || {
        echo "Docker is required for AZURE_BUILD_MODE=local." >&2
        return 1
    }
    az acr login --only-show-errors --name "$ACR_NAME" >/dev/null
    docker build --tag "$image" "$ROOT_DIR"
    docker push "$image"
}

case "$BUILD_MODE" in
    acr)
        if ! az acr build \
            --only-show-errors \
            --registry "$ACR_NAME" \
            --image "$IMAGE_REPOSITORY:$IMAGE_TAG" \
            --file "$ROOT_DIR/Dockerfile" \
            "$ROOT_DIR"
        then
            echo "ACR build failed; retrying with the local Docker daemon." >&2
            build_locally
        fi
        ;;
    local)
        build_locally
        ;;
    *)
        echo "AZURE_BUILD_MODE must be 'acr' or 'local'." >&2
        exit 1
        ;;
esac

az containerapp update \
    --only-show-errors \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP" \
    --image "$image" \
    --set-env-vars \
        MCP_HTTP_HOST=0.0.0.0 \
        MCP_HTTP_PORT=8000 \
        MCP_REQUIRE_AZURE_AUTH=true \
        ENABLE_LIVE_TRADING=false \
        FORCE_DRY_RUN=true \
    --output none

revision=$(az containerapp show \
    --only-show-errors \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP" \
    --query properties.latestRevisionName \
    --output tsv)

for _ in $(seq 1 60); do
    health=$(az containerapp revision show \
        --only-show-errors \
        --resource-group "$RESOURCE_GROUP" \
        --revision "$revision" \
        --query properties.healthState \
        --output tsv)
    if [[ "$health" == "Healthy" ]]; then
        break
    fi
    sleep 5
done

if [[ "$health" != "Healthy" ]]; then
    echo "Revision $revision did not become healthy." >&2
    exit 1
fi

fqdn=$(az containerapp show \
    --only-show-errors \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CONTAINER_APP" \
    --query properties.configuration.ingress.fqdn \
    --output tsv)

curl --fail --silent --show-error "https://$fqdn/healthz" >/dev/null
curl --fail --silent --show-error \
    "https://$fqdn/.well-known/oauth-protected-resource" \
    >/dev/null

mcp_status=$(curl \
    --silent \
    --output /dev/null \
    --write-out '%{http_code}' \
    --request POST \
    --header 'Content-Type: application/json' \
    --data '{}' \
    "https://$fqdn/mcp")
if [[ "$mcp_status" != "401" ]]; then
    echo "Expected unauthenticated /mcp to return 401, got $mcp_status." >&2
    exit 1
fi

printf 'Deployed %s\nRevision: %s\nURL: https://%s/mcp\n' \
    "$image" "$revision" "$fqdn"
