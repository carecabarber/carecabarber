/**
 * tests/test_frontend.js — Testes unitários das funções JS puras de app.js
 *
 * Usa o módulo node:test nativo (Node 18+), sem dependências externas.
 * Correr: node tests/test_frontend.js
 * Ou via npm: node --test tests/test_frontend.js
 */

"use strict";

const { test, describe, it } = require("node:test");
const assert = require("node:assert/strict");

// ══════════════════════════════════════════════════════════════
//  Funções copiadas de static/app.js para teste isolado
//  (sem DOM, sem fetch, sem window)
// ══════════════════════════════════════════════════════════════

function pad(n) { return String(n).padStart(2, "0"); }

function formatar(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h > 0 ? `${pad(h)}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

// Versão testável do intervalo adaptativo (sem DOM)
function _intervaloEstado(horaAtual, temCronometro = false) {
    if (temCronometro) return 3000;
    return (horaAtual >= 8 && horaAtual < 20) ? 30000 : 120000;
}

function _intervaloNovos(horaAtual) {
    return (horaAtual >= 8 && horaAtual < 20) ? 60000 : 180000;
}

function _intervaloLembretes(horaAtual) {
    return (horaAtual >= 8 && horaAtual < 20) ? 120000 : 300000;
}

// Formatar telefone (replicado de app.js)
function formatarTelefone(tel) {
    if (!tel) return "";
    const digits = String(tel).replace(/\D/g, "");
    if (digits.length === 7) return `${digits.slice(0, 3)} ${digits.slice(3, 5)} ${digits.slice(5)}`;
    if (digits.length === 8) return `${digits.slice(0, 4)} ${digits.slice(4, 6)} ${digits.slice(6)}`;
    return tel;
}

// ── Lógica de lembrete urgente (do verificarLembretes)
function _nivelLembrete(minutos) {
    if (minutos <= 0) return "chegou";
    if (minutos <= 5)  return "urgente";
    return "normal";
}

// ── Lógica de cursor de novos agendamentos
function _deveNotificar(ultimoIdVisto, novosIds) {
    if (ultimoIdVisto === 0) return false;  // primeira chamada só inicializa
    return novosIds.some(id => id > ultimoIdVisto);
}

function _atualizarCursor(ultimoIdVisto, novosIds) {
    if (!novosIds.length) return ultimoIdVisto;
    return Math.max(ultimoIdVisto, ...novosIds);
}

// ══════════════════════════════════════════════════════════════
//  TESTES
// ══════════════════════════════════════════════════════════════

describe("pad()", () => {
    it("zeros à esquerda para números < 10", () => {
        assert.equal(pad(0), "00");
        assert.equal(pad(5), "05");
        assert.equal(pad(9), "09");
    });
    it("não adiciona zeros para números >= 10", () => {
        assert.equal(pad(10), "10");
        assert.equal(pad(59), "59");
        assert.equal(pad(99), "99");
    });
});

describe("formatar() — cronómetro", () => {
    it("zero segundos → 00:00", () => {
        assert.equal(formatar(0), "00:00");
    });
    it("valores negativos → 00:00 (clamp)", () => {
        assert.equal(formatar(-5), "00:00");
    });
    it("59 segundos → 00:59", () => {
        assert.equal(formatar(59), "00:59");
    });
    it("60 segundos → 01:00", () => {
        assert.equal(formatar(60), "01:00");
    });
    it("3599 segundos → 59:59", () => {
        assert.equal(formatar(3599), "59:59");
    });
    it("3600 segundos → 01:00:00 (inclui horas)", () => {
        assert.equal(formatar(3600), "01:00:00");
    });
    it("7261 segundos → 02:01:01", () => {
        assert.equal(formatar(7261), "02:01:01");
    });
    it("trunca decimais (não arredonda)", () => {
        assert.equal(formatar(61.9), "01:01");
    });
});

describe("_intervaloEstado() — polling adaptativo", () => {
    it("cronómetro activo → sempre 3s", () => {
        assert.equal(_intervaloEstado(6, true),  3000);
        assert.equal(_intervaloEstado(14, true), 3000);
        assert.equal(_intervaloEstado(23, true), 3000);
    });
    it("hora de pico (8-19) → 30s", () => {
        assert.equal(_intervaloEstado(8),  30000);
        assert.equal(_intervaloEstado(12), 30000);
        assert.equal(_intervaloEstado(19), 30000);
    });
    it("fora de horas (0-7 e 20-23) → 120s", () => {
        assert.equal(_intervaloEstado(0),  120000);
        assert.equal(_intervaloEstado(7),  120000);
        assert.equal(_intervaloEstado(20), 120000);
        assert.equal(_intervaloEstado(23), 120000);
    });
});

describe("_intervaloNovos()", () => {
    it("hora de pico → 60s", () => {
        assert.equal(_intervaloNovos(9),  60000);
        assert.equal(_intervaloNovos(18), 60000);
    });
    it("fora de horas → 180s", () => {
        assert.equal(_intervaloNovos(3),  180000);
        assert.equal(_intervaloNovos(22), 180000);
    });
});

describe("_intervaloLembretes()", () => {
    it("hora de pico → 120s", () => {
        assert.equal(_intervaloLembretes(10), 120000);
    });
    it("fora de horas → 300s", () => {
        assert.equal(_intervaloLembretes(2), 300000);
    });
});

describe("formatarTelefone()", () => {
    it("vazio → vazio", () => {
        assert.equal(formatarTelefone(""), "");
        assert.equal(formatarTelefone(null), "");
    });
    it("7 dígitos (fixo CV) → xxx xx xx", () => {
        assert.equal(formatarTelefone("2612345"), "261 23 45");
    });
    it("8 dígitos (móvel CV) → xxxx xx xx", () => {
        assert.equal(formatarTelefone("91234567"), "9123 45 67");
    });
    it("dígitos com traços ignoram formatação existente", () => {
        assert.equal(formatarTelefone("261-23-45"), "261 23 45");
    });
    it("outros comprimentos passam sem alteração", () => {
        assert.equal(formatarTelefone("123"), "123");
    });
});

describe("_nivelLembrete() — urgência", () => {
    it("0 ou negativo → 'chegou'", () => {
        assert.equal(_nivelLembrete(0),  "chegou");
        assert.equal(_nivelLembrete(-1), "chegou");
    });
    it("1-5 minutos → 'urgente'", () => {
        assert.equal(_nivelLembrete(1), "urgente");
        assert.equal(_nivelLembrete(5), "urgente");
    });
    it(">5 minutos → 'normal'", () => {
        assert.equal(_nivelLembrete(6),  "normal");
        assert.equal(_nivelLembrete(20), "normal");
    });
});

describe("_deveNotificar() / _atualizarCursor() — novos agendamentos", () => {
    it("primeira chamada (cursor=0) → não notifica", () => {
        assert.equal(_deveNotificar(0, [1, 2, 3]), false);
    });
    it("ids iguais ao cursor → não notifica", () => {
        assert.equal(_deveNotificar(5, [3, 4, 5]), false);
    });
    it("ids maiores que cursor → notifica", () => {
        assert.equal(_deveNotificar(5, [5, 6, 7]), true);
    });
    it("lista vazia → não notifica", () => {
        assert.equal(_deveNotificar(5, []), false);
    });
    it("cursor actualiza para o máximo id", () => {
        assert.equal(_atualizarCursor(5, [3, 7, 6]), 7);
    });
    it("cursor não regride", () => {
        assert.equal(_atualizarCursor(10, [3, 4]), 10);
    });
    it("lista vazia não altera cursor", () => {
        assert.equal(_atualizarCursor(5, []), 5);
    });
});
