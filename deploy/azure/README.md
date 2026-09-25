# Azure Container Apps deployment

`deploy.sh` upgrades an existing Azure Container App in place. It builds the
Docker image in Azure Container Registry, creates a new Container Apps revision,
waits for it to become healthy, and verifies the public health, OAuth metadata,
and unauthenticated MCP boundary.

The target Container App must already have:

- external ingress targeting port `8000`;
- Azure Container Apps authentication (Easy Auth) enabled;
- `TASTYTRADE_CLIENT_SECRET` and `TASTYTRADE_REFRESH_TOKEN` populated through
  Container Apps secret references, not literal values;
- `MCP_PUBLIC_HOST`, `ENTRA_TENANT_ID`, and `MCP_API_APP_ID`;
- registry pull access to the target Azure Container Registry.

Easy Auth is intentionally configured to allow anonymous requests at the
platform boundary so `/healthz` and OAuth protected-resource metadata remain
public. The application itself requires Easy Auth's injected
`X-MS-CLIENT-PRINCIPAL-ID` header on `/mcp`.

Safe deployment defaults keep live trading tools unregistered and force all
orders to dry-run:

```text
ENABLE_LIVE_TRADING=false
FORCE_DRY_RUN=true
```

Run with the default resource names:

```bash
./deploy/azure/deploy.sh
```

Override resource names or the image tag when needed:

```bash
AZURE_RESOURCE_GROUP=rg-trading-mcp \
AZURE_CONTAINER_APP=tastytrade-mcp \
AZURE_ACR_NAME=hplintradingmcp \
AZURE_IMAGE_TAG=py-v1 \
./deploy/azure/deploy.sh
```

Builds run in ACR by default. If the remote builder or its upstream registry is
temporarily unavailable, the script falls back to the local Docker daemon. To
select local build and push explicitly:

```bash
AZURE_BUILD_MODE=local ./deploy/azure/deploy.sh
```

The script never reads or prints secret values.
