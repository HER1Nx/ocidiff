# ocidiff

**Diff two Docker images. Nothing to install. No Docker, no pull, no pip.**

`ocidiff` is one Python file that uses only the standard library. If you have
`python3`, you already have everything it needs:

```bash
curl -O https://raw.githubusercontent.com/HER1Nx/ocidiff/main/ocidiff.py
python3 ocidiff.py nginx:1.26.0 nginx:1.27.0
```

Or don't even save it:

```bash
curl -s https://raw.githubusercontent.com/HER1Nx/ocidiff/main/ocidiff.py | python3 - nginx:1.26.0 nginx:1.27.0
```

It reads the registry API directly and reports what changed in size, layer
count, environment variables, and installed Debian packages.

```console
$ python3 ocidiff.py nginx:1.26.0 nginx:1.27.0

A  library/nginx:1.26.0    67.7 MiB  7 layers
B  library/nginx:1.27.0    67.7 MiB  7 layers
                          same size

+ added   - removed   ~ changed

ENV  2 change(s)
    name            before       after
--------------------------------------
~   NGINX_VERSION   1.26.0       1.27.0
~   PKG_RELEASE     1~bookworm   2~bookworm

PACKAGES  20 change(s)
    name        before             after
----------------------------------------------------
~   libssl3     3.0.11-1~deb12u2   3.0.13-1~deb12u1
~   nginx       1.26.0-1~bookworm  1.27.0-2~bookworm
~   openssl     3.0.11-1~deb12u2   3.0.13-1~deb12u1
```

These two releases weigh the same, yet `openssl` moved from 3.0.11 to 3.0.13.

Works anywhere Python 3 does: a CI runner, a locked-down server, a laptop
without Docker, a Windows box.

## Usage

```bash
python3 ocidiff.py IMAGE_A IMAGE_B [--fast] [--json]
```

|       Argument       |                     Description                     |
| -------------------- | --------------------------------------------------- |
| `IMAGE_A`, `IMAGE_B` | Image references, e.g. `nginx:1.27.0`               |
| `--fast`             | Skip the package diff, which downloads image layers |
| `--json`             | Print the report as JSON (progress goes to stderr)  |

```bash
python3 ocidiff.py python:3.12 python:3.13 --fast          # no layer downloads
python3 ocidiff.py bitnami/nginx:latest nginx:latest       # across repositories
python3 ocidiff.py nginx:1.26 nginx:1.27 --json | jq .packages
```

Names follow Docker conventions: `nginx` → `library/nginx`, missing tag → `latest`.
Exit status is `0` on success, `1` when an image cannot be resolved.
Set `NO_COLOR=1` to turn colors off.

## How it works

Read `ocidiff.py` top to bottom; it runs in that order.

1. **registry**: asks `auth.docker.io` for a pull token, fetches the manifest,
   and when the tag is a multi-arch list picks the `linux/amd64` entry.
2. **leitura**: size and layer count come from the manifest, env from the
   config blob. For packages, layers are streamed newest-first as gzip tars
   (`tarfile` mode `r|gz`) until `/var/lib/dpkg/status` shows up; the rest
   is never downloaded.
3. **diferenca**: env and packages are both `{name: version}`, so one
   function diffs both.
4. **tela**: prints the tables.

Tests need nothing either: `python3 test_ocidiff.py`.

## Limitations

- Only Docker Hub, only `linux/amd64`.
- Package diff needs a Debian/Ubuntu base. Alpine, Wolfi and distroless report
  the section as unavailable instead of guessing.

## Roadmap

The rule for every item: **stdlib only, still one file.** If a feature needs a
dependency, it waits until the stdlib can do it.

[diffoci](https://github.com/reproducible-containers/diffoci) is the closest
tool: it diffs every file in every layer, but it is a Go binary you have to
download, and it leans on Docker/containerd for local images. `ocidiff`
answers the smaller question ("what versions moved?") with zero setup.

Next, in order of value per line of code:

1. **Any registry**: `ghcr.io/x/y:tag`, `quay.io/...`. Read the host from the
   name and follow the `WWW-Authenticate` header of a 401 to find the token
   endpoint. `urllib` covers it.
2. **`--platform linux/arm64`**: `escolher_amd64` becomes "pick this platform".
3. **Alpine packages**: `lib/apk/db/installed` (`P:` name, `V:` version),
   same shape as the dpkg parser.
4. **Distroless**: `var/lib/dpkg/status.d/*`, one stanza per file.
5. **More config fields** from diffoci's list: `Entrypoint`, `Cmd`, `User`,
   `WorkingDir`, `ExposedPorts`, `Labels`. They are already in the config
   blob, so this is free: feed them to `comparar_dicionarios`.
6. **Private images**: read the token from `~/.docker/config.json` (`auths`),
   no Docker needed to be running.
7. **RPM (Fedora/RHEL 9+)**: `var/lib/rpm/rpmdb.sqlite`, readable with the
   stdlib `sqlite3`.
8. **File-level diff** (diffoci's core feature): list every path, size and
   mode per image. Costs downloading all layers, so opt-in: `--files`.

Not planned: zstd layers until the stdlib `compression.zstd` (Python 3.14) is
common; local Docker/containerd images (that needs a daemon, and not needing
one is the point).
