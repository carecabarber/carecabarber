# Histórico de Migrações — Barbearia

Motor: `db/migrations.py`  
Tabela de controlo: `_schema_version`

> **Nota:** Este ficheiro documenta migrações geridas pelo novo motor declarativo (`db/migrations.py`).
> O sistema legado (v1–v22) está em `db/_conn.py` → `_run_migrations`, controlado pela tabela `schema_migrations`.

---

## v0 — Schema inicial (pré-motor declarativo)

Estado da base de dados antes da introdução de `db/migrations.py`.  
Gerido inteiramente por `_run_migrations` em `db/_conn.py` (versões 1–22 da tabela `schema_migrations`).

### Tabelas existentes

| Tabela                    | Descrição                                              |
|---------------------------|--------------------------------------------------------|
| `barbearias`              | Multi-tenant: cada registo é um estabelecimento        |
| `barbeiros`               | Profissionais + conta de utilizador (role: root/chefe/barbeiro) |
| `servicos`                | Catálogo de serviços por barbearia                     |
| `agendamentos`            | Marcações (agendado/em_andamento/concluido/cancelado)  |
| `horario_funcionamento`   | Horário semanal por barbearia                          |
| `dias_fechados`           | Dias de fecho especiais por barbearia                  |
| `configuracoes`           | Chave/valor de configuração por barbearia              |
| `ausencias`               | Ausências/folgas de barbeiros                          |
| `pagamentos`              | Histórico de pagamentos de planos                      |
| `planos_precos_barbearia` | Preços de planos por estabelecimento (v15)             |
| `push_subscriptions`      | Subscrições de notificações push (barbeiros)           |
| `cliente_push_subs`       | Subscrições de notificações push (clientes) (v19)      |
| `lista_espera`            | Fila de espera para vagas (v20)                        |
| `clientes_bloqueados`     | Lista negra de clientes por barbearia (v21)            |
| `webauthn_credentials`    | Credenciais WebAuthn (passkeys)                        |
| `schema_migrations`       | Controlo de migrações do sistema legado                |

---

## v1 — Índice de performance em agendamentos por criado_em

**Ficheiro:** `db/migrations.py` → `MIGRATIONS[0]`  
**SQL:** `CREATE INDEX IF NOT EXISTS idx_ag_criado_em ON agendamentos(barbearia_id, criado_em) WHERE criado_em IS NOT NULL`  
**Motivo:** Acelera queries de relatórios e listagens ordenadas por data de criação.  
**Idempotente:** sim (`IF NOT EXISTS`)

---

_Para adicionar uma migração: editar `MIGRATIONS` em `db/migrations.py` e documentar aqui._
