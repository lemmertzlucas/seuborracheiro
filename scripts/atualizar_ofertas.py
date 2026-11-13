#!/usr/bin/env python3
"""Atualiza as ofertas do Seu Borracheiro usando a API de afiliados da Shopee.

Roda no GitHub Actions (veja .github/workflows/atualizar-ofertas.yml).
Precisa dos segredos SHOPEE_APP_ID e SHOPEE_SECRET, configurados no repositório.
Sem eles, o script só avisa e sai sem mexer no site.

O script reescreve apenas o bloco entre as marcações
"/* ===== DADOS" e "/* ===== FIM DOS DADOS ===== */" do index.html.
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"
ARQUIVO = os.path.join(os.path.dirname(__file__), "..", "index.html")

# (categoria, termo de busca, quantos produtos manter)
BUSCAS = [
    ("carro", "pneu 175/70R13", 3),
    ("carro", "pneu 175/70R14", 3),
    ("carro", "pneu 175/65R14", 3),
    ("carro", "pneu 185/65R15", 3),
    ("carro", "pneu 195/55R15", 3),
    ("carro", "pneu 205/55R16", 3),
    ("moto", "pneu moto aro 18", 3),
    ("moto", "pneu moto aro 17", 2),
    ("agricola", "pneu agricola trator", 3),
    ("carrinho", "pneu carrinho de mao", 4),
    ("camaras", "camara de ar pneu carro aro", 4),
    ("camaras", "camara de ar moto aro 18", 1),
    ("rodas", "jogo de rodas aro 15", 2),
    ("rodas", "jogo de calotas aro 14", 2),
    ("valvulas", "bico valvula pneu sem camara", 3),
]

# Pneus remoldados ficam de fora das categorias de carro e moto.
SEM_REMOLD = {"carro", "moto"}
# Itens que aparecem na busca mas não são o produto (ex.: calota na busca de roda).
IGNORAR = {
    "carro": r"calota|camara|câmara|valvula|válvula|bico|remold|remoldad",
    "moto": r"camara|câmara|remold|remoldad",
    "rodas": r"parafuso|porca|adesivo",
    "camaras": r"valvula|válvula|haste|bico",
}
MARCAS = ["Pirelli", "Continental", "Goodyear", "Dunlop", "Michelin", "Bridgestone", "Firestone",
          "Hankook", "Kumho", "Ceat", "Nexen", "Westlake", "Xbri", "Barum", "Kenda", "Rinaldi",
          "Levorin", "Metzeler", "Pioneiro", "Delinte", "Compasal", "SpeedMax", "Landspider"]

QUERY = """{productOfferV2(keyword: "%s", sortType: 2, page: 1, limit: 30) {
  nodes { itemId productName priceMin priceMax priceDiscountRate commissionRate sales imageUrl offerLink }
}}"""


def chamar_api(app_id, secret, keyword):
    payload = json.dumps({"query": QUERY % keyword.replace('"', "")}, separators=(",", ":"))
    ts = str(int(time.time()))
    assinatura = hashlib.sha256((app_id + ts + payload + secret).encode()).hexdigest()
    req = urllib.request.Request(ENDPOINT, data=payload.encode(), method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id}, Timestamp={ts}, Signature={assinatura}",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        dados = json.load(r)
    if dados.get("errors"):
        raise RuntimeError(f"erro da API para '{keyword}': {dados['errors']}")
    return dados["data"]["productOfferV2"]["nodes"]


def medida_de(nome, categoria):
    padroes = {
        "carro": r"\d{3}/\d{2}\s?R\s?\d{2}",
        "moto": r"\d{2,3}/\d{2,3}-\d{2}|\d\.\d{2}-\d{2}",
        "agricola": r"\d{1,2}[.,]\d{1,2}-\d{2}|\d{1,2}[.,]\d{1,2}R\d{2}",
        "carrinho": r"\d[.,]\d{2}\s?[-x/]\s?\d",
        "camaras": r"aro\s?\d{2}(?:/\d{2})?",
        "rodas": r"aro\s?\d{2}",
        "valvulas": r"TR\s?-?\d{3}",
    }
    m = re.search(padroes.get(categoria, r"$^"), nome, re.I)
    if not m:
        return ""
    txt = m.group(0).upper().replace(" ", "")
    if categoria == "carro":
        txt = re.sub(r"R", "R", txt)
    return txt.replace("ARO", "Aro ").replace(",", ".")


def qtd_de(nome):
    m = re.search(r"(?:kit|jogo|par)\s*(?:com|de)?\s*(\d)", nome, re.I)
    if m:
        return int(m.group(1))
    return 2 if re.match(r"\s*par\b", nome, re.I) else 1


def marca_de(nome, categoria):
    for m in MARCAS:
        if re.search(rf"\b{m}\b", nome, re.I):
            return m
    return {"carrinho": "Carrinho", "camaras": "Câmara", "rodas": "Rodas",
            "valvulas": "Válvulas", "moto": "Moto", "agricola": "Agrícola"}.get(categoria, "Pneu")


def limpar_nome(nome):
    nome = re.sub(r"\s+", " ", nome).strip()
    return nome[:90].rstrip(" -,") + ("…" if len(nome) > 90 else "")


def montar_produto(n, categoria):
    nome = n["productName"]
    preco = float(n.get("priceMin") or n.get("priceMax") or 0)
    off = int(round(float(n.get("priceDiscountRate") or 0)))
    return {
        "marca": marca_de(nome, categoria),
        "nome": limpar_nome(nome),
        "medida": medida_de(nome, categoria) or "-",
        "categoria": categoria,
        "qtd": qtd_de(nome),
        "preco": round(preco, 2),
        "off": off if 0 < off < 90 else 0,
        "img": n["imageUrl"],
        "link": n["offerLink"],
    }


def escolher(nodes, categoria, limite, ja_usados):
    ruins = IGNORAR.get(categoria)
    bons = []
    for n in nodes:
        nome = n.get("productName") or ""
        if not n.get("offerLink") or not n.get("imageUrl"):
            continue
        if n["itemId"] in ja_usados:
            continue
        if ruins and re.search(ruins, nome, re.I):
            continue
        if categoria in SEM_REMOLD and re.search(r"remold", nome, re.I):
            continue
        bons.append(n)
    # prioriza quem vende bem e tem desconto
    bons.sort(key=lambda n: (int(n.get("sales") or 0) > 0, float(n.get("priceDiscountRate") or 0),
                             int(n.get("sales") or 0)), reverse=True)
    escolhidos = bons[:limite]
    ja_usados.update(n["itemId"] for n in escolhidos)
    return [montar_produto(n, categoria) for n in escolhidos]


def js_obj(p):
    return "{" + ",".join(f"{k}:{json.dumps(v, ensure_ascii=False)}" for k, v in p.items()) + "}"


def main():
    app_id = os.environ.get("SHOPEE_APP_ID", "").strip()
    secret = os.environ.get("SHOPEE_SECRET", "").strip()
    if not app_id or not secret:
        print("SHOPEE_APP_ID/SHOPEE_SECRET não configurados. Nada foi alterado.")
        return 0

    html = open(ARQUIVO, encoding="utf-8").read()
    produtos, usados, falhas = [], set(), []
    for categoria, termo, limite in BUSCAS:
        try:
            produtos += escolher(chamar_api(app_id, secret, termo), categoria, limite, usados)
        except Exception as e:  # uma busca com erro não derruba as outras
            falhas.append(f"{termo}: {e}")
        time.sleep(1)

    if falhas:
        print("Buscas com erro:\n  " + "\n  ".join(falhas))
    if len(produtos) < 10:
        print(f"Só {len(produtos)} produtos vieram da API. Mantendo o site como está.")
        return 1 if falhas else 0

    agora = datetime.now(timezone(timedelta(hours=-3)))
    bloco = ("/* ===== DADOS — a rotina automática vai reescrever só este bloco ===== */\n"
             f'const ATUALIZADO_EM = "{agora:%d/%m/%Y às %Hh%M}";\n'
             'const IMG = "https://down-bs-br.img.susercontent.com/";\n'
             'const LNK = "https://s.shopee.com.br/";\n'
             "const PRODUTOS = [\n " + ",\n ".join(js_obj(p) for p in produtos) + "\n];\n"
             "/* ===== FIM DOS DADOS ===== */")
    novo, n = re.subn(r"/\* ===== DADOS.*?/\* ===== FIM DOS DADOS ===== \*/", lambda _: bloco, html, flags=re.S)
    if n != 1:
        print("Não achei o bloco de dados no index.html.")
        return 1
    open(ARQUIVO, "w", encoding="utf-8").write(novo)
    print(f"{len(produtos)} produtos atualizados em {agora:%d/%m/%Y %H:%M}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
