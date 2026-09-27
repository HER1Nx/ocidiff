#!/usr/bin/env python3
"""
ocidiff: compara duas imagens Docker direto do registry. Sem docker, sem pull,
sem pip install. Um arquivo, so a biblioteca padrao do Python 3.

    python3 ocidiff.py nginx:1.26.0 nginx:1.27.0

Mapa do arquivo, na ordem em que o programa roda:

    1. registry    conversa com o Docker Hub (token, manifesto, blobs)
    2. leitura     tira tamanho, env e pacotes de uma imagem
    3. diferenca   compara dois dicionarios {nome: versao}
    4. tela        imprime o relatorio no terminal
    5. main        le os argumentos e junta tudo
"""

import argparse
import json
import os
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request

VERSAO = "0.1.0"

REGISTRY = "https://registry-1.docker.io"
AUTH = "https://auth.docker.io/token"
TIMEOUT = 60
ACCEPT = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
CAMINHO_DPKG = "var/lib/dpkg/status"


class ErroOcidiff(Exception):
    """Erro que o usuario ve como mensagem simples, sem traceback."""


# ---------------------------------------------------------------- 1. registry
#
# Uma imagem aqui e so um dicionario:
#   {"repo": "library/nginx", "tag": "1.27.0", "nome": "library/nginx:1.27.0",
#    "manifesto": {...}, "config": {...}, "token": "..."}


def abrir_url(url, token=None, accept=None):
    pedido = urllib.request.Request(url)
    if token:
        # ponytail: "unredirected" = o token nao vai junto quando o blob redireciona pro CDN
        pedido.add_unredirected_header("Authorization", "Bearer " + token)
    if accept:
        pedido.add_header("Accept", accept)
    return urllib.request.urlopen(pedido, timeout=TIMEOUT)


def baixar_json(url, token=None, accept=None):
    with abrir_url(url, token, accept) as resposta:
        return json.load(resposta)


def pedir_token(repo):
    parametros = urllib.parse.urlencode({
        "service": "registry.docker.io",
        "scope": f"repository:{repo}:pull",
    })
    return baixar_json(f"{AUTH}?{parametros}")["token"]


def ler_manifesto(repo, referencia, token):
    return baixar_json(f"{REGISTRY}/v2/{repo}/manifests/{referencia}", token, ACCEPT)


def abrir_blob(repo, digest, token):
    return abrir_url(f"{REGISTRY}/v2/{repo}/blobs/{digest}", token)


def escolher_amd64(indice):
    """Uma tag costuma apontar para uma lista, uma entrada por arquitetura."""
    for entrada in indice.get("manifests", []):
        plataforma = entrada.get("platform", {})
        if plataforma.get("architecture") == "amd64" and plataforma.get("os") == "linux":
            return entrada["digest"]
    raise ErroOcidiff("no linux/amd64 variant for this image")


def separar_nome(texto):
    """'nginx' -> ('library/nginx', 'latest'). Nome sem barra ganha 'library/'."""
    repo, _, tag = texto.partition(":")
    if "/" not in repo:
        repo = "library/" + repo
    if not tag:
        tag = "latest"
    return repo, tag


def buscar_imagem(texto):
    repo, tag = separar_nome(texto)
    try:
        token = pedir_token(repo)
        manifesto = ler_manifesto(repo, tag, token)
        if "manifests" in manifesto:
            manifesto = ler_manifesto(repo, escolher_amd64(manifesto), token)
        with abrir_blob(repo, manifesto["config"]["digest"], token) as resposta:
            config = json.load(resposta)
    except urllib.error.HTTPError as erro:
        if erro.code == 404:
            raise ErroOcidiff(f"image not found: {repo}:{tag}") from erro
        raise ErroOcidiff(f"Docker Hub refused the request: {erro.code}") from erro
    return {
        "repo": repo,
        "tag": tag,
        "nome": f"{repo}:{tag}",
        "manifesto": manifesto,
        "config": config,
        "token": token,
    }


def procurar_arquivo(imagem, caminho):
    """Varre as camadas da mais nova para a mais velha: a primeira copia achada vale.

    Cada camada e lida em stream (modo "r|gz") e largada assim que o arquivo aparece.
    """
    for camada in reversed(imagem["manifesto"]["layers"]):
        with abrir_blob(imagem["repo"], camada["digest"], imagem["token"]) as resposta:
            try:
                with tarfile.open(fileobj=resposta, mode="r|gz") as tar:
                    for membro in tar:
                        nome = membro.name
                        if nome.startswith("./"):
                            nome = nome[2:]
                        if nome != caminho:
                            continue
                        arquivo = tar.extractfile(membro)
                        if arquivo is not None:
                            return arquivo.read().decode("utf-8", "replace")
            except tarfile.TarError:
                continue
    return None


# ----------------------------------------------------------------- 2. leitura


def tamanho_mib(imagem):
    total = 0
    for camada in imagem["manifesto"]["layers"]:
        total += camada["size"]
    return total / 1024 / 1024


def ler_env(imagem):
    linhas = imagem["config"].get("config", {}).get("Env") or []
    env = {}
    for linha in linhas:
        if "=" in linha:
            nome, valor = linha.split("=", 1)
            env[nome] = valor
    return env


def ler_pacotes_dpkg(texto):
    """Le o /var/lib/dpkg/status e devolve {pacote: versao}."""
    pacotes = {}
    nome = None
    for linha in texto.splitlines():
        if linha.startswith("Package: "):
            nome = linha[len("Package: "):].strip()
        elif nome and linha.startswith("Version: "):
            pacotes[nome] = linha[len("Version: "):].strip()
            nome = None
    return pacotes


def ler_pacotes(imagem):
    """{pacote: versao}, ou None quando a imagem nao e Debian/Ubuntu."""
    texto = procurar_arquivo(imagem, CAMINHO_DPKG)
    if texto is None:
        return None
    return ler_pacotes_dpkg(texto)


# --------------------------------------------------------------- 3. diferenca
#
# Env e pacotes sao a mesma coisa: {nome: versao}. Uma funcao compara os dois.
# As chaves em ingles ("kind", "name"...) sao o formato do --json.


def comparar_dicionarios(antes, depois):
    mudancas = []
    for nome in sorted(set(antes) | set(depois)):
        valor_antes = antes.get(nome)
        valor_depois = depois.get(nome)
        if valor_antes == valor_depois:
            continue
        if valor_depois is None:
            tipo = "-"
        elif valor_antes is None:
            tipo = "+"
        else:
            tipo = "~"
        mudancas.append({"kind": tipo, "name": nome, "before": valor_antes, "after": valor_depois})
    return mudancas


def comparar(imagem_a, imagem_b):
    """A parte rapida: so manifesto e config, nenhuma camada baixada."""
    return {
        "a": imagem_a["nome"],
        "b": imagem_b["nome"],
        "size_a": tamanho_mib(imagem_a),
        "size_b": tamanho_mib(imagem_b),
        "layers_a": len(imagem_a["manifesto"]["layers"]),
        "layers_b": len(imagem_b["manifesto"]["layers"]),
        "env": comparar_dicionarios(ler_env(imagem_a), ler_env(imagem_b)),
        "packages": None,
        "packages_note": "",
    }


def comparar_pacotes(imagem_a, imagem_b):
    """A parte lenta: baixa camadas. None se alguma das duas nao for Debian/Ubuntu."""
    pacotes_a = ler_pacotes(imagem_a)
    pacotes_b = ler_pacotes(imagem_b)
    if pacotes_a is None or pacotes_b is None:
        return None
    return comparar_dicionarios(pacotes_a, pacotes_b)


# -------------------------------------------------------------------- 4. tela

USAR_COR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
if USAR_COR and os.name == "nt":
    os.system("")  # ponytail: liga as cores ANSI no console antigo do Windows

VERDE, VERMELHO, AMARELO, NEGRITO, APAGADO = "32", "31", "33", "1", "2"
COR_DO_TIPO = {"+": VERDE, "-": VERMELHO, "~": AMARELO}
VAZIO = "-"  # ponytail: so ASCII, o console do Windows (cp1252) quebra com "—"


def pintar(texto, cor):
    if not USAR_COR:
        return texto
    return f"\033[{cor}m{texto}\033[0m"


def avisar(texto):
    """Progresso e erros vao pro stderr, para o stdout sair limpo num pipe."""
    print(texto, file=sys.stderr)


def imprimir_cabecalho(relatorio):
    largura = max(len(relatorio["a"]), len(relatorio["b"]))
    for letra in ["a", "b"]:
        nome = relatorio[letra].ljust(largura)
        tamanho = f"{relatorio['size_' + letra]:.1f} MiB".rjust(10)
        camadas = f"{relatorio['layers_' + letra]} layers"
        print(f"{pintar(letra.upper(), APAGADO)}  {pintar(nome, NEGRITO)}  {tamanho}  {pintar(camadas, APAGADO)}")

    diferenca = relatorio["size_b"] - relatorio["size_a"]
    if abs(diferenca) < 0.05:
        texto, cor = "same size", APAGADO
    elif diferenca < 0:
        texto, cor = f"{diferenca:+.1f} MiB", VERDE
    else:
        texto, cor = f"{diferenca:+.1f} MiB", VERMELHO
    print(" " * (largura + 5) + pintar(texto.rjust(10), cor))

    print()
    print(pintar("+", VERDE) + " added   " + pintar("-", VERMELHO) + " removed   "
          + pintar("~", AMARELO) + " changed")


def imprimir_tabela(mudancas):
    linhas = []
    for m in mudancas:
        antes = m["before"] if m["before"] is not None else VAZIO
        depois = m["after"] if m["after"] is not None else VAZIO
        linhas.append((m["kind"], m["name"], antes, depois))

    largura_nome = max([len("name")] + [len(l[1]) for l in linhas])
    largura_antes = max([len("before")] + [len(l[2]) for l in linhas])

    print("    " + "name".ljust(largura_nome) + "   " + "before".ljust(largura_antes) + "   after")
    print("-" * (4 + largura_nome + 3 + largura_antes + 3 + len("after")))
    for tipo, nome, antes, depois in linhas:
        cor = COR_DO_TIPO[tipo]
        print(pintar(tipo, cor) + "   " + pintar(nome.ljust(largura_nome), cor)
              + "   " + pintar(antes.ljust(largura_antes), APAGADO) + "   " + depois)


def imprimir_secao(titulo, mudancas, motivo=""):
    print()
    if mudancas is None:
        print(pintar(titulo, NEGRITO) + "  " + pintar(f"({motivo})", APAGADO))
    elif not mudancas:
        print(pintar(titulo, NEGRITO) + "  " + pintar("(no changes)", APAGADO))
    else:
        print(pintar(titulo, NEGRITO) + "  " + pintar(f"{len(mudancas)} change(s)", APAGADO))
        imprimir_tabela(mudancas)


def imprimir_relatorio(relatorio):
    print()
    imprimir_cabecalho(relatorio)
    imprimir_secao("ENV", relatorio["env"])
    imprimir_secao("PACKAGES", relatorio["packages"], relatorio["packages_note"])
    print()


# -------------------------------------------------------------------- 5. main


def ler_argumentos(argv=None):
    p = argparse.ArgumentParser(
        prog="ocidiff",
        description="compare two Docker images and show what changed",
        epilog="example: python3 ocidiff.py nginx:1.26.0 nginx:1.27.0",
    )
    p.add_argument("image_a", help="e.g. nginx:1.26.0")
    p.add_argument("image_b", help="e.g. nginx:1.27.0")
    p.add_argument("--fast", action="store_true",
                   help="skip the package diff, which downloads image layers")
    p.add_argument("--json", action="store_true",
                   help="print the report as JSON instead of tables")
    p.add_argument("--version", action="version", version=f"ocidiff {VERSAO}")
    return p.parse_args(argv)


def main(argv=None):
    args = ler_argumentos(argv)
    try:
        avisar("fetching manifests...")
        imagem_a = buscar_imagem(args.image_a)
        imagem_b = buscar_imagem(args.image_b)
        relatorio = comparar(imagem_a, imagem_b)

        if args.fast:
            relatorio["packages_note"] = "skipped by --fast"
        else:
            avisar("downloading layers to read the package list...")
            relatorio["packages"] = comparar_pacotes(imagem_a, imagem_b)
            if relatorio["packages"] is None:
                relatorio["packages_note"] = "not a Debian/Ubuntu image"
    except ErroOcidiff as erro:
        avisar(pintar("error: ", VERMELHO) + str(erro))
        return 1

    if args.json:
        print(json.dumps(relatorio, indent=2))
    else:
        imprimir_relatorio(relatorio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
