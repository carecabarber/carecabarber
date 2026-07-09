# Migração para Railway — Runbook

> **Estado:** PREPARAÇÃO CONCLUÍDA. Nada foi migrado ainda. Todo o código e config
> abaixo já está no repositório, **dormente** — em PythonAnywhere continua
> byte-idêntico. O cutover é rápido quando decidires avançar.

## O que já ficou pronto (nesta preparação)

| Artefacto | Papel |
|-----------|-------|
| `railway.json` | Builder NIXPACKS (sem Docker), `startCommand` waitress, healthcheck `/health` |
| `runtime.txt` + `.python-version` | Fixam Python 3.12 (melhor disponibilidade de wheels) |
| `requirements.txt` | Adicionado `waitress` (servidor WSGI de produção) |
| `.env.example` | Todas as variáveis Railway documentadas |
| `wsgi.py` | `DB_PATH` do ambiente honrado (volume) |
| `helpers_booking.py` | `LOGOS_DIR` do ambiente (volume) |
| `app.py` | Rota de logos do volume, subdomínio por estabelecimento, beacon canónico — **tudo gated por env** |
| Templates beacon | URL canónico via `CANONICAL_URL` (segue para o novo domínio) |

**Gating = segurança:** sem as variáveis de ambiente definidas, todo este código
fica inerte. Por isso o PythonAnywhere não muda em nada até tu quereres.

## Regras respeitadas
- **Sem Docker** → builder NIXPACKS (se existisse um `Dockerfile`, o Railway usá-lo-ia).
- Sem Poetry, sem PostgreSQL, sem ORM. SQLite continua a ser a BD.

---

## Variáveis de ambiente (painel Railway → Variables)

| Variável | Valor | Obrigatória |
|----------|-------|:-----------:|
| `SECRET_KEY` | `python -c "import secrets;print(secrets.token_hex(32))"` | ✅ |
| `DB_PATH` | `/data/barbearia.db` | ✅ |
| `LOGOS_DIR` | `/data/logos` | ✅ |
| `VAPID_PRIVATE_KEY` | **a MESMA da produção actual** (senão invalida as subscrições push) | ✅ |
| `TENANT_BASE_DOMAIN` | `carecabarber.com` (para um URL por estabelecimento) | opcional |
| `CANONICAL_URL` | `https://carecabarber.com` | opcional |
| `SENTRY_DSN` | DSN do Sentry | opcional |

> O Railway injecta `PORT` automaticamente — **não** a definas. O `startCommand`
> já faz `waitress-serve --port=$PORT`.

## Volume persistente (CRÍTICO)

O filesystem do Railway é **efémero** — o que escreveres em disco perde-se a cada
deploy/reinício. A BD SQLite e os logos **têm** de viver num Volume.

1. Railway → serviço → **Volumes** → criar volume montado em `/data`.
2. `DB_PATH=/data/barbearia.db` e `LOGOS_DIR=/data/logos` (já nas variáveis acima).
3. Um único volume em `/data` chega para ambos (BD + logos).

---

## Um URL por estabelecimento (requisito)

Cada estabelecimento fica acessível em `<slug>.carecabarber.com` automaticamente —
sem verificação manual de domínio. Requer:

1. **DNS wildcard:** registo `CNAME  *.carecabarber.com → <o-teu-serviço>.up.railway.app`
   (ou o alvo que o Railway indicar em Settings → Networking → Custom Domain).
2. `TENANT_BASE_DOMAIN=carecabarber.com` nas variáveis.
3. Railway → adicionar o custom domain `*.carecabarber.com` (wildcard) ao serviço.

Com isto, `joao.carecabarber.com` → entrada de cliente do estabelecimento com
slug `joao`. Subdomínios reservados (`www`, `api`, `app`, `admin`, …) são ignorados.
Domínios próprios verificados (ex.: `salaojoao.pt`) continuam a funcionar em paralelo.

---

## Passos do cutover (quando decidires migrar)

1. **Criar projecto no Railway** a partir do repositório GitHub (deploy from repo).
2. **Definir as variáveis** da tabela acima.
3. **Criar o Volume** em `/data`.
4. **Primeiro deploy** (vai falhar o healthcheck — ainda não há BD no volume; é esperado).
5. **Carregar a BD** para o volume:
   - Fazer um backup fresco da produção PA (`barbearia.db`).
   - Enviar para `/data/barbearia.db` (via `railway run`/`railway ssh`, ou um
     endpoint/one-off script de upload). Enviar também os logos para `/data/logos`.
6. **Redeploy** → o healthcheck `/health` deve passar (verifica a BD).
7. **Verificação** (ver secção abaixo) no domínio `*.up.railway.app` do Railway.
8. **DNS cutover:** apontar `carecabarber.com` (e `*.carecabarber.com`) para o Railway.
9. Confirmar HTTPS emitido pelo Railway e revalidar.

## Verificação pós-deploy

```bash
# Substituir <URL> pelo domínio Railway ou final
curl -s https://<URL>/health           # 200 + db_ok
curl -s https://<URL>/healthz | jq .    # status ok, "sentry": true/false
curl -s https://<URL>/login -I          # 200 + headers de segurança (CSP, HSTS...)
curl -s https://<URL>/robots.txt        # bloqueio de bots IA
# Um URL por estabelecimento:
curl -sI https://<slug>.carecabarber.com/   # 302 → /cliente/<slug>
# Beacon aponta ao domínio canónico:
curl -s https://<URL>/login | grep px.gif   # deve conter CANONICAL_URL
```

Checklist manual: login de staff, marcação de cliente, upload de logo (confirmar
que persiste após um redeploy — prova o volume), notificações push (confirmam a
mesma VAPID key).

## Rollback

O PythonAnywhere fica intacto durante toda a migração (não vamos lá tocar).
Se algo correr mal no Railway: **reverter o DNS** para o PythonAnywhere. Como o
código é gated por env, o mesmo commit corre nos dois sítios sem alterações.

---

## Notas técnicas

- **`wsgi.py`** tem um loop de espera pela BD (até 90 s) pensado para o modelo
  multi-worker do PythonAnywhere (workers antigos a segurar o ficheiro). No Railway
  (processo único, volume dedicado) a BD está logo legível → o loop passa de
  imediato na 1ª tentativa. Sem efeito adverso.
- **Servidor:** `waitress` (8 threads). Compatível com o SQLite + `threading.Lock`
  já usados nos writes. Não usar o servidor de dev do Flask em produção.
- **Sem `Dockerfile` de propósito** — a presença de um faria o Railway ignorar o
  NIXPACKS e violar a regra "sem Docker".
