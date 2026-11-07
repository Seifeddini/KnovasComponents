# Released images (release-images workflow)

How the Platform, Knovas Link and the sync component are built, signed and published from a branch, and how a
deployment takes them by digest (V1-G10). The workflow is `.github/workflows/release-images.yml`; its helper
`scripts/ci/release_image.sh` names the images; `tests/test_release_images_workflow.py` (Platform suite) pins both.

## What it builds

| Choice | GHCR package | Built from | Used by |
|---|---|---|---|
| `platform` | `ghcr.io/<owner>/docbridge-web-clientbundle` | `KnovasPlatform/components/docbridge_integration/Dockerfile` | `docker-compose.yml`, service `docbridge-web` (`DOCBRIDGE_WEB_IMAGE`) |
| `knovas-link` | `ghcr.io/<owner>/knovas-link` | `KnovasLink/Dockerfile` | the Builder's site package (`KNOVAS_LINK_DIGEST`) |
| `sync-component` | `ghcr.io/<owner>/sync-component` | `KnovasPlatform/components/sync_component/Dockerfile` | the Builder's site package (`SYNC_COMPONENT_DIGEST`) |

`all` builds the three, `site` the last two. `<owner>` is the repository's owner in lower case. The package names are
the image names the deployment already uses.

- **Tags:** the first 12 characters of the commit, and the branch name made into a tag (`cl/x-y` → `cl-x-y`). Tags
  move with the next run; a deployment always names the **digest**.
- **brain-runner:** the Platform (Brain Builder, Sync von GitHub) and the sync component install it from the private
  knowledge base while they are built. The workflow hands the repository secret `KB_READ_TOKEN` to that build as a
  BuildKit secret (no image layer, no log) and refuses to release either image without it.
- **Signature:** each pushed digest is signed keylessly (cosign, sigstore) by the run itself and verified at once
  against the run's identity:
  `https://github.com/<owner>/<repository>/.github/workflows/release-images.yml@refs/heads/<branch>`, issuer
  `https://token.actions.githubusercontent.com`, and the commit.
- **Runners:** one fresh single-use machine per image (`[self-hosted, knovas-ci-ephemeral]` while the repository
  variable `CI_EPHEMERAL_RUNNER` is set), never the shared CI box.

## Once per repository

1. **The workflow on the default branch.** GitHub offers "Run workflow" (and `gh workflow run`) only for a workflow
   whose file is on the default branch. A run then uses the file **of the branch it was started on**, so the branch
   you release must contain the file too. To put only this file on the default branch:

   ```bash
   git fetch origin
   git switch -c release-workflow origin/main
   git checkout origin/<the branch that has it> -- .github/workflows/release-images.yml
   git commit -m "ci: the release-images workflow, startable from any branch (V1-G10)"
   git push origin HEAD:main
   ```

   You should see the workflow under Actions → "release-images" with a "Run workflow" button.
2. **Secret `KB_READ_TOKEN`** (Settings → Secrets and variables → Actions → Repository secrets): a fine-grained token,
   **Contents: read** on the knowledge-base repository only. The CI job `knovas-platform` uses the same secret.
3. **Variable `CI_EPHEMERAL_RUNNER`** (same page, Variables) is set, so the jobs run on the single-use runners.
4. **The three packages** (after the first run creates them, or now if they exist): on GitHub, your profile →
   Packages → each of `docbridge-web-clientbundle`, `knovas-link`, `sync-component` → Package settings:
   - **Manage Actions access** → Add repository → this repository → role **Write**. (A package this workflow
     created first is linked already; one that existed before is not, and the push fails with `denied`.)
   - **Danger zone → visibility: Private.** The Platform and sync-component images contain brain-runner from the
     private knowledge base; they must never be public.

## Run it

In the browser: Actions → release-images → Run workflow → "Use workflow from": **your branch** → Images: `all` (or
`platform`, `site`, …) → Run workflow. Or:

```bash
gh workflow run release-images.yml --repo <owner>/<repository> --ref <branch> -f images=all
gh run list --repo <owner>/<repository> --workflow release-images.yml --limit 1      # the run's id
gh run watch <run id> --repo <owner>/<repository>
```

You should see one job per image, each green. The run's **Summary** has one section per image: the image by digest,
the branch, the commit, the tags, the signer, and the line to paste:

```
DOCBRIDGE_WEB_IMAGE=ghcr.io/<owner>/docbridge-web-clientbundle@sha256:…   (platform: knovas.env)
KNOVAS_LINK_DIGEST=sha256:…                                                (knovas-link: the site's .env)
SYNC_COMPONENT_DIGEST=sha256:…                                             (sync-component: the site's .env)
```

**Stop if** a job fails at "Build and check the image" with `KB_READ_TOKEN is not set` (step 2 above), at "Push" with
`denied` (step 4, Manage Actions access), or at "Sign" (do not deploy that digest; run again).

## Pull and check on a machine

The packages are private: each machine logs in with a read-only, revocable credential. GHCR takes a personal access
token **(classic)** with only the scope **`read:packages`** (github.com → Settings → Developer settings → Personal
access tokens → Tokens (classic) → Generate new token (classic); an expiry date; one token per machine, so revoking it
stops that machine only):

```bash
docker login ghcr.io -u <github user>          # paste the token when asked for the password
```

Optional, the signature (cosign pinned by checksum, the version the workflow signs with):

```bash
curl -sSfL -o /tmp/cosign https://github.com/sigstore/cosign/releases/download/v2.6.1/cosign-linux-amd64
echo "064954c5d8c7e3b28188eee5b1727b31c411550bc5fefd41aa672d3c761d103a  /tmp/cosign" | sha256sum -c -
chmod +x /tmp/cosign
/tmp/cosign verify ghcr.io/<owner>/<package>@sha256:<digest> \
  --certificate-identity "https://github.com/<owner>/<repository>/.github/workflows/release-images.yml@refs/heads/<branch>" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-github-workflow-sha <commit from the summary> > /dev/null && echo "signature OK"
```

You should see `/tmp/cosign: OK` from the checksum, then cosign's lines ending with `signature OK`.

## Deploy by digest

- **The Platform:** check out the **same commit** as the image (the checkout supplies `docker-compose.yml`,
  `config/` and the nginx configuration that the image runs with), set `DOCBRIDGE_WEB_IMAGE` in `knovas.env`, then
  `docker compose --env-file knovas.env pull docbridge-web` and
  `docker compose --env-file knovas.env up -d --no-build docbridge-web-nginx`. Never `--build` or `start.sh` while
  `DOCBRIDGE_WEB_IMAGE` is set.
- **A site:** the Builder's package names `knovas-link` and `sync-component` by digest variable; see
  [../connect-a-site.md](../connect-a-site.md).
- **Roll back:** put the previous digest back (the earlier run's summary has it), check out its commit, pull, `up -d
  --no-build`. Database migrations only move forward: before a rollback across a release that migrated the database,
  restore the backup taken before it.
