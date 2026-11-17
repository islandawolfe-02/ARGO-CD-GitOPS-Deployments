# ARGO-CD-GitOPS-Deployments

[![Deployment configuration checks](https://github.com/islandawolfe-02/ARGO-CD-GitOPS-Deployments/actions/workflows/check.yml/badge.svg)](https://github.com/islandawolfe-02/ARGO-CD-GitOPS-Deployments/actions/workflows/check.yml)

A shared deployment repository for applications running on minikube. Each app
builds and publishes its image to GHCR; this repository stores the deployment
settings that Argo CD applies to the cluster.

> **Scope.** Application repositories own image builds and smoke checks. This
> repository owns deployment configuration and Argo registration. GitHub runners
> do not need access to the Mac's Kubernetes API or its cluster credentials.

## Architecture

```mermaid
flowchart LR
  App[App repository: optional workflow] --> GHCR[App's GHCR package]
  App -->|verified digest and user inputs| Receiver[deploy.yml in this repository]
  Receiver --> Git[Deployment configuration on main]
  Git --> Parent[Parent Argo Application: cd-bootstrap]
  Parent --> Guard[Read-only ownership check]
  Guard --> Child[Register namespace, project and child Application]
  Child --> Namespace[Child Argo Application: app, Loki, Grafana]
  GHCR -->|Kubernetes image pull| Namespace
```

Each installation has its own namespace, Argo Application, Loki and Grafana.
A parent Application registers new installations automatically.

## Repository layout

| Path | Purpose |
|---|---|
| [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) | Receive requests, validate images and commit generated configuration |
| [`.github/workflows/check.yml`](.github/workflows/check.yml) | Run configuration tests on pull requests and pushes to `main` |
| `projects/<project>.json` | Register each app's source repository, listener, health endpoint and environments |
| `deployments/<project>/<namespace>/<deployment>/resources.json` | Desired state watched by the child Application |
| `deployments/<project>/<namespace>/<deployment>/record.json` | Public request details, source commit, image digest and deployment ID |
| `deployments/<project>/<namespace>/<deployment>/commands.txt` | Installation-specific verification and port-forward commands |
| `bootstrap/cd-*.json` | Ownership hook and namespace, AppProject and child Application registration |
| [`scripts/parent.py`](scripts/parent.py) | Preview or install the parent and its ownership-check resources |
| [`scripts/bootstrap.py`](scripts/bootstrap.py) | Register one installation manually when the parent is not used |
| [`tests/`](tests/) | Rendering and ownership checks without a live cluster |

## Prerequisites

You need Python 3, Docker Desktop, minikube, kubectl and an existing Argo CD
installation in the `argocd` namespace. Commands below run from this repository
in your Mac terminal, with Docker Desktop and minikube running.

Git is also needed to clone the repository. Existing applications in the
cluster can continue running alongside these installations.

## GitHub access

Configure GitHub access:

| Where | Setting |
|---|---|
| App repository | Add the Actions repository secret `CD_DISPATCH_TOKEN` |
| Token permissions | Grant **Actions: read and write** on `ARGO-CD-GitOPS-Deployments` |
| App's GHCR package | Set visibility to **Public**; publish ARM64 and AMD64 images |
| Deployment repository | Allow its workflow to push generated configuration to `main` |

Keep tokens and cluster credentials out of Git. Restrict write access to trusted
maintainers, because this repository controls Argo Applications and Projects.

## Quick start (fresh clone)

Clone the deployment repository and enter it:

```bash
git clone https://github.com/islandawolfe-02/ARGO-CD-GitOPS-Deployments.git
cd ARGO-CD-GitOPS-Deployments
```

### Connect Argo once per cluster

Preview the parent configuration:

```bash
python3 scripts/parent.py > /tmp/argo-cd-parent.json
cat /tmp/argo-cd-parent.json
```

Then install it:

```bash
python3 scripts/parent.py --install
kubectl --context minikube -n argocd get application cd-bootstrap
```

The parent watches `bootstrap/` on `main`. Before registering an installation,
a read-only check verifies resource ownership; conflicts stop registration.
Resources belonging to other installations are not adopted, and automatic
deletion is disabled.

Run the installer again when updating the installed ownership-check code or
permissions. Ordinary deployments do not need another install or manual apply.

### Deploy an app

1. Open the app repository's optional workflow in **GitHub Actions**.
2. Select **Deploy: Yes**. Select **Build image: Yes** for a new build, or
   **Build image: No** and supply an existing image reference:

   ```text
   ghcr.io/<owner>/<image>@sha256:<digest>
   ```

3. Enter the namespace, deployment name and three unused local ports. Keep the
   same namespace and deployment name when updating an existing installation.
4. Follow **Actions → Record deployment request** in this repository. It checks
   the inputs and public image, then saves the generated configuration.
5. Argo registers the installation and deploys the app, Loki and Grafana.
   The run summary provides the deployment ID and access commands.

Namespace and deployment names must contain **1–63 lowercase letters, digits
or hyphens**, and start and end with a letter or digit. Use `demo-1`;
`demo_1`, uppercase letters and spaces are invalid.

Each installation needs its own namespace and distinct local ports. System and
protected namespaces are rejected, along with reserved local ports `18080`,
`18081`, `18082`, `3000` and `3100`.

| Component | Default local port |
|---|---|
| App | `18090` |
| Grafana | `3001` |
| Loki | `3101` |

The local ports must be between `1024` and `65535` and available on your Mac.
Container and Service ports can repeat across namespaces; the forwarded local
ports must remain distinct.

## Startup and verification

Use the exact commands from the workflow summary or the installation's
`commands.txt`. Check that its Argo Application is **Synced/Healthy** and its
Pods are ready, then start each port-forward in a separate terminal.

With the default local ports, open the app at **http://127.0.0.1:18090** and
Grafana at **http://127.0.0.1:3001/d/cd-requests**. If the app sends request logs
to Loki, match a request ID from the app with its Grafana row.

A successful workflow means the configuration was saved. Check the child
Application and app response to confirm that deployment finished.

| Check | Expected result |
|---|---|
| Parent Application `cd-bootstrap` | Synced/Healthy, with no failed ownership hook |
| Child Application from the run summary | Synced/Healthy at the intended configuration revision |
| App, Loki and Grafana Pods | Ready and Running in the selected namespace |
| Service EndpointSlices | Ready endpoint addresses match the corresponding Pod IPs |
| App Deployment image | Digest reference matches the installation's `record.json` |
| Browser and Grafana | App responds; any shipped request logs can be found by request ID |

## Build and image identity

Images are built and checked in the app repository, then published to its GHCR
package. The deployment request records the source commit and a multi-platform
image digest; Kubernetes pulls that image from GHCR. No manual image load into
minikube is needed for this route.

The registry check requires Linux AMD64 and ARM64 manifests. A running Pod's
`imageID` can identify its platform-specific image rather than the parent
multi-platform digest, so these values need not be identical. Keep the source
commit, requested image reference and configuration revision distinct.

## Add another app

Create `projects/<project>.json` with that app's settings, for example:

```json
{
  "source_repository": "your-account/your-app",
  "container_port": 8081,
  "health_path": "/healthz",
  "environments": ["development"],
  "environment": {}
}
```

The image must support the configured listener and health endpoint. The app
provides its own log shipping; this repository does not add application code.
Its publishing workflow must send the inputs declared in
[deploy.yml](.github/workflows/deploy.yml), including the project name, source
commit, image digest, environment and destination settings.

The request fields are:

| Input | Meaning |
|---|---|
| `project` | Registered profile name, without `.json` |
| `source_repository` | App repository as `owner/repository`, matching the profile |
| `source_revision` | Full 40-character lowercase source commit SHA |
| `image` | App's own public GHCR package, pinned with `@sha256:<64 lowercase hex characters>` |
| `namespace`, `deployment_name` | Destination and installation identity; retain both for updates |
| `app_port`, `grafana_port`, `loki_port` | Three distinct, unused local port-forward ports |
| `lab_env` | An environment allowed by the registered profile |

The app's workflow supplies these inputs when dispatching `deploy.yml`. Its
build and smoke checks remain the app repository's responsibility; the receiver
validates the public image index and request, rather than repeating those checks.

Generated files are stored in:

```text
projects/                                      App profiles
deployments/<project>/<namespace>/<deployment>/ App settings and access commands
bootstrap/                                     Argo registration files
scripts/                                       Setup, rendering and ownership checks
tests/                                         Local checks
```

## Troubleshooting

| Problem | Check |
|---|---|
| Missing `CD_DISPATCH_TOKEN` | Store the secret in the **app repository** |
| Invalid namespace or deployment name | Use lowercase letters, digits and hyphens; replace `_` with `-` |
| Port already assigned | Choose unused ports for the new installation |
| Image pull fails | Check package visibility, digest and supported architecture |
| Registration fails | Inspect the parent Application and ownership-check Job logs |

```bash
kubectl --context minikube -n argocd get application cd-bootstrap
kubectl --context minikube -n argocd get jobs
kubectl --context minikube -n argocd logs job/<failed-job-name>
```

Resolve ownership conflicts rather than bypassing the checks. Retry a full
parent sync after correcting the problem; selective sync skips ownership hooks.

The cluster must be running for Argo to deploy. GitHub queue delays can postpone
the configuration update, and local browser access needs running port-forwards.

## Recovery and manual registration

To roll back, deploy a previously verified image digest using the same namespace
and deployment name. Changing those names creates another installation;
deleting configuration files does not remove cluster resources.

Without the parent, manual registration remains available:

```bash
python3 scripts/bootstrap.py <deployment-id>
```

Loki storage is ephemeral; retain any log evidence you need outside the Pod.

## Local checks

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

These tests check rendering and ownership rules without connecting to Kubernetes.
