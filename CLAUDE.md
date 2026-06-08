# Barbearia App — Contexto para Claude

## Stack
- **Backend:** Flask 3.x + Flask-WTF (CSRF) + Werkzeug + SQLite
- **Frontend:** HTML/CSS/JS puro, PWA (`sw.js`, `manifest.json`)
- **App Android:** Capacitor em `../barbearia-app/`
- **Auth:** login tradicional + WebAuthn opcional (`_WEBAUTHN_OK` flag)
- **Deploy:** PythonAnywhere — corre atrás de nginx, usa `ProxyFix`
- **URL produção:** `https://carecabarber.pythonanywhere.com`

## Ficheiros principais
| Ficheiro | O que faz |
|----------|-----------|
| `app.py` | Toda a lógica Flask (~2950 linhas) |
| `database.py` | Acesso SQLite, cache de slots, fusos horários (~1950 linhas) |
| `barbearia.db` | Base de dados SQLite |
| `wsgi.py` | Entry point PythonAnywhere |
| `backup.py` | Backups automáticos |
| `templates/` | 25+ templates Jinja2 |
| `static/style.css` | CSS com design system (variáveis CSS) |
| `static/app.js` | JS principal do frontend |

## Schema da base de dados (colunas reais)

### agendamentos
| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | INTEGER | PK |
| `barbearia_id` | INTEGER NOT NULL | FK |
| `cliente` | TEXT NOT NULL | nome do cliente |
| `telefone` | TEXT | opcional |
| `servico_id` | INTEGER NOT NULL | FK |
| `barbeiro_id` | INTEGER | FK, pode ser NULL |
| `data_hora` | TEXT NOT NULL | formato `YYYY-MM-DD HH:MM:SS` |
| `inicio` | TEXT | quando serviço foi iniciado |
| `fim` | TEXT | quando serviço foi terminado |
| `status` | TEXT | `agendado`\|`walk-in`\|`em_andamento`\|`concluido`\|`cancelado` |
| `tipo` | TEXT | `agendado`\|`walk-in` |
| `valor` | INTEGER | valor cobrado (cêntimos ou inteiro) |
| `avaliacao` | INTEGER | 1-5, NULL se não avaliado |
| `token_reagendar` | TEXT | token para link de reagendamento |
| `token_avaliar` | TEXT | token para QR de avaliação/ação |
| `criado_em` | TEXT | timestamp de criação |
| `notas` | TEXT | notas internas |

### barbeiros
`id`, `nome`, `barbearia_id`, `ativo` (1/0), `role` (barbeiro\|chefe\|root), `username`, `password_hash`, `mesa_token`

### servicos
`id`, `barbearia_id`, `nome`, `duracao_min` (default 30), `preco` (default 0), `ativo` (1/0)

### configuracoes (chave/valor por barbearia)
| Chave | Valor |
|-------|-------|
| `timezone` | ex: `Atlantic/Cape_Verde` |
| `buffer_minutos` | minutos de buffer entre marcações (default 10) |
| `max_por_dia` | máximo de marcações por dia por barbeiro |

## Funções database.py (públicas)
```
# Barbearias
listar_barbearias(apenas_ativas) / get_barbearia(id) / get_barbearia_por_slug(slug)
criar_barbearia(nome) / toggle_barbearia(id) / editar_barbearia(id, nome) / set_logo(bid, filename)

# Config
get_config(chave, barbearia_id, default) / set_config(chave, valor, barbearia_id)
get_todas_configs(barbearia_id)

# Horários
get_horario(barbearia_id) / set_horario_dia(dia, hora_ab, hora_fe, fechado, bid)
listar_dias_fechados(bid) / adicionar_dia_fechado(data, motivo, bid) / dia_esta_fechado(data, bid)

# Barbeiros
listar_barbeiros(bid, apenas_ativos, incluir_chefe) / criar_barbeiro(nome, bid)
get_barbeiro(id) / get_barbeiro_por_username(username) / toggle_barbeiro(id)
set_credenciais(id, username, senha) / alterar_senha(id, nova_senha)
get_barbeiro_por_mesa_token(token)

# Ausências
listar_ausencias(bid, barbeiro_id) / criar_ausencia(barbeiro_id, data_ini, data_fim, tipo, motivo, hora_ini, hora_fim)
ausencia_ativa(barbeiro_id, data_str, hora_str) / apagar_ausencia(id)

# Serviços
listar_servicos(bid) / servico_por_id(id) / criar_servico(...) / editar_servico(...) / apagar_servico(id)

# Agendamentos
criar_agendamento(cliente_nome, servico_id, data_hora, bid, barbeiro_id, tipo, valor)
get_agendamento(id) / listar_hoje(bid, barbeiro_id) / listar_todos(bid, ...)
cancelar_agendamento(id) / reagendar_agendamento(id, nova_data_hora, novo_barbeiro_id, novo_servico_id)
iniciar_trabalho(id) / terminar_trabalho(id, valor) / marcar_nao_compareceu(id)
barbeiro_tem_em_andamento(barbeiro_id) / barbeiro_proxima_marcacao_minutos(barbeiro_id, bid)
verificar_disponibilidade(barbeiro_id, data_hora_str, duracao_min, bid, excluir_id)
horarios_disponiveis(barbeiro_id, data_str, duracao_min, bid)
get_agendamento_por_token(token) / get_agendamento_por_token_avaliar(token)
gerar_token_reagendar(agendamento_id)
deletar_walkin_orfao(id)

# Cache
invalidar_cache_slots(barbearia_id) / invalidar_cache_slots_completo()

# Fusos
_agora(barbearia_id) → datetime local / get_barbearia_tz(bid) / set_barbearia_tz(bid, tz_name)

# Estatísticas
estatisticas(bid, barbeiro_id) / estatisticas_detalhadas_barbeiro(barbeiro_id, bid)
tendencia_semanal(bid, barbeiro_id, semanas) / resumo_hoje(bid, barbeiro_id)
media_avaliacoes(bid, barbeiro_id)
```

## Jinja2 — filtros e globais em app.py
```
{{ valor | moeda }}          → formata preço (inteiro → "1.500 CVE")
{{ lista | omit_keys('k') }} → remove chaves de lista de dicts
{{ tel | tel }}              → formata telefone
bid()                        → session['barbearia_id'] (helper global)
csp_nonce                    → nonce para CSP inline scripts (usar em <script nonce="{{ csp_nonce }}">)
```

## Design system — variáveis CSS (style.css)
```css
/* Cores de fundo */
--bg: #070706;  --surface: #0f0e0c;  --surface2: #161410;
--surface3: #1d1b16;  --surface4: #252219;

/* Accent (dourado) */
--accent: #f0b429;  --accent-dim: #c49010;  --accent-glow: rgba(240,180,41,0.14);

/* Texto */
--text: #ebebeb;  --text2: #787060;  --text3: #403c34;

/* Estado */
--green: #22c55e;  --red: #ef4444;  --blue: #3b82f6;

/* Bordas */
--border: rgba(255,235,170,0.07);  --border-hi: rgba(255,235,170,0.13);

/* Raios */
--radius: 14px;  --radius-sm: 10px;  --radius-pill: 999px;

/* Sombras */
--sh-xs / --sh-sm / --sh / --sh-lg / --sh-accent
```

## Arquitectura multi-tenant
- `root` — superadmin que gere todas as barbearias via `/root`
- `bid()` → retorna `session['barbearia_id']` — usar em todas as rotas de staff
- Cada barbearia tem `slug` único (URL cliente: `/cliente/<slug>`)
- Sessão Flask: `user_id`, `user_nome`, `role`, `barbearia_id`
- Decoradores: `@staff_required`, `@chefe_required`, `@root_required`

## Fusos horários
- Default: `Atlantic/Cape_Verde` (Cabo Verde — onde a barbearia opera)
- Sempre usar `_agora(barbearia_id)` em vez de `datetime.now()`
- Cache de fuso: TTL 5 min (`_TZ_CACHE_TTL = 300`)

## Cache de slots
- Em memória, TTL 60s (datas futuras) / 15s (hoje)
- Máx 300 entradas — evita OOM no PythonAnywhere
- **Sempre** chamar `_invalidar_idx(barbearia_id)` após criar/cancelar/reagendar
- PythonAnywhere tem workers separados — cache não é partilhada (comportamento esperado)

## Segurança
- CSRF em todos os POSTs — rotas públicas (mesa, cliente, ag) têm `@csrf.exempt`
- `WTF_CSRF_SSL_STRICT = False` e `WTF_CSRF_TIME_LIMIT = None` — obrigatório para PA
- Rate limiting: `_ip_ok()` para login, `_api_ok()` para APIs da mesa
- Validação de imagens por magic bytes (`_IMG_MAGIC`) — não confiar só na extensão
- Logging de segurança → stderr (PA não suporta RotatingFileHandler)
- `_booking_lock` — threading.Lock para operações de agendamento (evita race conditions)

## Rotas por papel
| Rota | Papel | Notas |
|------|-------|-------|
| `/login`, `/logout` | Todos | |
| `/` | Staff | Agenda do dia |
| `/novo` | Staff | Nova marcação manual |
| `/walkin` | Staff | Walk-in imediato |
| `/historico` | Staff | Histórico + exportar CSV |
| `/estatisticas` | Chefe | Stats da barbearia |
| `/barbeiros` | Chefe | Gerir equipa |
| `/servicos` | Chefe | Gerir serviços |
| `/configuracoes` | Chefe | Configurações |
| `/perfil` | Staff | Perfil + QR da mesa + biometria |
| `/cliente/<slug>` | Cliente | Página pública |
| `/cliente/<slug>/marcar` | Cliente | Marcação online |
| `/mesa/<token>` | Tablet | Autoatendimento QR (sem auth) |
| `/mesa/<token>/entrar` | Cliente | Escanear QR → escolher serviço |
| `/ag/<token>` | Cliente | Iniciar/terminar via QR pessoal |
| `/root` | Root | Gerir todas as barbearias |

## base.html — Globals e Auto-behaviors

### Context processors (auto-disponíveis em TODOS os templates sem passar)
| Variable | O que é |
|----------|---------|
| `csp_nonce` | Nonce para `<script nonce="{{ csp_nonce }}">` |
| `agora_iso` | Datetime servidor em ISO — injectado como `_SERVER_NOW` no JS |
| `_wn_ok` | True se WebAuthn está activo (`_WEBAUTHN_OK` flag) |
| `_wn_user_id` | ID do utilizador actual (para chaves localStorage do WebAuthn) |
| `csrf_token()` | Função CSRF do Flask-WTF |
| `request`, `session` | Objectos Flask padrão |
| `bid()` | Shortcut para `session['barbearia_id']` |

### JS globals (disponíveis em todas as páginas após carregar app.js)
- `_SERVER_NOW` — string ISO da hora do servidor (Cape Verde); usado por `_horaServidor()`
- Todas as funções de `app.js`: `mostrarToast()`, `abrirModal()`, `fecharModal()`, `confirmarTerminar()`, `toggleFab()`, `_fetch()`, `notificar()`, `setStar()`

### Auto-behaviors do base.html (não precisam de código extra)
- **CSRF auto-inject** — base.html injeta `<input name="csrf_token">` em TODOS os forms via JS → nunca precisas de adicionar manualmente (mas não faz mal se existir)
- **Flash messages → toasts** — `flash(msg, 'sucesso'|'erro'|'aviso'|'info')` no Python aparece automaticamente como toast no frontend
- **`data-confirm="mensagem"`** em qualquer `<form>` → mostra `confirm()` antes de submeter (substitui `onsubmit=confirm()`)
- **Fuso timezone cookie** — `tz=<browser tz>` definido automaticamente; lido pelo Flask para contexto horário
- **Modal terminar** — definido no base.html, sempre presente; IDs: `#modalOverlay`, `#modalBox`, `#modalCliente`, `#modalServico`, `#modalDuracao`, `#modalValor`, `#modalAvaliacao`, `#modalStars`

### Navegação inferior (bottom nav)
Sempre presente: Hoje (`/`), Histórico, Stats, Gerir (só `role=='chefe'`), Perfil.
Para marcar tab activa: `class="bnav-item {% if request.endpoint=='nome_rota' %}active{% endif %}"`

## Padrões HTML (componentes do design system)

```html
<!-- Page header -->
<div class="page-header"><h1>Título da Página</h1></div>

<!-- Section title -->
<h2 class="section-title">Subtítulo</h2>

<!-- Card básico -->
<div class="card">...</div>

<!-- Card de cliente em atendimento (fundo especial) -->
<div class="card card-active">...</div>

<!-- Campo de formulário -->
<div class="field">
    <label>Label</label>
    <input type="text" name="campo" placeholder="...">
</div>

<!-- Formulário em card (login, perfil, etc.) -->
<form class="form-card" method="post">...</form>

<!-- Botões -->
<button class="btn btn-primary">Ação principal</button>
<button class="btn btn-primary btn-full">Full width</button>
<button class="btn btn-cancel">Cancelar</button>
<button class="btn btn-outline-small">Pequeno outline</button>

<!-- Badges -->
<span class="badge badge-walkin">Walk-in</span>
<span class="badge badge-visita">3 visitas</span>

<!-- Status classes (em .item-agendamento) -->
<!-- status-agendado | status-em_andamento | status-concluido | status-cancelado -->
<li class="item-agendamento status-agendado">
    <div class="item-hora">HH:MM</div>
    <div class="item-body">
        <div class="item-cliente">Nome Cliente</div>
        <div class="item-servico">Serviço · NN min</div>
    </div>
    <div class="item-acoes"><!-- botões --></div>
</li>

<!-- Resumo bar (números de topo) -->
<div class="resumo-bar">
    <div class="resumo-item">
        <span class="resumo-val">42</span>
        <span class="resumo-label">Clientes</span>
    </div>
    <div class="resumo-sep"></div>
    ...
</div>

<!-- Cronómetro (auto-iniciado pelo app.js) -->
<span class="cronometro" data-id="{{ a.id }}"
      data-segundos="{{ segundos_decorridos }}"
      data-estimado="{{ servico.duracao_min * 60 }}">00:00</span>
<div class="alerta-atraso" id="alerta-{{ a.id }}" style="display:none">⚠️ Em atraso</div>

<!-- Contacto bar -->
<div class="contacto-bar">
    <a href="tel:{{ a.telefone }}" class="btn-contacto">📞 {{ a.telefone | tel }}</a>
    <a href="https://wa.me/238{{ a.telefone | replace(' ','') }}"
       class="btn-contacto btn-whatsapp" target="_blank">💬 WhatsApp</a>
</div>

<!-- Botão terminar (usa data-* para XSS safety — não usar onclick= com strings) -->
<button type="button" class="btn btn-terminar js-terminar-btn"
        data-id="{{ a.id }}" data-cliente="{{ a.cliente | e }}"
        data-duracao="{{ servico.duracao_min }}"
        data-servico="{{ servico.nome | e }}"
        data-preco="{{ a.valor or servico.preco }}">Terminar</button>
<!-- Necessita listener no base.html: document.addEventListener('click', e => {
       const btn = e.target.closest('.js-terminar-btn'); if (btn) abrirModalBtn(btn); }) -->

<!-- Form oculto necessário para confirmarTerminar() funcionar -->
<form id="form-terminar-{{ a.id }}" method="post" action="/terminar/{{ a.id }}" style="display:none">
    <input type="hidden" name="valor" id="valor-input-{{ a.id }}">
</form>
```

## Convenções de código
- SQL **nunca** em `app.py` — sempre via funções de `database.py`
- `db._write()` / `db._read()` — context managers, nunca chamar `get_conn()` directamente
- Datas: `YYYY-MM-DD`, horas: `HH:MM`, datetime completo: `YYYY-MM-DD HH:MM:SS`
- Flash messages: `flash(msg, 'sucesso'|'erro'|'aviso'|'info')` (em PT, não EN)
- `_limpar(v, maxlen)` para sanitizar strings de utilizador
- `_val_data(v)` e `_val_hora(v)` para validar antes de usar em SQL
- JSON errors: `jsonify({'error': msg}), 4xx`
- Templates recebem variáveis explícitas — não aceder a `session` no template para dados do barbeiro

## Armadilhas conhecidas
- **Cache slots** — após qualquer escrita que afecte disponibilidade → `_invalidar_idx(bid)`
- **PythonAnywhere workers** — cache em memória não é partilhada; comportamento esperado
- **WebAuthn** — só funciona em HTTPS; desactivado automaticamente em localhost
- **ProxyFix** — obrigatório; sem ele CSRF falha (cookies secure vs. proxy)
- **Logos** — guardar em `static/logos/{barbearia_id}/`; criar pasta antes
- **RotatingFileHandler** — não usar no PA (OSError); usar stderr
- **Templates e sessão** — NÃO usar `session.get('user_nome')` nos templates para dados do barbeiro; passar `barbeiro=barb_atual` explicitamente da rota e usar `barbeiro.nome` *(bug corrigido 18/05/2026)*
- **bidfax** — (VIN Remover) usa minúsculas no URL; usar `{vin_lower}`

## Template Variables por Página
| Template | Variáveis passadas pela rota |
|----------|------------------------------|
| `index.html` | `agendamentos`, `agora`, `resumo`, `resumo_fim_dia`, `tz_barbearia`, `csp_nonce` |
| `perfil.html` | `erro`, `ok`, `credenciais`, `mesa_token`, `barbeiro`, `csp_nonce` |
| `mesa.html` | `ag`, `barbearia`, `barbeiro`, `mesa_token`, `s` (servico), `csp_nonce` |
| `mesa_entrar.html` | `barbearia`, `barbeiro`, `mesa_token`, `s` (servicos), `csp_nonce` |
| `novo.html` | `b` (barbeiros), `s` (servicos), `hoje`, `erro`, `csp_nonce` |
| `walkin.html` | `b` (barbeiros), `s` (servicos), `hora_fecho`, `csp_nonce` |
| `barbeiros.html` | `b` (barbeiros), `barbeiros`, `aus` (ausencias), `hoje`, `csp_nonce` |
| `historico.html` | `a` (agendamentos), `b` (barbeiros), `d` (data), `datas`, `pagina`, `total_paginas`, `total_cortes`, `total_valor`, `val_dia`, `n_dia`, `csp_nonce` |
| `estatisticas.html` | `a` (avaliacao), `b` (barbeiros), `s` (servicos), `stats`, `tendencia`, `label`, `w` (semanas), `csp_nonce` |
| `configuracoes.html` | `barbearia`, `configs`, `d` (dias), `h` (horarios), `hoje`, `tz_label`, `tz_val`, `csp_nonce` |
| `cliente_marcar.html` | `b` (barbeiros), `barbearia`, `s` (servicos), `hoje`, `agora`, `erro` |
| `cliente_confirmacao.html` | `ag`, `servico` (s), `barbeiro` (b), `barbearia` |
| `cliente_entrada.html` | `erro`, `barbearia` |
| `cliente_home.html` | `agendamentos` (enriquecidos), `barbearia` |
| `ag_acao.html` | `ag`, `barbearia`, `barbeiro`, `servico`, `erro`, `token`, `csp_nonce` |
| `avaliar_link.html` | `ag`, `servico` (s), `barbeiro` (b), `barbearia`, `sucesso`, `ja_avaliou` |
| `cancelar_link.html` | `ag` (enriquecer), `barbearia`, `erro_cancelar` |
| `cancelar_link_ok.html` | `ag` (enriquecer), `barbearia` |
| `reagendar.html` | `ag` (enriquecer), `servicos`, `barbeiros`, `hoje`, `erro`, `origem` ("cliente"\|"staff"), `barbearia` |
| `reagendar_link_ok.html` | `ag` (enriquecer), `barbearia` |
| `root.html` | `barbearias`, `erro`, `ok` |
| `servicos.html` | `servicos` |
| `estatisticas_barbeiro.html` | `det`, `dias_pt`, `is_chefe` |
| `erro_simples.html` | `msg` |
| `offline.html` | *(sem variáveis)* |

**`enriquecer(ag)` / `enriquecer_lista(ags)`** — adiciona campos ao dict de agendamento:
`servico_nome`, `duracao_estimada`, `preco_servico`, `barbeiro_nome`, `num_visitas`, `segundos_decorridos`, `hora_fim_estimada`
→ Usar sempre em vez de acesso directo ao `ag` cru da BD.

**Regra:** Sempre passar `csp_nonce=csp_nonce` a TODOS os render_template. Barbeiro actual: `barb_atual = db.get_barbeiro(session['user_id'])` → passar como `barbeiro=barb_atual`.

## app.js — Padrões e Funções

### Modal de terminar serviço
```javascript
// Abrir modal (modo seguro via data-* — evita XSS)
abrirModalBtn(btn)   // lê data-id, data-cliente, data-duracao, data-servico, data-preco
// Ou directamente:
abrirModal(id, cliente, duracaoEstimada, servicoNome, precoServico)
fecharModal()        // fecha; se _reloadPending → reload automático
confirmarTerminar()  // anti-double-click, submete form-terminar-{id}
```
Flags: `_modalAberto` (true enquanto modal aberto), `_reloadPending` (reload pendente).

### Toast e notificações
```javascript
mostrarToast(msg, tipo)  // tipo: "sucesso" (verde), "aviso" (azul), "erro" (vermelho, default)
notificar(titulo, corpo) // notificação nativa — só dispara se tab em 2.º plano
pedirPermissaoNotificacao()  // pedir permissão (chamado no DOMContentLoaded)
```

### Polling e auto-refresh
```javascript
_fetch(url, opts)  // fetch com timeout automático (8s), cancela pedido anterior
verificarEstadoPagina()        // GET /api/estado → {h: "hash"} — reload se hash mudou
verificarNovosAgendamentos()   // GET /api/novos-agendamentos?desde_id=N → lista
verificarLembretes()           // GET /api/lembretes → lista com minutos_ate
```
- Intervalo adaptativo: 30s das 8h-20h, 2min fora de horas
- `_modalAberto=true` → suspende reload (usa `_reloadPending`)
- Polling de lembretes e novos agendamentos **só na dashboard** (`/`)

### Hora do servidor (Cabo Verde)
```javascript
_horaServidor()  // DateTime calibrado com offset servidor — usar sempre em vez de new Date()
// Calibrado em DOMContentLoaded a partir de _SERVER_NOW (variável global do template base)
```

### Cronómetros
HTML: `<span class="cronometro" data-id="{id}" data-segundos="{s}" data-estimado="{min*60}">`
JS: auto-arrancado no DOMContentLoaded. Adiciona classe `em-atraso` quando ultrapassa estimado.

### FAB (botão flutuante)
```javascript
toggleFab()  // toggle #fabBtn e #fabMenu com classe "open"
// Clique fora do .fab-group fecha automaticamente
```

## APIs Internas — Respostas
| Endpoint | Resposta |
|----------|----------|
| `GET /api/estado` | `{"h": "abc123"}` — hash do estado actual dos agendamentos |
| `GET /api/novos-agendamentos?desde_id=N` | `[{"id": N, "cliente": "...", "servico": "...", "barbeiro": "...", "hora": "HH:MM", "tipo": "agendado\|walk-in"}]` |
| `GET /api/lembretes` | `[{"id": N, "cliente": "...", "servico": "...", "hora": "HH:MM", "telefone": "...", "minutos_ate": N}]` |
| `GET /api/meu-status` | `[{"id": N, "servico": "...", "barbeiro": "..."}]` — agendamentos em_andamento do cliente |

## Receitas Comuns

### Adicionar nova rota (staff)
```python
@app.route('/nova-rota', methods=['GET', 'POST'])
@staff_required
def nova_rota():
    barb_atual = db.get_barbeiro(session['user_id'])
    # lógica...
    return render_template('nova_rota.html',
                           barbeiro=barb_atual,
                           csp_nonce=csp_nonce)
```

### Adicionar nova rota (API JSON)
```python
@app.route('/api/nova', methods=['POST'])
@staff_required
def api_nova():
    data = request.get_json(silent=True) or {}
    val = _limpar(data.get('campo', ''), 100)
    if not val:
        return jsonify({'error': 'Campo obrigatório'}), 400
    # lógica...
    return jsonify({'ok': True})
```

### Adicionar coluna à BD
```python
# Em database.py, na função de inicialização ou migração:
with _write() as conn:
    conn.execute("ALTER TABLE agendamentos ADD COLUMN nova_coluna TEXT")
# Atualizar schema em CLAUDE.md após alterar
```

### Adicionar chave de configuração
```python
# Ler:  db.get_config('nova_chave', barbearia_id, 'valor_default')
# Guardar: db.set_config('nova_chave', valor, barbearia_id)
# Expor em configuracoes.html: adicionar campo + submit na rota /configuracoes
```

### Invalidar cache após escrita
```python
# SEMPRE após criar/cancelar/reagendar/terminar agendamento:
db._invalidar_idx(bid())
# ou:
db.invalidar_cache_slots(barbearia_id)
```

## Previsão de Pedidos Comuns
- **"Adicionar campo ao agendamento"** → alterar schema `agendamentos` + `criar_agendamento()` + forms HTML + CLAUDE.md
- **"Nova página de relatório/stats"** → rota `@chefe_required` + template + link na nav de chefe
- **"Novo tipo de notificação"** → `mostrarToast()` + `notificar()` no app.js + endpoint `/api/...`
- **"Enviar SMS/WhatsApp"** → implementar em `app.py` via API externa; guardar `telefone` do agendamento
- **"Filtro no histórico"** → `listar_todos(bid, ...)` já aceita filtros; adicionar param na rota + select no HTML
- **"Exportar PDF"** → usar `weasyprint` ou `reportlab`; rota separada que devolve `send_file()`

## Deploy — Automático (PostToolUse hook)
O projeto tem deploy automático para PythonAnywhere configurado via Claude Code hook:

- **Hook:** `.claude/settings.json` → `PostToolUse` em `Write|Edit` → chama `.claude/deploy.sh`
- **deploy.sh** — lê credenciais de `.pythonanywhere`, faz upload + reload + health check
- **Credenciais** em `.pythonanywhere` (gitignored): `USER`, `DOMAIN`, `API_TOKEN`, `API_BASE`, `SSH_HOST`
- **Throttle:** reload apenas se passaram >30s desde o último (stamp em `/tmp/carecabarber_last_reload`)
- **Health check:** após reload, `curl /login` → verifica HTTP 200/302 antes de reportar sucesso
- **Log:** `/tmp/barbearia_deploy.log` — audit trail de todos os deploys
- **Excluções:** `.claude/*`, `*.pyc`, `*.db`, `backups/*`, ficheiros de QR — não são enviados
- **Caminho remoto:** `/home/CarecaBarber/barbearia/` no PythonAnywhere

```bash
# Deploy de TODOS os ficheiros (setup inicial ou sync completo):
bash .claude/deploy_all.sh

# Dry-run (lista ficheiros sem enviar):
bash .claude/deploy_all.sh --dry-run

# Deploy manual de um ficheiro específico:
echo '{"tool_input":{"file_path":"/home/helder-neves/Documentos/barbearia/app.py"}}' \
  | bash .claude/deploy.sh

# Ver log de deploys:
cat /tmp/barbearia_deploy.log

# Reload manual (ler token de .pythonanywhere):
source .pythonanywhere
curl -X POST -H "Authorization: Token $API_TOKEN" \
  "$API_BASE/webapps/$DOMAIN/reload/"
```

## App Android (Capacitor)
- **Localização:** `~/Documentos/barbearia-app/`
- **Framework:** Capacitor 6 + Android (Gradle)
- **Ficheiro de configuração:** `capacitor.config.json` — aponta para `https://carecabarber.pythonanywhere.com`
- **Build:** `barbearia-app/build-android.sh` — compila o APK
- **Android manifest:** `android/app/src/main/AndroidManifest.xml`
- **Nota:** a app Android é essencialmente um WebView da PWA em produção; não tem código nativo

## Backup automático (`backup.py`)
- Usa **SQLite Online Backup API** (`sqlite3.connect().backup()`) — seguro com escritas concorrentes
- Copia `barbearia.db` → `~/backups/barbearia_YYYYMMDD_HHMMSS.db`
- Mantém máx 30 backups (apaga os mais antigos); se falhar, limpa o ficheiro parcial
- Corre diariamente via **PythonAnywhere Scheduled Tasks**
- Não precisa de alterações em deploy normal

## Deploy — PythonAnywhere (informação base)
- WSGI em `/var/www/...wsgi.py` → aponta para `wsgi.py` local
- Logs de erro: `/var/log/...error.log`
- `SECRET_KEY` via env var ou `.secret_key` (gerado automaticamente na 1ª execução)
- `WEBAUTHN_RP_ID` e `WEBAUTHN_ORIGIN` via env vars para WebAuthn em produção
- SSH: `ssh.eu.pythonanywhere.com` — user `CarecaBarber`
