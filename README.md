# ocidiff

**Diff two Docker images without pulling them.**

`ocidiff` compares two images straight from the registry and reports what
changed between them in: size, layer count, environment variables, and
installed Debian packages. It reads the Docker Hub API directly, so it needs
neither a Docker daemon nor a full `docker pull`.

```console
$ ocidiff nginx:1.26.0 nginx:1.27.0

╭──────────────────────────────────────────────╮
│ A  library/nginx:1.26.0   67.7 MiB  7 layers │
│ B  library/nginx:1.27.0   67.7 MiB  7 layers │
│                          same size           │
│                                              │
│ + added   - removed   ~ changed              │
╰──────────────────────────────────────────────╯

ENV  2 change(s)
    name            before       after
───────────────────────────────────────────
~   NGINX_VERSION   1.26.0       1.27.0
~   PKG_RELEASE     1~bookworm   2~bookworm

PACKAGES  20 change(s)
    name        before             after
──────────────────────────────────────────────────
~   libssl3     3.0.11-1~deb12u2   3.0.13-1~deb12u1
~   nginx       1.26.0-1~bookworm  1.27.0-2~bookworm
~   openssl     3.0.11-1~deb12u2   3.0.13-1~deb12u1
```

These two releases weigh the same, yet `openssl` moved from 3.0.11 to 3.0.13.


## Requirements

- Python 3.10+
- [`httpx`](https://www.python-httpx.org/) and [`rich`](https://rich.readthedocs.io/)

## Installation

```bash
pipx install ocidiff
```

Dependencies are installed for you.

## Usage

```bash
ocidiff IMAGE_A IMAGE_B [--fast]
```

|       Argument       |                     Description                     |
| -------------------- | --------------------------------------------------- |
| `IMAGE_A`, `IMAGE_B` | Image references, e.g. `nginx:1.27.0`               |
| `--fast`             | Skip the package diff, which downloads image layers |

### Examples

```bash
# Two tags of the same image
ocidiff nginx:1.26.0 nginx:1.27.0

# Size and environment only, no layer downloads
ocidiff python:3.12 python:3.13 --fast

# Across repositories
ocidiff bitnami/nginx:latest nginx:latest
```

Names follow Docker conventions: an unqualified name resolves to the official
namespace (`nginx` → `library/nginx`) and a missing tag defaults to `latest`.

Exit status is `0` on success and `1` when the image cannot be resolved.

## How it works

1. Requests a pull-scoped token from `auth.docker.io`.
2. A tag usually points at a manifest list covering several architectures;
   `ocidiff` selects the `linux/amd64` entry.
3. Environment variables come from the image config blob.
4. Layers are scanned newest-first for `/var/lib/dpkg/status`, 
   since the topmost copy is the effective one. Each
   layer is opened as a gzip stream (`tarfile` mode `r|gz`) and abandoned as soon
   as the file is found.
5. Environment variables and packages are both plain `{name: version}` mappings.

## Limitations/steps ahead

- Only `linux/amd64` images are inspected.
- Package comparison requires a Debian or Ubuntu base. Alpine, Wolfi, and
  distroless images report the section as unavailable rather than guessing.
- Only Docker Hub is supported; other registries are not configurable yet.
