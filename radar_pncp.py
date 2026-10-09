"""Radar de editais PNCP para empresas de climatização.

Python 3.10+, sem dependências externas. Uso: python radar_pncp.py
Dados públicos: https://www.gov.br/pncp/pt-br/acesso-a-informacao/dados-abertos
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
TZ = ZoneInfo("America/Sao_Paulo")
DEFAULT_TERMS = {
    "ar condicionado": 100,
    "ar-condicionado": 100,
    "climatizacao": 95,
    "sistema de refrigeracao": 90,
    "manutencao de condicionadores": 90,
    "pmoc": 85,
    "split inverter": 85,
    "aparelhos de refrigeracao": 70,
}
MODALITIES = (6, 8)  # pregão eletrônico e dispensa de licitação


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def get_json(params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "RadarPNCP/1.0 contato: relatorio local"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                if response.status == 204:
                    return {"data": [], "totalRegistros": 0, "totalPaginas": 0}
                body = response.read()
                if not body:
                    return {"data": [], "totalRegistros": 0, "totalPaginas": 0}
                return json.loads(body)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2 or isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                raise RuntimeError(f"Falha na API PNCP: {url}: {exc}") from exc
            time.sleep(12 * (attempt + 1) if isinstance(exc, urllib.error.HTTPError) and exc.code == 429 else 2 ** attempt)
    raise AssertionError("unreachable")


def collect(days: int, page_size: int, max_pages: int, uf: str) -> tuple[list[dict], list[str], int, int]:
    today = datetime.now(TZ).date()
    start = today - timedelta(days=days - 1)
    found: dict[str, dict] = {}
    problems: list[str] = []
    read_count = 0
    expected_count = 0
    for modality in MODALITIES:
        page = 1
        while True:
            params = {
                "dataInicial": start.strftime("%Y%m%d"),
                "dataFinal": today.strftime("%Y%m%d"),
                "codigoModalidadeContratacao": modality,
                "pagina": page,
                "tamanhoPagina": page_size,
                "uf": uf,
            }
            try:
                payload = get_json(params)
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                problems.append(str(exc))
                break
            rows = payload.get("data") or []
            if not isinstance(rows, list):
                problems.append(f"Formato inesperado, modalidade {modality}, página {page}")
                break
            if page == 1:
                expected_count += int(payload.get("totalRegistros") or 0)
            read_count += len(rows)
            for row in rows:
                key = row.get("numeroControlePNCP")
                if key:
                    found[key] = row
            total_pages = int(payload.get("totalPaginas") or 0)
            if page >= total_pages or not rows:
                break
            if page >= max_pages:
                problems.append(f"Modalidade {modality}: limite de {max_pages} páginas; {total_pages} disponíveis")
                break
            page += 1
            time.sleep(2)
    return list(found.values()), problems, read_count, expected_count


def shortlist(raw: list[dict], terms: dict[str, int]) -> list[dict]:
    now = datetime.now(TZ)
    selected = []
    for row in raw:
        if row.get("situacaoCompraId") not in (None, 1):
            continue
        obj = norm(row.get("objetoCompra"))
        extra = norm(row.get("informacaoComplementar"))
        hits = [term for term in terms if term in obj or term in extra]
        if not hits:
            continue
        end_raw = row.get("dataEncerramentoProposta") or ""
        try:
            end = datetime.fromisoformat(end_raw)
            end = end.replace(tzinfo=TZ) if end.tzinfo is None else end.astimezone(TZ)
            days_left = round((end - now).total_seconds() / 86400, 1)
        except ValueError:
            days_left = None
        if days_left is not None and days_left < 0:
            continue
        key = row["numeroControlePNCP"]
        org = row.get("orgaoEntidade") or {}
        unit = row.get("unidadeOrgao") or {}
        value = row.get("valorTotalEstimado")
        score = max(terms[t] for t in hits)
        if days_left is not None and days_left <= 7:
            score += 10
        if unit.get("ufSigla") in ("SP", "PR", "SC", "RS"):
            score += 5
        if any(t in obj for t in ("manutencao", "higienizacao", "limpeza de ar condicionado", "pmoc")):
            category = "Serviço de manutenção"
        elif any(t in obj for t in ("aquisicao", "fornecimento", "compra de")):
            category = "Fornecimento"
        else:
            category = "Verificar objeto"
        selected.append({
            "id": key, "score": score, "category": category, "terms": ", ".join(hits),
            "object": row.get("objetoCompra") or "",
            "published": row.get("dataPublicacaoPncp") or "",
            "deadline": end_raw, "days_left": days_left,
            "value": value if isinstance(value, (int, float)) and value > 0 else None,
            "agency": org.get("razaoSocial") or "",
            "city": unit.get("municipioNome") or "", "uf": unit.get("ufSigla") or "",
            "modality": row.get("modalidadeNome") or "",
            "source": f"https://pncp.gov.br/app/editais/{org.get('cnpj')}/{row.get('anoCompra')}/{row.get('sequencialCompra')}",
            "origin": row.get("linkSistemaOrigem") or "",
        })
    return sorted(selected, key=lambda x: (-x["score"], x["days_left"] if x["days_left"] is not None else 9999, x["id"]))


def brl(value: float | None) -> str:
    return "—" if value is None else "R$ " + f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def render(rows: list[dict], run: dict) -> str:
    cards = []
    new_stat = "" if run["new"] is None else f'<div class="stat"><b>{run["new"]}</b><small>novas neste ciclo</small></div>'
    for x in rows:
        title = html.escape(x["object"])
        cards.append(f'''<article class="card"><div class="top"><span class="score">Prioridade {x['score']}</span><span>{html.escape(x['modality'])}</span></div>
<h2>{title}</h2><p class="meta">{html.escape(x['category'])} · {html.escape(x['agency'])} · {html.escape(x['city'])}/{html.escape(x['uf'])}</p>
<div class="facts"><span>Prazo: <strong>{html.escape(x['deadline'][:16].replace('T',' ')) or 'não informado'}</strong></span><span>Estimativa: <strong>{html.escape(brl(x['value']))}</strong></span></div>
<p class="terms">Termos: {html.escape(x['terms'])}</p><a href="{html.escape(x['source'], quote=True)}" target="_blank" rel="noopener">Abrir edital no PNCP ↗</a></article>''')
    notice = "Consulta parcial: " + "; ".join(run["problems"]) if run["problems"] else "Consulta concluída dentro do recorte configurado."
    return f'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Radar PNCP — climatização</title>
<style>:root{{font-family:Inter,Segoe UI,Arial,sans-serif;color:#132333;background:#f4f7f7}}*{{box-sizing:border-box}}body{{margin:0}}header{{background:#092d39;color:white;padding:42px max(20px,calc((100vw - 1100px)/2))}}h1{{font-size:clamp(29px,4vw,46px);margin:0 0 8px}}header p{{color:#cde5e8;margin:0}}main{{max-width:1100px;margin:auto;padding:30px 20px}}.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px}}.stat,.card,.note,.offer{{background:white;border:1px solid #dce6e7;border-radius:14px;padding:20px}}.stat{{min-width:160px;flex:1}}.stat b{{display:block;font-size:26px;color:#086b75}}.stat small,.meta,.terms,.note{{color:#53666b}}.note,.offer{{margin-bottom:22px}}.offer{{background:#e5f3f1;border-color:#bddad5}}.offer strong{{font-size:19px}}.offer p{{margin:8px 0 12px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(315px,1fr));gap:16px}}.top,.facts{{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}}.top{{font-size:13px;color:#53666b}}.score{{color:#006f70;font-weight:700}}.card h2{{font-size:18px;line-height:1.4;margin:16px 0}}.facts{{padding:12px 0;border-top:1px solid #e6eded;border-bottom:1px solid #e6eded;font-size:14px}}.terms{{font-size:13px}}a{{color:#006f70;font-weight:700}}footer{{font-size:12px;color:#63777a;margin-top:24px}}</style>
<header><h1>Radar de compras públicas</h1><p>Climatização em Santa Catarina · fonte PNCP</p></header><main><div class="stats"><div class="stat"><b>{len(rows)}</b><small>oportunidades abertas</small></div>{new_stat}<div class="stat"><b>{run['read']}</b><small>editais examinados</small></div></div>
<div class="note">Atualizado em {html.escape(run['at'])}. {html.escape(notice)} A classificação usa palavras no objeto e informações complementares; confirme itens, anexos, elegibilidade e prazo no edital original.</div>
<div class="offer"><strong>Quer uma triagem para sua empresa?</strong><p>Este painel é uma amostra pública. O teste de 7 dias acompanha editais de climatização em SC e destaca o que merece sua análise.</p><a href="https://github.com/luizhenriquesan7os-create/radar-clima-sc/issues/new?title=Solicitar+teste+do+Radar+Clima+SC" target="_blank" rel="noopener">Solicitar teste gratuito ↗</a></div>
<section class="cards">{''.join(cards) if cards else '<p>Nenhum edital do recorte correspondeu aos termos configurados.</p>'}</section>
<footer>Fonte: Portal Nacional de Contratações Públicas. Produto independente, sem vínculo com o Governo Federal. Dados sujeitos a correção pelos órgãos publicadores.</footer></main></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=2, help="dias de publicação a consultar (padrão: 2)")
    parser.add_argument("--max-pages", type=int, default=20, help="máximo de páginas por modalidade")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--uf", default="SC", help="UF do órgão público; padrão SC")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--stateless", action="store_true", help="para hospedagem efêmera: omite a contagem de novos")
    args = parser.parse_args()
    if args.days < 1 or args.max_pages < 1 or not 10 <= args.page_size <= 50 or not re.fullmatch(r"[A-Z]{2}", args.uf):
        parser.error("days e max-pages positivos; page-size entre 10 e 50; UF com duas letras")
    args.out.mkdir(parents=True, exist_ok=True)
    rows, problems, read, expected = collect(args.days, args.page_size, args.max_pages, args.uf)
    if not rows:
        print("Nenhum dado foi recebido do PNCP; relatório anterior preservado.", file=sys.stderr)
        for p in problems: print(p, file=sys.stderr)
        return 2
    selected = shortlist(rows, DEFAULT_TERMS)
    old: set[str] = set()
    if not args.stateless:
        db = sqlite3.connect(args.out / "radar_historico.sqlite3")
        try:
            db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")
            old = {row[0] for row in db.execute("SELECT id FROM seen")}
            now = datetime.now(TZ).isoformat(timespec="seconds")
            db.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)", [(r["id"], now) for r in selected])
            db.commit()
        finally:
            db.close()
    run = {"at": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"), "read": read, "expected": expected,
           "matched": len(selected), "new": None if args.stateless else sum(r["id"] not in old for r in selected),
           "problems": problems, "days": args.days, "uf": args.uf, "source": BASE}
    (args.out / "radar_ultimo.html").write_text(render(selected, run), encoding="utf-8")
    with (args.out / "radar_ultimo.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(selected[0]) if selected else ["id","score","category","terms","object","published","deadline","days_left","value","agency","city","uf","modality","source","origin"])
        writer.writeheader(); writer.writerows(selected)
    (args.out / "radar_execucao.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
