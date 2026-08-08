# k3s deployment

Manifests for the single-node k3s demo (PRD week 10). Applied in order:

```bash
kubectl apply -k ops/k8s
kubectl -n aletheia rollout status deploy/gateway
```

## What is deliberately not here

**Secrets are not in git.** `secret.example.yaml` shows the shape; the real one is
created out of band:

```bash
kubectl -n aletheia create secret generic aletheia \
  --from-literal=postgres-password="$(openssl rand -base64 24)" \
  --from-literal=tenants='demo:REPLACE:0.05'
```

**No autoscaling.** The PRD targets one Hetzner node, and an HPA on a single node
adds a moving part that cannot help. The verifier is the component that would
need it first (see `eval/results/week8-latency.md`).

**No pod-level Postgres HA.** A single-region demo with `%99` availability (PRD
§4.2) does not justify it, and Patroni on one node is theatre.

## Choices worth knowing about

**The calibration job is a CronJob, not a sidecar.** It drives the whole pipeline
over a labelled dataset and writes one row; running it beside a service would tie
its schedule to that service's lifecycle and give it a share of the request path's
resources.

**Readiness and liveness differ per service.** Retrieval and risk hold database
connections and report not-ready without one; generation and the verifier hold
their models in-process and are ready as soon as they are up. Liveness never
depends on a dependency for any of them — a slow database must not get pods
restart-looped (ADR-0001).

**The verifier gets the models image and the largest memory request.** mDeBERTa is
roughly 1.1 GB resident, and a pod that gets OOM-killed mid-verification produces
an abstention, which looks like a corpus problem rather than an infrastructure one.

**`terminationGracePeriodSeconds` exceeds the request budget** on the gateway, so
a rolling update drains in-flight answers instead of turning them into dropped
connections.
