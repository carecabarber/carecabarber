"""database.py — Shim de compatibilidade.

A implementação real vive em db/ (um módulo por domínio).
Todo o código externo continua a fazer ``import database as db`` sem alterações.

Domínios:
  db/_conn.py       — Conexão, constantes, cache, migrações, configurações
  db/barbearia.py   — Barbearias, planos, horários, ausências
  db/barbeiros.py   — Barbeiros, auth, WebAuthn, fotos
  db/servicos.py    — Serviços
  db/agendamentos.py — Agendamentos, estado, bloqueios, tokens, fila de espera
  db/disponibilidade.py — Cálculo de slots disponíveis e conflitos (read-only)
  db/relatorios.py  — Estatísticas, tendência
  db/push.py        — Web Push subscriptions
"""

# ── db/_conn.py ───────────────────────────────────────────────────────────────
from db._conn import (
    DB_PATH, FMT, _BARB_COLS, _SCHEMA_VERSION,
    ST_AGENDADO, ST_EM_ANDAMENTO, ST_CONCLUIDO,
    ST_CANCELADO, ST_NAO_COMP, ST_WALKIN,
    _HORARIO_PADRAO, _CONN_LOCK,
    _slots_cache, _slots_cache_lock, _slots_cache_get, _slots_cache_set,
    invalidar_cache_slots, invalidar_cache_slots_completo,
    _tz_local, _tz_cache, _tz_cache_lock, _TZ_CACHE_TTL,
    set_request_tz, _agora, get_barbearia_tz, set_barbearia_tz,
    _CONN, _connect, get_conn, _write, _write_exclusive, _read,
    normalizar_tel, gerar_slug, slug_unico, normalizar_dominio,
    backup_db, _run_migrations, init_db,
    get_config, set_config, get_todas_configs,
)

# ── db/barbearia.py ───────────────────────────────────────────────────────────
from db.barbearia import (
    _TIPOS_VALIDOS, PLANOS, PLANO_EXP, _PLANO_BID,
    listar_barbearias, get_barbearia, get_barbearia_por_slug,
    get_barbearia_por_dominio, set_dominio, verificar_dominio,
    criar_barbearia, set_tipo_barbearia, set_vocab_custom,
    get_planos_precos, set_plano_preco,
    get_planos_precos_barbearia, set_plano_preco_barbearia,
    _plano_info, registar_pagamento, _codigo_plano_atual,
    verificar_plano, listar_pagamentos, verificar_todos_planos,
    listar_todos_pagamentos, cancelar_plano, desativar_planos_expirados,
    toggle_barbearia, editar_barbearia, set_logo,
    get_horario, set_horario_dia, get_horario_dia,
    listar_dias_fechados, adicionar_dia_fechado, remover_dia_fechado,
    dia_esta_fechado,
    listar_ausencias, criar_ausencia, apagar_ausencia,
    ausencia_ativa, barbeiro_ausente,
    cliente_bloquear, cliente_desbloquear,
    cliente_bloqueado, clientes_bloqueados_listar,
)

# ── db/barbeiros.py ───────────────────────────────────────────────────────────
from db.barbeiros import (
    get_barbeiro_por_username, verificar_senha, username_existe,
    set_credenciais, alterar_senha,
    guardar_foto_perfil, get_foto_perfil, apagar_foto_perfil,
    registar_credencial, get_credenciais_barbeiro,
    get_credencial_por_id, atualizar_sign_count, apagar_credencial,
    listar_barbeiros, criar_barbeiro, criar_chefe,
    toggle_barbeiro, contar_agendamentos_futuros_barbeiro,
    contar_chefes_ativos, apagar_barbeiro,
    get_barbeiro, get_barbeiro_por_mesa_token, get_agendamentos_mesa,
    get_barbeiros_por_ids, editar_barbeiro, repor_senha_barbeiro,
    set_pausa_almoco,
)

# ── db/servicos.py ────────────────────────────────────────────────────────────
from db.servicos import (
    listar_servicos, servico_por_id, get_servicos_por_ids,
    criar_servico, atualizar_servico, apagar_servico, toggle_servico_ativo, mover_servico,
)

# ── db/agendamentos.py ────────────────────────────────────────────────────────
from db.agendamentos import (
    criar_agendamento, confirmar_agendamento, contar_marcacoes_cliente_dia,
    marcar_nao_compareceu,
    listar_hoje, listar_proximas_barbeiro,
    listar_todos, contar_ativos_dia, contar_todos,
    listar_datas_historico, listar_por_telefone,
    get_agendamento, barbeiro_tem_em_andamento, get_servico_em_andamento,
    barbeiro_proxima_marcacao_minutos,
    iniciar_trabalho, terminar_trabalho,
    _estado_hash, estado_hoje, estado_cliente,
    cancelar_agendamento, deletar_walkin_orfao, reagendar_agendamento,
    contar_visitas, contar_visitas_batch,
    criar_bloqueio_hora, listar_bloqueios_dia,
    agendamentos_cliente_barbeiro_dia, gerar_token_reagendar,
    get_agendamento_por_token, get_agendamento_por_token_avaliar,
    guardar_avaliacao, media_avaliacoes,
    limpar_em_andamento_presos, novos_agendamentos, proximos_agendamentos,
    espera_adicionar, espera_verificar_cliente, espera_marcar_notificado,
    espera_notificar_proximo, espera_listar_activa, espera_limpar_expiradas, espera_remover,
    marcar_lembrete_wa,
    fidelidade_reset, fidelidade_resets_count,
)

# ── db/disponibilidade.py ─────────────────────────────────────────────────────
from db.disponibilidade import (
    verificar_disponibilidade, horarios_disponiveis,
)

# ── db/relatorios.py ──────────────────────────────────────────────────────────
from db.relatorios import (
    estatisticas, estatisticas_detalhadas_barbeiro,
    tendencia_semanal, duracao_real_minutos,
    taxa_cancelamentos, top_clientes, visitas_cliente, analytics_clientes,
    resumo_mensal,
)

# ── db/push.py ────────────────────────────────────────────────────────────────
from db.push import (
    push_guardar, push_remover, push_listar, push_remover_expiradas,
    resumo_hoje,
    cliente_push_guardar, cliente_push_remover, cliente_push_listar_por_tel,
)
