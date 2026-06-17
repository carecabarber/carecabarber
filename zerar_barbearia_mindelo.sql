-- ================================================================
-- Script para zerar os dados de uma barbearia específica
-- Uso: python3 -c "import sqlite3; conn=sqlite3.connect('barbearia.db'); conn.executescript(open('zerar_barbearia_mindelo.sql').read()); conn.commit(); conn.close()"
-- Nota: substitui BARBEARIA_ID pelo ID real da barbearia Mindelo
-- ================================================================

-- Verificar qual é o ID antes de correr:
-- SELECT id, nome FROM barbearias;

-- Alterar este valor para o ID correto da Barbearia Mindelo:
-- (corre este bloco depois de confirmar o ID)

BEGIN;

-- 1. Apagar agendamentos
DELETE FROM agendamentos WHERE barbearia_id = (SELECT id FROM barbearias WHERE nome LIKE '%indelo%' LIMIT 1);

-- 2. Apagar credenciais biométricas dos barbeiros desta barbearia
DELETE FROM webauthn_credentials WHERE barbeiro_id IN (
    SELECT id FROM barbeiros WHERE barbearia_id = (SELECT id FROM barbearias WHERE nome LIKE '%indelo%' LIMIT 1)
);

-- 3. Apagar ausências
DELETE FROM ausencias WHERE barbeiro_id IN (
    SELECT id FROM barbeiros WHERE barbearia_id = (SELECT id FROM barbearias WHERE nome LIKE '%indelo%' LIMIT 1)
);

-- 4. Apagar dias fechados
DELETE FROM dias_fechados WHERE barbearia_id = (SELECT id FROM barbearias WHERE nome LIKE '%indelo%' LIMIT 1);

-- Nota: NÃO apaga barbeiros, serviços, horários nem a própria barbearia
-- para preservar a configuração. Só limpa os agendamentos e dados operacionais.

COMMIT;

-- Verificar resultado:
-- SELECT COUNT(*) FROM agendamentos WHERE barbearia_id = (SELECT id FROM barbearias WHERE nome LIKE '%indelo%' LIMIT 1);
