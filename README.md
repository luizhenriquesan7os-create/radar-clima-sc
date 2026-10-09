# Radar Clima SC

Painel público de licitações de climatização em Santa Catarina, gerado a partir da [API de dados abertos do PNCP](https://www.gov.br/pncp/pt-br/acesso-a-informacao/dados-abertos).

O filtro considera o objeto e a informação complementar de pregões eletrônicos e dispensas. Confirme sempre os itens, anexos, elegibilidade e prazos no edital oficial. O projeto não é afiliado ao Governo Federal.

**Amostra pública:** https://luizhenriquesan7os-create.github.io/radar-clima-sc/

## Execução local

Python 3.10+ e bibliotecas padrão:

```bash
python radar_pncp.py --days 2 --uf SC --out site --stateless
```

A publicação no GitHub Pages é executada pela rotina em `.github/workflows/publicar-radar.yml` cinco vezes por dia, se o serviço do GitHub e a API do PNCP estiverem disponíveis. A agenda pode sofrer atrasos conforme o GitHub Actions. Uma execução com erro não publica dados novos.

## Teste gratuito

Para solicitar uma triagem de 7 dias para sua empresa, [preencha o formulário](https://docs.google.com/forms/d/e/1FAIpQLSeNV-itqJ1q7X1LDfNAoEJ0wewCdeKKQNlPku8s_25oFnskFQ/viewform?usp=publish-editor). Informe apenas nome da empresa e email profissional. A solicitação não cria compromisso de compra.
