# Running on Kubernetes

Watchtower includes a Helm chart in [`deploy/helm/watchtower`](../deploy/helm/watchtower). It runs the same
pieces as the Compose setup, described in [Deploying to production](deployment.md), as Kubernetes resources:

- the **controller** as a Deployment (two replicas by default; one schedules at a time, all serve the dashboard
  and API), with a Service and an optional Ingress;
- **workers** as a StatefulSet. Each pod holds a worker and a MediaMTX container, which share the pod's network as
  the MediaMTX configuration expects, plus a volume for the worker's identity and its waiting results;
- optionally the bundled **token server**, as a StatefulSet with a volume.

**MongoDB and Redis are not part of the chart.** Use a managed service or an operator you already run. Redis is
only needed with more than one controller replica.

!!! info "Images"
    The chart pulls `ghcr.io/naumanhsa/watchtower-controller`, `-worker` and `-token-server`, which are published for
    each release (the first one is 2.0.0). To run code that isn't released, build the three images from the
    repository root (`docker build -f controller/Dockerfile -t <your registry>/watchtower-controller:<tag> .`, and the
    same for `worker` and `token-server`), push them, and set `image.registry`, `image.owner` and `image.tag`.

!!! note
    The chart is checked in CI (lint, rendering, validation against the Kubernetes schemas) but has not yet been run
    on many real clusters. If something needs adjusting for yours, please open an issue.

## Step by step

### 1. Create the namespace and the secrets

Generate an admin token and a worker token, and keys for the token server:

```bash
kubectl create namespace watchtower

ADMIN_TOKEN=$(openssl rand -base64 32 | tr '+/' '-_')
WORKER_TOKEN=$(openssl rand -base64 32 | tr '+/' '-_')

kubectl -n watchtower create secret generic wt-token-server \
  --from-literal=TOKEN_SERVER_BOOTSTRAP_TOKENS="admin:$ADMIN_TOKEN:first-admin,worker:$WORKER_TOKEN:k8s-workers" \
  --from-literal=TOKEN_SERVER_ADMIN_KEY="$(openssl rand -hex 32)" \
  --from-literal=TOKEN_SERVER_MASTER_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"

kubectl -n watchtower create secret generic wt-worker --from-literal=WATCHTOWER_TOKEN="$WORKER_TOKEN"

echo "Sign in with: $ADMIN_TOKEN"
```

The master key is 32 random bytes in URL-safe base64, which is exactly what the `openssl` line produces.

If your MongoDB address contains a password, put it in a secret too, for example
`kubectl -n watchtower create secret generic wt-controller --from-literal=MONGO_URI=mongodb://user:pass@mongo:27017`,
and use `controller.existingSecret: wt-controller` below instead of `controller.mongoUri`.

### 2. Write your values file

Save this as `watchtower-values.yaml` and adjust it:

```yaml
controller:
  replicas: 2
  mongoUri: mongodb://mongo.database.svc:27017
  redisUrl: redis://redis.database.svc:6379/0
  env:
    AUTH_PROVIDER: http
    RESULTS_URL: https://your-app.example.com/watchtower/events
    LOG_FORMAT: json
  ingress:
    enabled: true
    className: nginx
    host: watchtower.example.com
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt
    tls:
      - secretName: watchtower-tls
        hosts: [watchtower.example.com]

tokenServer:
  enabled: true
  existingSecret: wt-token-server

worker:
  replicas: 3
  maxStreams: 4
  tokenSecret:
    name: wt-worker
  hostNetwork: true
  env:
    DELIVERY_MODE: realtime
    REQUIRE_ENCRYPTION: "true"
```

Any setting from the [configuration reference](configuration.md) can be added under `controller.env`,
`worker.env` or `tokenServer.env`. The full list of chart values, with comments, is in
[`values.yaml`](../deploy/helm/watchtower/values.yaml).

### 3. Install

From the repository root:

```bash
helm install wt deploy/helm/watchtower -n watchtower -f watchtower-values.yaml
kubectl -n watchtower get pods
```

When the pods are running, open `https://watchtower.example.com` and sign in with the admin token from step 1.
The workers appear on the **Workers** page, named after their pods (`wt-watchtower-worker-0` and so on).

Issue more tokens with the token server's command line inside its pod:

```bash
kubectl -n watchtower exec statefulset/wt-watchtower-token-server -- \
  watchtower-token-server issue --role operator --subject alice@example.com
```

## Live view on Kubernetes

Analysis works however your cluster's network is set up. Live view is the part that needs thought, because video
travels over UDP straight from a worker pod to the viewer's browser.

**The simple way: `worker.hostNetwork: true`.** Each worker pod uses its node's network, advertises the node's IP
address to viewers, and viewers connect to the node directly on 8889/tcp and 8189/udp+tcp. Only one worker pod can
run per node this way, and those ports must be reachable on the nodes.

**Otherwise:** expose each worker's 8889/tcp and 8189/udp+tcp yourself (a LoadBalancer or NodePort Service per
worker pod) and set `worker.publicWebrtcUrl` and `worker.publicHost` to the exposed address, or add a TURN relay
through `worker.env` (`MTX_WEBRTCICESERVERS2_0_URL` and friends, see [Workers](workers.md#viewers-behind-strict-firewalls)).

**With an HTTPS dashboard** (the normal case), browsers require an HTTPS live view address too. Give the worker's
port 8889 an HTTPS address through your ingress or a load balancer that terminates TLS, as a separate route from the
dashboard, and set `worker.publicWebrtcUrl` to it. Never add a proxy container *inside* the worker pod: MediaMTX
trusts connections from inside the pod without a token.

## Scaling and upgrades

- **More workers:** raise `worker.replicas` and run `helm upgrade`. Each new pod registers as a new worker.
- **Fewer workers:** lower `worker.replicas`. Kubernetes stops the highest-numbered pods; a stopping worker drains
  itself, so its cameras move before it exits. Forget the removed workers on the **Workers** page afterwards.
- **Upgrades:** change the chart version or `image.tag` and run `helm upgrade`. Controllers and workers of
  neighbouring versions work together, so a rolling upgrade is safe; upgrade controllers first when the release
  notes say the protocol changed.

## Using SSO on Kubernetes

Put the `OIDC_*` settings in `controller.env` (they are not secret) and set `AUTH_PROVIDER: oidc,http` so people sign
in through your identity provider while workers keep using the token server. See
[Users and sign-in](access.md#using-single-sign-on).
