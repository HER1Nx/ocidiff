"""Checagens sem rede e sem pytest: python3 test_ocidiff.py (pytest tambem roda)."""

import io
import tarfile

import ocidiff


def tar_gz(nome, conteudo):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        dados = conteudo.encode()
        info = tarfile.TarInfo(nome)
        info.size = len(dados)
        tar.addfile(info, io.BytesIO(dados))
    return buffer.getvalue()


def test_separar_nome():
    assert ocidiff.separar_nome("nginx") == ("library/nginx", "latest")
    assert ocidiff.separar_nome("nginx:1.27.0") == ("library/nginx", "1.27.0")
    assert ocidiff.separar_nome("bitnami/nginx") == ("bitnami/nginx", "latest")


def test_escolher_amd64():
    indice = {"manifests": [
        {"platform": {"architecture": "arm64", "os": "linux"}, "digest": "arm"},
        {"platform": {"architecture": "amd64", "os": "windows"}, "digest": "win"},
        {"platform": {"architecture": "amd64", "os": "linux"}, "digest": "amd"},
    ]}
    assert ocidiff.escolher_amd64(indice) == "amd"
    try:
        ocidiff.escolher_amd64({"manifests": []})
        raise AssertionError("devia ter falhado")
    except ocidiff.ErroOcidiff:
        pass


def test_ler_env():
    imagem = {"config": {"config": {"Env": ["PATH=/usr/bin", "OPTS=-Da=b", "QUEBRADO"]}}}
    assert ocidiff.ler_env(imagem) == {"PATH": "/usr/bin", "OPTS": "-Da=b"}
    assert ocidiff.ler_env({"config": {}}) == {}


def test_ler_pacotes_dpkg():
    texto = "Package: nginx\nVersion: 1.27\n\nPackage: sem-versao\n\nPackage: tar\nVersion: 1.34\n"
    assert ocidiff.ler_pacotes_dpkg(texto) == {"nginx": "1.27", "tar": "1.34"}


def test_comparar_dicionarios():
    antes = {"igual": "1", "mudou": "1", "saiu": "1"}
    depois = {"igual": "1", "mudou": "2", "entrou": "1"}
    assert [(m["kind"], m["name"]) for m in ocidiff.comparar_dicionarios(antes, depois)] == [
        ("+", "entrou"), ("~", "mudou"), ("-", "saiu"),
    ]


def test_procurar_arquivo_pega_a_camada_mais_nova_e_pula_lixo():
    blobs = {
        "velha": tar_gz("./" + ocidiff.CAMINHO_DPKG, "Version: 1.0"),
        "nova": tar_gz("./" + ocidiff.CAMINHO_DPKG, "Version: 2.0"),
        "lixo": b"nao sou um tar.gz",
    }
    original = ocidiff.abrir_blob
    ocidiff.abrir_blob = lambda repo, digest, token: io.BytesIO(blobs[digest])
    try:
        imagem = {"repo": "x", "token": "", "manifesto": {"layers": [
            {"digest": "velha"}, {"digest": "nova"}, {"digest": "lixo"},
        ]}}
        assert ocidiff.procurar_arquivo(imagem, ocidiff.CAMINHO_DPKG) == "Version: 2.0"
        assert ocidiff.procurar_arquivo(imagem, "etc/nada") is None
    finally:
        ocidiff.abrir_blob = original


if __name__ == "__main__":
    for nome, funcao in list(globals().items()):
        if nome.startswith("test_"):
            funcao()
            print("ok", nome)
