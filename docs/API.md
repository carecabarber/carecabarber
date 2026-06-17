# API Reference — CarecaBarber

> URL base (produção): `https://carecabarber.pythonanywhere.com`
> Autenticação: sessão Flask (cookie). Todas as rotas assinaladas com 🔒 exigem login.
> Respostas: JSON, salvo indicação contrária.

---

## Índice

1. [Auth](#1-auth)
2. [Dashboard / Agenda](#2-dashboard--agenda)
3. [Agendamentos](#3-agendamentos)
4. [API JSON (tempo real)](#4-api-json-tempo-real)
5. [Clientes (área pública)](#5-clientes-área-pública)
6. [Estatísticas & PDF](#6-estatísticas--pdf)
7. [Barbeiros](#7-barbeiros)
8. [Serviços](#8-serviços)
9. [Configurações](#9-configurações)
10. [Push Notifications](#10-push-notifications)
11. [Mesa (QR Code Walk-in)](#11-mesa-qr-code-walk-in)
12. [Root (super-admin)](#12-root-super-admin)
13. [PWA / Sistema](#13-pwa--sistema)

---

## 1. Auth

### `GET /login`
Página de login.

### `POST /login`
Autenticar utilizador.

| Campo | Tipo | Obrigatório |
|-------|------|-------------|
| `username` | string | ✓ |
| `password` | string | ✓ |

**Resposta**: redireciona para `/` em caso de sucesso ou devolve a página com erro.

### `POST /logout` 🔒
Termina a sessão e redireciona para `/login`.

---

## 2. Dashboard / Agenda

### `GET /` 🔒
Dashboard principal com agenda do dia.

**Query params opcionais:**
- `barbeiro_id` (int) — filtrar por barbeiro

**Requer:** role `chefe` ou `barbeiro`

---

## 3. Agendamentos

### `GET /novo` 🔒
Formulário de novo agendamento.

### `POST /novo` 🔒
Criar agendamento.

| Campo | Tipo | Obrigatório |
|-------|------|-------------|
| `cliente` | string | ✓ |
| `telefone` | string | — |
| `servico_id` | int | ✓ |
| `barbeiro_id` | int | — |
| `data` | string (YYYY-MM-DD) | ✓ |
| `hora` | string (HH:MM) | ✓ |

### `GET /walkin` 🔒
Página de walk-in (atendimento imediato sem marcação).

### `POST /walkin` 🔒
Criar walk-in.

### `POST /iniciar/<id>` 🔒
Iniciar atendimento de um agendamento.

### `POST /terminar/<id>` 🔒
Concluir atendimento (registar valor cobrado).

| Campo | Tipo | Obrigatório |
|-------|------|-------------|
| `valor` | int (centavos) | ✓ |

### `POST /cancelar/<id>` 🔒
Cancelar agendamento. Notifica a fila de espera automaticamente.

### `POST /nao-compareceu/<id>` 🔒
Marcar cliente como não compareceu.

### `POST /bloquear` 🔒
Bloquear intervalo de tempo no horário.

### `POST /desbloquear/<id>` 🔒
Remover bloqueio de horário.

### `POST /avaliar/<id>` 🔒
Registar avaliação interna (1–5 estrelas) de um atendimento.

### `GET /reagendar/<id>` 🔒
Formulário de reagendamento.

### `POST /reagendar/<id>` 🔒
Reagendar agendamento existente.

### `GET /historico` 🔒
Histórico de atendimentos com filtros de data e barbeiro.

**Query params:**
- `data_ini` (YYYY-MM-DD)
- `data_fim` (YYYY-MM-DD)
- `barbeiro_id` (int)
- `page` (int, default 1)

### `GET /historico/exportar.csv` 🔒
Exportar histórico filtrado em CSV.

### `GET /minhas-marcacoes` 🔒
Marcações futuras do barbeiro autenticado.

### `GET /ag/<token>`
Aceder a agendamento por link com token (sem login).

### `GET /avaliar-link/<token>` | `POST /avaliar-link/<token>`
Página pública de avaliação enviada por SMS/WhatsApp.

### `GET /reagendar-link/<token>` | `POST /reagendar-link/<token>`
Reagendar via link enviado por SMS.

### `GET /cancelar-link/<token>` | `POST /cancelar-link/<token>`
Cancelar via link enviado por SMS.

---

## 4. API JSON (tempo real)

### `GET /api/push/vapid-public`
Chave pública VAPID para subscrição de push.

**Resposta:**
```json
{ "publicKey": "BFQ..." }
```

### `GET /api/slots`
Slots disponíveis para marcação.

**Query params:**
- `data` (YYYY-MM-DD) — obrigatório
- `servico_id` (int) — obrigatório
- `barbeiro_id` (int) — opcional
- `barbearia_id` (int) — opcional

**Resposta:**
```json
{ "slots": ["09:00", "09:30", "10:00"] }
```

### `GET /api/tempo/<id>` 🔒
Tempo decorrido de um atendimento em curso.

**Resposta:**
```json
{ "segundos": 1234, "status": "em_andamento" }
```

### `GET /api/lembretes` 🔒
Verifica agendamentos que necessitam de lembrete.

**Resposta:**
```json
{ "lembretes": [ { "id": 1, "cliente": "João", ... } ] }
```

### `GET /api/meu-status` 🔒
Estado actual do barbeiro autenticado (em atendimento ou livre).

**Resposta:**
```json
{ "em_atendimento": false, "ag_id": null }
```

### `GET /api/novos-agendamentos` 🔒
Polling de novos agendamentos desde um timestamp.

**Query params:**
- `desde` (ISO datetime)

**Resposta:**
```json
{ "novos": 2, "agendamentos": [ {...} ] }
```

### `GET /api/estado` 🔒
Estado resumido da agenda (polling mobile).

**Resposta:**
```json
{ "em_andamento": [...], "agendados": [...], "resumo": { "clientes": 5, "valor": 5000 } }
```

---

## 5. Clientes (área pública)

### `GET /cliente/<slug>`
Página de entrada do cliente (inserir telefone). Mostra avaliações da barbearia.

### `POST /cliente/<slug>`
Autenticar cliente com telefone + OTP.

| Campo | Tipo |
|-------|------|
| `telefone` | string |
| `otp` | string (6 dígitos) |

### `GET /cliente/<slug>/area`
Área pessoal do cliente — marcações, fila de espera, lembrete push.

### `GET /cliente/<slug>/marcar` | `POST /cliente/<slug>/marcar`
Formulário de nova marcação pelo cliente.

### `POST /cliente/<slug>/cancelar/<id>`
Cancelar marcação pelo cliente (só `status=agendado`).

### `GET /cliente/<slug>/reagendar/<id>` | `POST /cliente/<slug>/reagendar/<id>`
Reagendar marcação pelo cliente.

### `GET /cliente/<slug>/confirmacao/<id>`
Página de confirmação após marcação.

### `POST /cliente/<slug>/iniciar-servico/<id>`
Iniciar serviço walk-in pelo cliente via QR (mesa).

### `POST /cliente/<slug>/terminar-servico/<id>`
Terminar serviço walk-in pelo cliente via QR (mesa).

### `POST /cliente/<slug>/fila-espera`
Entrar na fila de espera para uma data sem disponibilidade.

| Campo | Tipo |
|-------|------|
| `data` | string (YYYY-MM-DD) |
| `servico_id` | int |
| `barbeiro_id` | int (opcional) |

### `POST /cliente/<slug>/dispensar-espera/<id>`
Dispensar notificação de slot disponível na fila de espera.

---

## 6. Estatísticas & PDF

### `GET /estatisticas` 🔒
Dashboard de estatísticas (só chefe → inclui todos; barbeiro → só as próprias).

### `GET /estatisticas/barbeiro/<id>` 🔒
Estatísticas detalhadas de um barbeiro específico.

### `GET /relatorio-pdf` 🔒 (chefe)
Gerar relatório mensal em PDF.

**Query params:**
- `mes` (YYYY-MM) — mês completo
- `data_ini` + `data_fim` (YYYY-MM-DD) — intervalo personalizado
- `barbeiro_id` (int) — filtrar por barbeiro

**Resposta:** `application/pdf` com header `Content-Disposition: attachment`.

---

## 7. Barbeiros

### `GET /barbeiros` 🔒 (chefe)
Listar barbeiros da barbearia.

### `POST /barbeiros` 🔒 (chefe)
Criar novo barbeiro.

| Campo | Tipo |
|-------|------|
| `nome` | string |
| `username` | string |
| `senha` | string |

### `POST /barbeiros/editar/<id>` 🔒 (chefe)
Editar dados do barbeiro.

### `POST /barbeiros/toggle/<id>` 🔒 (chefe)
Activar/desactivar barbeiro.

### `POST /barbeiros/apagar/<id>` 🔒 (chefe)
Apagar barbeiro (só se não tiver atendimentos).

### `POST /barbeiros/credenciais/<id>` 🔒 (chefe)
Alterar username/senha do barbeiro.

### `POST /barbeiros/repor-senha/<id>` 🔒 (chefe)
Repor senha para valor temporário.

### `POST /barbeiros/<id>/foto` 🔒
Upload de foto de perfil (multipart/form-data, campo `foto`).

### `POST /barbeiros/<id>/foto/apagar` 🔒
Apagar foto de perfil.

### `POST /barbeiros/<id>/pausa-almoco` 🔒 (chefe)
Definir pausa de almoço permanente do barbeiro.

| Campo | Tipo |
|-------|------|
| `pausa_almoco_inicio` | string (HH:MM) |
| `pausa_almoco_fim` | string (HH:MM) |

### `POST /barbeiros/ausencia` 🔒 (chefe)
Registar ausência/feriado.

### `POST /barbeiros/ausencia/apagar/<id>` 🔒 (chefe)
Remover ausência.

### `GET /foto/<barbeiro_id>`
Foto de perfil (PNG). Pública (sem login).

---

## 8. Serviços

### `GET /servicos` 🔒 (chefe)
Listar serviços da barbearia.

### `POST /servicos` 🔒 (chefe)
Criar serviço.

| Campo | Tipo |
|-------|------|
| `nome` | string |
| `duracao` | int (minutos) |
| `preco` | int (valor na moeda local) |

### `POST /servicos/editar/<id>` 🔒 (chefe)
Editar serviço existente.

### `POST /servicos/apagar/<id>` 🔒 (chefe)
Apagar serviço (só se não houver agendamentos futuros).

---

## 9. Configurações

### `GET /configuracoes` 🔒 (chefe)
Página de configurações da barbearia.

### `POST /configuracoes` 🔒 (chefe)
Guardar configurações.

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `nome` | string | Nome da barbearia |
| `tipo` | string | `barbearia` / `salao` / `clinica` / `outro` |
| `moeda` | string | `ECV` / `EUR` / `BRL` / `USD` |
| `horario_*` | string | Horário por dia da semana |
| `duracao_padrao` | int | Duração padrão dos serviços (min) |
| `max_vagas_por_slot` | int | Vagas simultâneas por horário |
| `tz` | string | Fuso horário (ex: `Atlantic/Cape_Verde`) |

### `GET /perfil` 🔒
Página de perfil do utilizador autenticado.

### `POST /perfil` 🔒
Actualizar dados de perfil (nome, senha).

### `POST /perfil/foto` 🔒
Upload de foto de perfil.

### `POST /perfil/foto/apagar` 🔒
Apagar foto de perfil.

---

## 10. Push Notifications

### `POST /api/push/subscribe` 🔒
Subscrever push notifications para staff.

**Body JSON:**
```json
{ "endpoint": "...", "p256dh": "...", "auth": "..." }
```

**Resposta:**
```json
{ "ok": true }
```

### `POST /api/push/unsubscribe` 🔒
Cancelar subscrição de push do staff.

**Body JSON:**
```json
{ "endpoint": "..." }
```

### `POST /api/cliente-push/subscribe`
Subscrever push notifications para cliente autenticado.

**Requer:** sessão de cliente (`role=cliente`)

**Body JSON:**
```json
{ "endpoint": "...", "p256dh": "...", "auth": "..." }
```

**Respostas:**
- `200` — `{ "ok": true }`
- `400` — dados incompletos
- `403` — não autorizado
- `503` — push não disponível neste servidor

### `POST /api/cliente-push/unsubscribe`
Cancelar subscrição push do cliente.

**Requer:** sessão de cliente

**Body JSON:**
```json
{ "endpoint": "..." }
```

---

## 11. Mesa (QR Code Walk-in)

Sistema de QR code para walk-in autónomo pelo cliente.

### `GET /mesa/<token>`
Página de espera/serviço em curso (o cliente vê após scan).

### `GET /mesa/<token>/entrar`
Formulário de entrada (seleccionar serviço).

### `GET /mesa/<token>/info`
Estado actual da mesa.

**Resposta:**
```json
{ "livre": true, "servico": null }
```

### `POST /mesa/<token>/iniciar`
Iniciar serviço na mesa via QR.

### `POST /mesa/<token>/terminar`
Terminar serviço na mesa via QR.

### `POST /mesa/<token>/walkin`
Registar walk-in via QR (cria agendamento + inicia).

---

## 12. Root (super-admin)

> Acesso apenas ao utilizador `root`. Gestão multi-tenant.

### `GET /root`
Dashboard root com lista de todas as barbearias.

### `POST /root/criar`
Criar nova barbearia.

### `POST /root/editar/<id>`
Editar dados de barbearia.

### `POST /root/gerir/<id>`
Entrar na barbearia como chefe.

### `POST /root/toggle/<id>`
Activar/desactivar barbearia.

### `POST /root/logo/<id>`
Upload de logo (multipart/form-data).

### `POST /root/pagamento/<id>`
Registar pagamento de plano.

### `POST /root/cancelar-plano/<id>`
Cancelar plano de barbearia.

### `GET /root/planos/<id>`
Ver planos de uma barbearia.

### `GET /root/precos` | `POST /root/planos/precos` | `POST /root/barbearia/<id>/precos`
Gestão de preços de planos.

### `POST /root/alterar-senha`
Alterar senha do root.

### `POST /root/sair-barbearia`
Sair da barbearia gerida e voltar ao painel root.

---

## 13. PWA / Sistema

### `GET /healthz`
Health check (monitorização e watchdog).

**Resposta:**
```json
{ "status": "ok", "db": true, "uptime_s": 1234 }
```
Devolve sempre `200` (mesmo quando DB inacessível, `"db": false`).

### `GET /manifest.json`
Web App Manifest para instalação PWA.

### `GET /sw.js`
Service Worker (cache offline, push notifications).

### `GET /offline`
Página offline (mostrada pelo SW quando sem rede).

### `GET /conta-suspensa`
Página de conta suspensa.

---

## Códigos de Erro Comuns

| Código | Significado |
|--------|-------------|
| `302` | Redirecionamento (ex: login requerido) |
| `400` | Dados inválidos ou incompletos |
| `403` | Sem permissão para esta operação |
| `404` | Recurso não encontrado |
| `413` | Ficheiro demasiado grande (fotos) |
| `429` | Rate limit excedido (máx. ~20 req/min por IP) |
| `500` | Erro interno — reportado ao Sentry se configurado |
| `503` | Serviço indisponível (ex: push não configurado) |

---

## Notas de Segurança

- **CSRF**: Todas as rotas `POST` exigem token CSRF (Flask-WTF). As rotas JSON da API (`/api/*`) estão isentas mas exigem sessão válida.
- **Rate limiting**: `_api_ok()` bloqueia IPs que excedam ~20 req/min nas rotas públicas do cliente.
- **Multi-tenant**: Cada barbearia tem `barbearia_id` isolado. Não há cruzamento de dados entre barbearias.
- **Autenticação por OTP**: Clientes autenticam via OTP de 6 dígitos enviado por WhatsApp/SMS (sem password).
