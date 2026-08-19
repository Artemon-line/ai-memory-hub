# K3s Local Bearer Stack

This example runs ai-memory-hub on Kubernetes/K3s with:

- Postgres metadata storage
- PGVector vector storage
- bearer-token auth
- private in-cluster Postgres
- no public tunnel
- OpenTelemetry disabled by default

K3s is Kubernetes, so these manifests use ordinary `kubectl` commands. On a K3s
node, `sudo k3s kubectl ...` is equivalent to `kubectl ...` when you have not
copied the kubeconfig to your user account.

## Files

- `Containerfile`: hub image with the `postgres` and `tokenizer` extras.
- `config.bearer.yaml`: readable config template for the mounted hub config.
- `k8s.yaml`: namespace, secrets template, Postgres, PGVector, hub deployment,
  and internal services.

The Kubernetes Secret embeds `config.yaml` because the current config uses full
Postgres DSNs. Do not put rendered secrets in git.

## Build The Image

Build the image on the K3s node, then import it into K3s containerd:

```bash
docker build -t ai-memory-hub:k3s-local -f examples/k3s-local/Containerfile .
docker save ai-memory-hub:k3s-local | sudo k3s ctr images import -
```

If you build on another machine, push the image to a registry your Pi can pull
from and change `image: ai-memory-hub:k3s-local` in `k8s.yaml`.

## Render Secrets

Render the manifest with local-only generated secrets:

```bash
cd examples/k3s-local
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
TOKEN_HASH_SECRET="$(openssl rand -hex 48)"
sed \
  -e "s/REPLACE_POSTGRES_PASSWORD/${POSTGRES_PASSWORD}/g" \
  -e "s/REPLACE_TOKEN_HASH_SECRET/${TOKEN_HASH_SECRET}/g" \
  k8s.yaml > k8s.rendered.yaml
```

Keep `k8s.rendered.yaml` private. It contains the Postgres password, token hash
secret, and mounted hub config.

## Deploy

```bash
sudo k3s kubectl apply -f k8s.rendered.yaml
sudo k3s kubectl -n ai-memory-hub rollout status deploy/postgres
sudo k3s kubectl -n ai-memory-hub rollout status deploy/ai-memory-hub
sudo k3s kubectl -n ai-memory-hub get pods,svc,pvc
```

Check readiness from the Pi:

```bash
sudo k3s kubectl -n ai-memory-hub port-forward svc/ai-memory-hub 8000:8000
curl -fsS http://127.0.0.1:8000/ready
```

## Create A User And Bearer Token

Run the admin CLI inside the hub pod. The token is printed once.

```bash
sudo k3s kubectl -n ai-memory-hub exec deploy/ai-memory-hub -- \
  /app/.venv/bin/aim admin user create tyran \
  --config /app/config.yaml \
  --display-name "Tyran"

sudo k3s kubectl -n ai-memory-hub exec deploy/ai-memory-hub -- \
  /app/.venv/bin/aim admin token create \
  --config /app/config.yaml \
  --user tyran \
  --display-name "laptop-agents"
```

Use that token from clients:

```bash
export AMH_TOKEN="amh_..."
curl -fsS http://127.0.0.1:8000/memory/search \
  -H "Authorization: Bearer ${AMH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"query":"hello","top_k":3}'
```

## Reach It From A Laptop

Start private. For one-off testing, SSH or `kubectl port-forward` is safest.

For LAN/VPN access from laptop agents, expose only the hub service:

```bash
sudo k3s kubectl -n ai-memory-hub patch svc ai-memory-hub \
  -p '{"spec":{"type":"LoadBalancer"}}'

sudo k3s kubectl -n ai-memory-hub get svc ai-memory-hub
```

Then point agents at:

```text
http://<pi-lan-ip>:8000/mcp/
```

Every client request must include:

```text
Authorization: Bearer <token>
```

Postgres remains `ClusterIP` and should not be exposed outside the cluster.

## Optional: Desktop Ollama Embeddings

The default config uses deterministic local embeddings so the Pi stack is light.
To use Ollama on another LAN machine, edit the embedded `config.yaml` in
`k8s.yaml` before rendering:

```yaml
providers:
  embeddings: http
  embedding_model: nomic-embed-text
  embedding_dimension: 768
  metadata_db: postgres
  vector_db: pgvector
  agent: mvp

embedding_endpoint:
  base_url: http://<desktop-lan-ip>:11434/v1
  api_key: ollama
```

On the Ollama host, bind Ollama to the LAN interface and firewall it to trusted
devices only. Pull the model before starting the hub:

```bash
ollama pull nomic-embed-text
```

After changing embedding dimensions for existing data, use a fresh PGVector
table or run a planned reindex flow.

## Optional: Observability

The default K3s config keeps tracing and metrics disabled. That is intentional
for a Pi-sized always-on node. Start with:

```text
GET /health
GET /ready
GET /observability
kubectl logs -n ai-memory-hub deploy/ai-memory-hub
```

If you want OpenTelemetry later, build the image with the optional extra and add
an OTel Collector deployment:

```bash
docker build \
  -t ai-memory-hub:k3s-local-otel \
  -f examples/k3s-local/Containerfile \
  --build-arg OPTIONAL_EXTRAS="--extra observability" \
  .
```

Then set `observability.tracing.enabled: true` and/or
`observability.metrics.enabled: true` in the mounted config and point both
endpoints at the collector service. Keep Jaeger and Prometheus off the Pi until
you know you need them.

## Cleanup

```bash
sudo k3s kubectl delete namespace ai-memory-hub
```

This removes the namespace and its PVCs. Back up first if the data matters.
