# Cluster resource reduction

The original test encountered API timeouts, liveness restarts and high memory
usage. At the owner's request, the other application workloads were stopped.

- Kept the app, Loki and Grafana in `cd-e2e-20261117-182740`.
- Kept Argo CD and the Kubernetes system workloads.
- Scaled twelve older Deployments to zero, removing thirteen application Pods.
- Paused reconciliation on their five Argo Applications with the
  `argocd.argoproj.io/skip-reconcile` annotation.
- Retained Deployment definitions, namespaces and Services; no Git configuration
  was changed by this intervention.
- The retained app became Ready again on its automatic retry after cleanup.

The stopped namespaces are `demo-app`, `observability`, `order-dev`,
`order-prod`, `order-test` and `p1-islandawolfe-02`.

`changes.json` records the original replica counts from the cluster inventory
and the operations performed. Some Pods were recreated during the initial pause
transition and were scaled down again. `pods-after.json`, `argo-after.txt` and
`memory-after.txt` record the final observations. Docker memory usage was about
97% before cleanup; the later observation is in `memory-after.txt`.

When restoring an older workload, use its recorded replica count and remove the
pause annotation from its Argo Application when ready to resume reconciliation.
The standalone observability Deployments need their replica counts restored
manually. Paused Application status can remain stale while reconciliation is
stopped. Initial failure evidence and smoke results were retained separately.

This cleanup is an intervention during the test, not a clean first-pass success
or a sustained stability test.
