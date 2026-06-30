# ecr-build-push-pull

Builds a Docker image and pushes it to ECR, or pulls it if the tag already exists.
ECR is also used as the BuildKit layer cache. A dependency hash lets source-only
changes reuse an existing image while the test action mounts the current checkout.

## Usage

```yaml
- uses: ./.github/actions/ecr-build-push-pull
  with:
    image-tag: ${{ env.DOCKER_IMAGE_TAG }}
    isaacsim-base-image: nvcr.io/nvidia/isaac-sim
    isaacsim-version: 6.0.0
    dockerfile-path: docker/Dockerfile.base
    cache-tag: cache-base
    ecr-url: (optional, complete url for ECR storage)
```

## ECR URL resolution order

1. `ecr-url` input
2. `ECR_CACHE_URL` environment variable on the runner
3. SSM parameter `/github-runner/<instance-id>/ecr-cache-url`
4. If none resolve, ECR is skipped and the image is built locally

## Cache behavior

The action checks increasingly broad caches before building anything:

1. **Exact commit image:** The CI image tag includes the tested commit. If the
   corresponding ECR manifest exists, the action pulls and retags that image
   locally. No build runs.
2. **Dependency image:** If there is no exact image, the action calculates a
   dependency hash from:

   - the selected Dockerfile;
   - `isaaclab.sh` and `environment.yml`;
   - Isaac Lab CLI installation code;
   - repository package manifests such as `pyproject.toml`, `setup.py`,
     `extension.toml`, requirement files, and `uv.lock`; and
   - the resolved digest of the Isaac Sim base image.

   Regular Python source files are deliberately excluded. A source-only change
   therefore resolves to the same `deps-<hash>` image. On a hit, the action
   creates the commit-specific ECR tag from that existing manifest without
   downloading or rebuilding its layers. Package tests then mount the current
   checkout at `/workspace/isaaclab`, so they execute the changed source rather
   than the source stored in the cached image.
3. **BuildKit layer cache:** On a dependency-cache miss, the action builds a new
   image using the registry cache named by `cache-tag`. Unchanged Docker layers
   are downloaded from ECR instead of being rebuilt. The completed image is
   pushed under both its commit-specific tag and its `deps-<hash>` tag for later
   runs.
4. **Local fallback:** If no ECR URL can be resolved, the action builds locally.
   The resulting image can be reused only on that runner and is not available to
   other runners or future machines.

The `cache-result` output identifies the selected path:

- `exact-hit`: reused the commit-specific image;
- `dependency-hit`: reused an image with matching environment dependencies;
- `remote-miss`: built and published a new ECR image; or
- `local-build`: built locally because ECR was unavailable.

## Opening a tested image

After authenticating to ECR, copy `Source revision SHA` and `Image` from the CI job
summary:

```bash
SOURCE_REVISION_SHA=<source-revision-sha>
IMAGE=<ecr-image>@sha256:<digest>
WORKTREE=/tmp/isaaclab-repro

git fetch origin "$SOURCE_REVISION_SHA"
git worktree add --detach "$WORKTREE" "$SOURCE_REVISION_SHA"
docker pull "$IMAGE"
# Restore _isaac_sim hidden by the worktree mount.
docker run --rm -it --gpus all \
  --entrypoint bash \
  -v "$WORKTREE:/workspace/isaaclab" \
  -w /workspace/isaaclab \
  "$IMAGE" \
  -c 'ln -sfn /isaac-sim _isaac_sim && exec bash'
rm "$WORKTREE/_isaac_sim"
git worktree remove "$WORKTREE"
```

The image provides the dependencies; the mounted revision provides the exact
source tested by CI. Run the relevant test commands from the interactive shell.
