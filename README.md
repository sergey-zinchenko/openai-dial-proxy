# openai-dial-proxy

OpenAI-compatible HTTP in front of [AI DIAL Core](https://github.com/epam/ai-dial-core). Clients that speak `/v1/chat/completions` keep that URL. The proxy forwards to Core's Azure-shaped deployment routes.

Apache-2.0. Independent project. Not an EPAM product.

Image: `ghcr.io/sergey-zinchenko/openai-dial-proxy`

The GitHub repository and the GHCR package are private for now. Pulling the image needs a registry secret until the package is made public. After that, delete `image.pullSecrets` from the values file you use.

## What it maps

| In (OpenAI) | Out (DIAL Core) |
|-------------|-----------------|
| `POST /v1/chat/completions` with JSON `model` | `POST /openai/deployments/{model}/chat/completions?api-version=` |
| `POST /v1/embeddings` | `POST /openai/deployments/{model}/embeddings?api-version=` |
| `GET /v1/models` | `GET /openai/models?api-version=` (`MODELS_UPSTREAM_PATH`) |

`api-version` comes from the query string, then the `api-version` header, then `DEFAULT_API_VERSION` (`2024-10-21`).

Auth: `Api-Key` is forwarded as `Api-Key`. A JWT `Authorization: Bearer` stays `Authorization`. Any other bearer token is sent as `Api-Key`. Do not put a DIAL key in Helm values. The client sends its own key.

Provider prefixes on the model id (`openai/`, `azure/`, `litellm/`, `dial/`) are stripped only on the upstream path. The JSON body is forwarded as received, except lone UTF-16 surrogates (a truncated emoji) which are replaced with U+FFFD so downstream Python services can encode UTF-8. A clean body is forwarded byte-identical.

httpx decodes gzip from Core. The proxy drops `content-encoding` and `content-length` so the client does not gunzip plaintext.

`GET /health` is the probe. It is not a public API.

## Deploy with dial-extension 3.1.1

Chart: [`dial/dial-extension`](https://github.com/epam/ai-dial-helm/tree/main/charts/dial-extension) `3.1.1` from `https://charts.dialx.ai`.

That chart already has both:

- `ingress` (`networking.k8s.io/v1`)
- `httpRoute` (Gateway API, since chart 3.1.0)

Use one. The chart default for both is a prefix of `/`. That would steal Core routes such as `/v1/ops`, `/v1/applications`, and `/v1/toolsets`. The examples pin **Exact** matches for the three paths above.

```bash
helm repo add dial https://charts.dialx.ai
helm repo update
helm search repo dial/dial-extension --version 3.1.1
```

### Private image pull

While the package is private, create a secret in the DIAL namespace. A classic PAT with `read:packages` is enough.

```bash
kubectl -n dial create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=YOUR_GITHUB_USER \
  --docker-password=YOUR_GITHUB_TOKEN
```

When you make the package public, remove `image.pullSecrets` and delete the secret.

### Ingress

[`deploy/values-ingress.yaml`](deploy/values-ingress.yaml). `ingress-nginx` class name `nginx`. Edit the host, TLS secret, and `DIAL_UPSTREAM`.

The chart writes one Exact path from `ingress.path` and the other two from `ingress.extraPaths`. Service port is 80. The container listens on 8080.

```bash
helm upgrade --install openai-dial-proxy dial/dial-extension \
  --version 3.1.1 \
  --namespace dial \
  --create-namespace \
  -f deploy/values-ingress.yaml
```

Long completions need a long proxy read timeout. The example sets nginx `proxy-read-timeout` and `proxy-send-timeout` to 900 seconds and turns buffering off so SSE is not held.

### Gateway API

[`deploy/values-httproute.yaml`](deploy/values-httproute.yaml). Set `httpRoute.parentRefs` to your Gateway (name, namespace, listener `sectionName`). The placeholder is `public` / `gateway` / `https`.

Empty `httpRoute.rules` makes the chart emit `PathPrefix /`. The example sets three Exact rules and a 900 second request timeout. `ingress.enabled` stays false.

```bash
helm upgrade --install openai-dial-proxy dial/dial-extension \
  --version 3.1.1 \
  --namespace dial \
  --create-namespace \
  -f deploy/values-httproute.yaml
```

Confirm the Gateway implementation accepts `HTTPRoute.spec.rules[].timeouts`. If it drops unknown fields, raise the timeout on the Gateway or on a policy that your controller actually honors.

### Shared settings

| Value | Why |
|-------|-----|
| `containerPorts.http: 8080` | Process listens here. The chart Service stays on port 80. |
| `readOnlyRootFilesystem` + `temporary` `/tmp` | Image runs as uid 1001 and needs a writable temp dir. |
| `DIAL_UPSTREAM` | In-cluster Core, for example `http://dial-core.dial.svc.cluster.local`. |
| `REQUEST_TIMEOUT` | Upstream timeout in seconds. Match it to the edge timeout. |
| `networkPolicy.allowExternalEgress: true` | So the proxy can reach Core. Tighten this if a namespace policy already allows that traffic. |

After the image workflow is green, pin `image.digest` (it overrides `tag`):

```bash
docker buildx imagetools inspect ghcr.io/sergey-zinchenko/openai-dial-proxy:latest
```

## Client

```json
{
  "baseUrl": "https://dial.example.com/v1",
  "apiKey": "<dial-api-key>",
  "api": "openai-completions"
}
```

`baseUrl` must be the host plus `/v1`, not the Core `/openai` prefix.

## Develop

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

Runtime third-party notices are in [`NOTICE`](NOTICE). `scripts/check_licenses.py` fails CI if that list drifts.

## Image build

GitHub Actions builds `python:3.12-slim` from Docker Hub and installs from PyPI. Nothing in the Dockerfile talks to a private registry. The workflow pushes to GHCR with `GITHUB_TOKEN` on `main` and on `v*` tags, then runs Trivy (fail on unfixed HIGH and CRITICAL) and uploads an SPDX SBOM.
