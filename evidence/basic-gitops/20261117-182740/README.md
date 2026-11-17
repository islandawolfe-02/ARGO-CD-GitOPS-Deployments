# Basic GitOps deployment test

- Namespace: cd-e2e-20261117-182740
- Installation: foundation-e2e
- Argo Application: cd-0c71e7859fdc6cc9
- Source commit: ac66c172bfde067ba456318e24d88ef2999c16a6
- Environment: workshop-green
- App workflow: https://github.com/islandawolfe-02/Basic-GitOps-using-Argo-CD-and-Minikube/actions/runs/44088356141
- Deployment workflow: https://github.com/islandawolfe-02/ARGO-CD-GitOPS-Deployments/actions/runs/44088722610
- Browser request ID: e2ba53f1-cb88-43e8-a6d6-2e1052a06a44

## Results

- AMD64 and ARM64 registry smoke checks: PASS; both saved registry smoke reports end with `RESULT: PASS`.
- Request matches saved deployment record: PASS; all inputs in `publish-artifact/deployment-request.json` match `record.json`.
- Automatic Argo registration: PASS; `cd-bootstrap` registered the child Application without a manual per-installation apply.
- Child Application Synced/Healthy: PASS after resource cleanup; see [saved Argo status](resource-reduction/argo-after.txt).
- App, Loki and Grafana ready: PASS after resource cleanup; all three were Running and Ready. The app had nine restarts; see [saved Pod status](resource-reduction/pods-after.json).
- Endpoint IPs match Pod IPs: PASS; ready app, Loki and Grafana endpoint IPs match their Pod IPs ([verification](workload-verification.json)).
- Deployed image matches record: PASS; the live app Deployment uses the exact GHCR digest in `record.json` ([verification](workload-verification.json)).
- Cluster smoke checks: PASS on the diagnostic rerun; the initial run failed with API responses of `000`. Both attempts are retained in `cluster-smoke.txt` and `cluster-smoke-rerun/`.
- Browser, Pod logs and Grafana request ID match: PARTIAL; browser JSON, Grafana and Loki match `e2ba53f1-cb88-43e8-a6d6-2e1052a06a44`. The saved Pod log lacks it because the request predates the available container log windows; see [restart verification](request-log-check/verification.json).
- Existing deployments remain present: PASS for Deployment definitions; twelve older Deployments were intentionally scaled to zero and their thirteen Pods removed during [resource cleanup](resource-reduction/README.md).

## Request-log verification

Checked the current and previous app container logs after nine restarts. The
original request was recorded by Loki at `2026-11-17T13:32:54.4149132Z`. The
available previous container started at `13:45:30Z`, and the current container
started at `13:56:14Z`, both later than that request. Neither available log
contains its request ID. This confirms that the saved Pod-log window is too
recent for the original request; its earlier stdout line could not be recovered
through the current or previous container log API.

The raw logs and timestamp checks are retained in `request-log-check/`.
The original browser JSON, Grafana screenshot, Loki response and Pod log were
kept unchanged. The original three-way match remains partial.

## Scope

Fresh application and deployment checkouts, a newly built registry image,
and a new namespace on the existing personal minikube cluster.
Cluster creation and Argo CD installation were not repeated.

The test required resource cleanup after startup restarts and API timeouts.
Older application workloads were stopped at the owner's request; their Argo
reconciliation was paused. This was not a clean first-pass success, and sustained
stability has not been tested.
