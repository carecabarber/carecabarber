// ── Modal de terminar ───────────────────────────────────────
let modalAtualId  = null;
let _modalAberto  = false;  // impede auto-refresh enquanto modal está aberto
let _reloadPending = false; // estado mudou enquanto modal estava aberto

function abrirModal(id, cliente, duracaoEstimada, servicoNome, precoServico) {
    modalAtualId = id;
    _modalAberto = true;
    document.getElementById("modalCliente").textContent = cliente;
    const servicoEl = document.getElementById("modalServico");
    if (servicoEl) servicoEl.textContent = servicoNome || "";
    document.getElementById("modalDuracao").textContent = "⏱ Duração estimada: " + duracaoEstimada + " min";
    // Pré-preencher valor com o preço do serviço (editável pelo barbeiro)
    const inp = document.getElementById("modalValor");
    inp.value = (precoServico && precoServico > 0) ? precoServico : "";
    // Mostrar preço base do serviço
    const _refBox = document.getElementById("modalPrecoRef");
    const _refVal = document.getElementById("modalPrecoVal");
    if (_refBox && _refVal) {
        if (precoServico > 0) {
            _refVal.textContent = precoServico.toLocaleString("pt-PT") + " " + (window._MOEDA_SIM || "");
            _refBox.style.display = "flex";
        } else {
            _refBox.style.display = "none";
        }
    }
    // Limpar avaliação anterior
    setStar(0);
    document.getElementById("modalOverlay").classList.add("open");
    setTimeout(() => { inp.focus(); inp.select(); }, 300);
}

// Wrapper seguro para botões com data-* (evita XSS em parâmetros onclick)
function abrirModalBtn(btn) {
    const id      = parseInt(btn.dataset.id, 10);
    if (!id || isNaN(id)) return;   // guard: dataset.id ausente ou inválido
    const cliente = btn.dataset.cliente || "";
    const duracao = parseInt(btn.dataset.duracao, 10) || 0;
    const servico = btn.dataset.servico || "";
    const preco   = parseFloat(btn.dataset.preco) || 0;
    abrirModal(id, cliente, duracao, servico, preco);
}

function fecharModal() {
    document.getElementById("modalOverlay").classList.remove("open");
    modalAtualId = null;
    _modalAberto = false;
    // Re-ativar botão confirmar para a próxima abertura (evita ficar disabled após cancelar)
    const btnConf = document.querySelector(".btn-terminar-modal");
    if (btnConf) { btnConf.disabled = false; btnConf.textContent = "✓ Confirmar"; }
    if (_reloadPending) { _reloadPending = false; location.reload(); }
}

function setStar(n) {
    const av = document.getElementById("modalAvaliacao");
    if (av) av.value = n > 0 ? n : "";
    document.querySelectorAll("#modalStars .star").forEach(s => {
        s.classList.toggle("active", parseInt(s.dataset.v) <= n);
    });
}

function confirmarTerminar() {
    if (!modalAtualId) return;
    const valorInput = document.getElementById("valor-input-" + modalAtualId);
    const form       = document.getElementById("form-terminar-" + modalAtualId);
    if (!valorInput || !form) { fecharModal(); return; }
    // Anti-double-click: disable button immediately
    const btnConf = document.querySelector(".btn-terminar-modal");
    if (btnConf) {
        if (btnConf.disabled) return;          // already submitted
        btnConf.disabled = true;
        btnConf.textContent = "A enviar…";
    }
    valorInput.value = document.getElementById("modalValor").value || "0";
    // Incluir avaliação no form se preenchida e válida (1-5)
    const _avEl = document.getElementById("modalAvaliacao");
    const avVal = _avEl ? _avEl.value : "";
    const avSafe = avVal && /^[1-5]$/.test(avVal) ? avVal : "";
    let avInp = form.querySelector('[name="avaliacao"]');
    if (!avInp) {
        avInp = document.createElement("input");
        avInp.type = "hidden"; avInp.name = "avaliacao";
        form.appendChild(avInp);
    }
    avInp.value = avSafe;
    form.submit();
}

// ── FAB toggle ──────────────────────────────────────────────
function toggleFab() {
    const btn  = document.getElementById("fabBtn");
    const menu = document.getElementById("fabMenu");
    if (!btn || !menu) return;
    btn.classList.toggle("open");
    menu.classList.toggle("open");
}

document.addEventListener("click", e => {
    const group = document.querySelector(".fab-group");
    if (group && !group.contains(e.target)) {
        var _fb = document.getElementById("fabBtn"); if (_fb) _fb.classList.remove("open");
        var _fm = document.getElementById("fabMenu"); if (_fm) _fm.classList.remove("open");
    }
});

// ── Toast queue ─────────────────────────────────────────────
// Fila FIFO: mostra um toast de cada vez; quando acaba avança para o próximo.
// Resolve o problema de múltiplos toasts simultâneos (ex: vários novos agendamentos)
// que antes se sobrepunham e só o último era visível.
const _toastFila = [];
let   _toastActivo = false;

function mostrarToast(msg, tipo) {
    _toastFila.push({ msg, tipo });
    if (!_toastActivo) _toastProximo();
}

function _toastProximo() {
    if (!_toastFila.length) { _toastActivo = false; return; }
    _toastActivo = true;
    const { msg, tipo } = _toastFila.shift();

    let toast = document.getElementById("toast-global");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "toast-global";
        toast.className = "toast";
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    if (tipo === "aviso") {
        toast.style.background = "var(--blue)";
        toast.style.boxShadow  = "0 4px 16px rgba(52,152,219,0.4)";
    } else if (tipo === "sucesso") {
        toast.style.background = "var(--green)";
        toast.style.boxShadow  = "0 4px 16px rgba(46,204,113,0.4)";
    } else {
        toast.style.background = "var(--red)";
        toast.style.boxShadow  = "0 4px 16px rgba(231,76,60,0.4)";
    }
    toast.classList.add("show");
    // Após 4.5s esconde; após +0.5s (fade) avança na fila
    setTimeout(() => {
        toast.classList.remove("show");
        setTimeout(_toastProximo, 500);
    }, 4500);
}

// ── Notificação nativa ──────────────────────────────────────
function pedirPermissaoNotificacao() {
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }
}

function notificar(titulo, corpo) {
    // Só dispara notificação nativa quando o tab está em segundo plano
    // (se o utilizador já está a ver a página, o toast é suficiente)
    if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
        new Notification(titulo, { body: corpo });
    }
}

// ── Hora do servidor (scope global) ─────────────────────────
// Inicializada com fallback seguro; substituída em DOMContentLoaded quando
// _SERVER_NOW (injectado pelo template) já está disponível.
// Todos os módulos que precisam da hora de Cabo Verde usam esta função.
let _horaServidor = () => new Date();

// Listener único de visibilitychange — chamado por todos os cronómetros
const _cronAtualizar = [];
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) _cronAtualizar.forEach(fn => fn());
});

// ── Cronómetros ─────────────────────────────────────────────

// Persistir IDs já notificados (atraso) entre reloads da página — evita toast repetido
const atrasoNotificado = (() => {
    try {
        const s = JSON.parse(sessionStorage.getItem("_atrasos_notif") || "[]");
        return new Set(Array.isArray(s) ? s : []);
    } catch(e) { return new Set(); }
})();
function _persistirAtrasos() {
    try { sessionStorage.setItem("_atrasos_notif", JSON.stringify([...atrasoNotificado])); } catch(e) {}
}

function pad(n) { return String(n).padStart(2, "0"); }

function formatar(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h > 0 ? `${pad(h)}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

/// ── Helper: fetch com timeout automático ────────────────────
// Aceita _fetch(url) ou _fetch(url, opts) onde opts pode incluir { signal, ms }
// O timeout (padrão 8s) é SEMPRE aplicado, mesmo quando se passa um signal externo.
function _fetch(url, opts) {
    const ms  = (opts && typeof opts === "object" ? opts.ms : opts) || 8000;
    const ext = (opts && typeof opts === "object") ? opts : {};
    try {
        // Construir lista de signals: o externo (AbortController) + timeout automático
        const signals = [];
        if (ext.signal) signals.push(ext.signal);
        if (typeof AbortSignal !== "undefined" && AbortSignal.timeout) {
            signals.push(AbortSignal.timeout(ms));
        }
        let sig;
        if (signals.length >= 2 && typeof AbortSignal.any === "function") {
            sig = AbortSignal.any(signals);   // dispara quando qualquer um abortar
        } else if (signals.length === 1) {
            sig = signals[0];
        }
        const fetchOpts = { ...ext };
        if (sig) fetchOpts.signal = sig;
        return fetch(url, fetchOpts);
    } catch(e) { return fetch(url, ext); }
}

// ── Notificações de novos agendamentos ─────────────────────
let _ultimoIdVisto = 0;   // começa a 0; na 1ª chamada apenas inicializa o cursor

async function verificarNovosAgendamentos() {
    try {
        const r = await _fetch(`/api/novos-agendamentos?desde_id=${_ultimoIdVisto}`);
        if (!r.ok) return;
        const novos = await r.json();
        if (!novos.length) return;

        // Atualizar cursor
        const maxId = Math.max(...novos.map(a => a.id));

        if (_ultimoIdVisto === 0) {
            // Primeira chamada — só inicializa o cursor, não notifica
            _ultimoIdVisto = maxId;
            return;
        }

        _ultimoIdVisto = maxId;

        // Notificar cada novo agendamento
        novos.forEach(a => {
            const icone  = a.tipo === 'walk-in' ? '⚡' : '📅';
            const barbMsg = a.barbeiro !== '—' ? ` · ${a.barbeiro}` : '';
            const msg    = `${a.cliente} — ${a.servico}${barbMsg} às ${a.hora}`;
            mostrarToast(`${icone} Nova marcação: ${msg}`, 'sucesso');
            notificar(`${icone} Nova marcação!`, msg);
        });
    } catch(e) {
        if (e && e.name !== "AbortError") console.warn("[novos-ag]", e);
    }
}

// ── Lembretes de marcações próximas (staff) ─────────────────
const lembretesNotificados = new Set();

function verificarLembretes() {
    _fetch("/api/lembretes")
        .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(lista => {
            // Actualizar dot de notificação na nav
            const novos = lista.filter(a => !lembretesNotificados.has(a.id));
            atualizarBadgeLembrete(novos.length);

            // Limpar Set se crescer demasiado (evitar memory leak em sessões longas)
            if (lembretesNotificados.size > 300) {
                const arr = [...lembretesNotificados];
                lembretesNotificados.clear();
                arr.slice(-150).forEach(id => lembretesNotificados.add(id));
            }

            lista.forEach(a => {
                if (lembretesNotificados.has(a.id)) return;
                lembretesNotificados.add(a.id);

                const min = a.minutos_ate;
                const tel = a.telefone;
                const msg = `${a.cliente} — ${a.servico} às ${a.hora}`;

                if (min <= 0) {
                    mostrarToast(`⏰ Hora de ${a.cliente}! ${tel ? "📞 " + tel : ""}`, "aviso");
                    notificar("⏰ Cliente a chegar!", msg + (tel ? "\n📞 " + tel : ""));
                } else {
                    mostrarToast(`⏰ ${a.cliente} em ${min} min${tel ? " · 📞 " + tel : ""}`, "aviso");
                    notificar(`⏰ Marcação em ${min} min`, msg + (tel ? "\n📞 " + tel : ""));
                }
            });
        })
        .catch(e => { if (e && e.name !== "AbortError") console.warn("[lembretes]", e); });
}

// ── Intervalo adaptativo: rápido em horário de trabalho, lento fora ──────────
// Usa _horaServidor() (hora de Cabo Verde) — não o relógio local do browser
function _intervaloEstado() {
    if (document.querySelector(".cronometro")) return 3000;  // serviço em curso → sync rápido
    const h = _horaServidor().getHours();
    return (h >= 8 && h < 20) ? 30000 : 120000;   // 30s em horas de pico (era 12s)
}
function _intervaloNovos() {
    const h = _horaServidor().getHours();
    return (h >= 8 && h < 20) ? 60000 : 180000;   // 60s em horas de pico (era 30s)
}
function _intervaloLembretes() {
    const h = _horaServidor().getHours();
    return (h >= 8 && h < 20) ? 120000 : 300000;  // 2min em horas de pico (era 90s)
}

// Agenda próxima chamada com intervalo recalculado (adapta se hora muda)
// Os IDs são guardados para cancelar no pagehide e não acumular no BFCache.
let _timerId_estado   = null;
let _timerId_novos    = null;
let _timerId_lembretes = null;
let _polling_activo   = true;   // false após pagehide — impede novas iterações

function _agendarEstado() {
    if (!_polling_activo) return;
    _timerId_estado = setTimeout(() => { if (_polling_activo) { verificarEstadoPagina(); _agendarEstado(); } }, _intervaloEstado());
}
function _agendarNovos() {
    if (!_polling_activo) return;
    _timerId_novos = setTimeout(() => { if (_polling_activo) { verificarNovosAgendamentos(); _agendarNovos(); } }, _intervaloNovos());
}
function _agendarLembretes() {
    if (!_polling_activo) return;
    _timerId_lembretes = setTimeout(() => { if (_polling_activo) { verificarLembretes(); _agendarLembretes(); } }, _intervaloLembretes());
}

// ── Init ────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    pedirPermissaoNotificacao();

    // ── Calibrar _horaServidor PRIMEIRO (antes de qualquer uso) ──
    // _SERVER_NOW está disponível em todas as páginas via base.html (context processor)
    if (typeof _SERVER_NOW !== "undefined") {
        const _serverNowMs    = new Date(_SERVER_NOW).getTime();
        const _clientOffsetMs = Date.now() - _serverNowMs;
        _horaServidor = () => new Date(Date.now() - _clientOffsetMs);
    }

    injetarLinhaAgora();   // usa _horaServidor já calibrado

    // Auto-refresh quando estado dos agendamentos muda (intervalo adaptativo)
    verificarEstadoPagina();
    _agendarEstado();

    // ── Relógio live no cabeçalho ──────────────────────────────
    const _relogioEl = document.getElementById("relogio-server");

    function _atualizarRelogio() {
        if (!_relogioEl) return;
        const d = _horaServidor();
        _relogioEl.textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    }
    let _idRelogio = null;
    if (_relogioEl) {
        _atualizarRelogio();
        _idRelogio = setInterval(_atualizarRelogio, 10000);  // atualiza a cada 10s (suficiente)
    }

    // ── Linha "agora" atualiza a cada minuto ──────────────────
    const _idLinhaAgora = setInterval(() => {
        const linha = document.querySelector(".agenda-agora-line");
        if (linha) linha.remove();
        injetarLinhaAgora();
    }, 60000);

    // ── Limpar intervalos e timeouts ao sair da página (evita memory leak no BFCache) ──────
    const _idsParaLimpar = [_idLinhaAgora, _idRelogio].filter(Boolean);
    window.addEventListener("pagehide", () => {
        _polling_activo = false;
        _idsParaLimpar.forEach(id => clearInterval(id));
        clearTimeout(_timerId_estado);
        clearTimeout(_timerId_novos);
        clearTimeout(_timerId_lembretes);
        clearTimeout(toastTimer);
        _cronAtualizar.length = 0;   // limpa callbacks de cronómetros (evita acumulação no BFCache)
    }, { once: true });

    // ── Cronómetros sem drift ─────────────────────────────────
    document.querySelectorAll(".cronometro").forEach(el => {
        const id          = el.dataset.id;
        const estimado    = parseInt(el.dataset.estimado, 10) || 0;
        const segundosBase = parseInt(el.dataset.segundos, 10) || 0;
        const initMs      = Date.now();   // âncora — evita drift do setInterval
        const alertaEl    = document.getElementById(`alerta-${id}`);
        const card        = el.closest(".card-active") || el.closest(".card");
        const nomeEl      = card ? card.querySelector(".cliente-nome") : null;
        const nome        = nomeEl ? nomeEl.textContent.trim() : "Cliente";

        function segundosDecorridos() {
            return segundosBase + Math.floor((Date.now() - initMs) / 1000);
        }

        const barEl = document.getElementById("bar-" + id);

        function atualizar() {
            const s = segundosDecorridos();
            el.textContent = formatar(s);
            const emAtraso = estimado > 0 && s > estimado;
            el.classList.toggle("em-atraso", emAtraso);
            if (alertaEl) alertaEl.style.display = emAtraso ? "block" : "none";
            if (barEl && estimado > 0) {
                const pct = Math.min(100, Math.round(s / estimado * 100));
                barEl.style.width = pct + "%";
                barEl.style.background = emAtraso ? "var(--red, #ef4444)" : "var(--accent)";
            }
            if (emAtraso && !atrasoNotificado.has(id)) {
                atrasoNotificado.add(id);
                _persistirAtrasos();
                const min = Math.round((s - estimado) / 60);
                mostrarToast(`⚠️ ${nome} — tempo estimado ultrapassado!`, "aviso");
                notificar("⚠️ Atendimento em atraso", `${nome} — ${min} min acima do estimado`);
            }
        }

        atualizar();
        // 500ms — setInterval não é exacto a 1000ms, evita saltar visualmente um segundo
        const _idCron = setInterval(atualizar, 500);
        _idsParaLimpar.push(_idCron);
        // Registar no handler global de visibilitychange (evita N listeners para N cronómetros)
        _cronAtualizar.push(atualizar);
    });

    // Lembretes e novos agendamentos — apenas no dashboard (index)
    // Noutras páginas (histórico, perfil, configurações) o polling é desnecessário
    const _isDashboard = location.pathname === "/" || location.pathname.endsWith("/dashboard");
    if (_isDashboard) {
        setTimeout(() => { verificarLembretes(); _agendarLembretes(); }, 4000);
        setTimeout(() => { verificarNovosAgendamentos(); _agendarNovos(); }, 8000);
    }
});

// ── Auto-refresh da página quando o estado muda ─────────────
let _hashAtual    = null;
let _estadoAbort  = null;   // AbortController activo — cancela pedido anterior se sobreposição

function verificarEstadoPagina() {
    const path = window.location.pathname;
    // Activo na dashboard de staff (/) e na área do cliente (/cliente/<slug>/area)
    if (path !== "/" && !path.match(/^\/cliente\/[a-z0-9-]+\/area$/)) return;

    // Cancelar pedido anterior ainda pendente (evita race condition se o timer disparou antes)
    if (_estadoAbort) { try { _estadoAbort.abort(); } catch (_) {} }
    _estadoAbort = new AbortController();
    const signal = _estadoAbort.signal;

    _fetch("/api/estado", { signal })
        .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(data => {
            _estadoAbort = null;
            if (!data.h) return;
            if (_hashAtual === null) {
                _hashAtual = data.h;   // regista estado inicial
                return;
            }
            if (data.h !== _hashAtual) {
                if (_modalAberto) {
                    _reloadPending = true; // modal aberto — recarrega quando fechar
                } else {
                    location.reload();
                }
            }
        })
        .catch(err => { if (err && err.name !== "AbortError") {} });
}

// ── Polling de status do cliente (página cliente_home) ──────
const _statusIdsConhecidos = new Set();
let _primeiraVerificacao = true;
let _pollingStatusActive  = false;   // impede múltiplas instâncias simultâneas

function iniciarPollingStatusCliente() {
    if (_pollingStatusActive) return;   // já está a correr — não duplicar
    _pollingStatusActive = true;
    function verificar() {
        _fetch("/api/meu-status")
            .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(lista => {
                if (_primeiraVerificacao) {
                    lista.forEach(a => _statusIdsConhecidos.add(a.id));
                    _primeiraVerificacao = false;
                    return;
                }
                lista.forEach(a => {
                    if (!_statusIdsConhecidos.has(a.id)) {
                        _statusIdsConhecidos.add(a.id);
                        notificar("✂️ O teu corte começou!", `${a.servico} com ${a.barbeiro}. Podes entrar!`);
                        mostrarToast("✂️ O teu atendimento começou!", "aviso");
                    }
                });
            })
            .catch(() => {});
    }
    verificar();
    const _pollingId = setInterval(verificar, 30000);  // a cada 30s (era 8s — economiza CPU)
    // Limpar ao sair da página para não acumular em SPA ou BFCache
    window.addEventListener("pagehide", () => {
        clearInterval(_pollingId);
        _pollingStatusActive = false;   // resetar guard para permitir reinício após BFCache restore
    }, { once: true });
    // BFCache restore (iOS Safari) — reiniciar polling se a página for restaurada do cache
    window.addEventListener("pageshow", (e) => {
        if (e.persisted) iniciarPollingStatusCliente();
    });
}


// ── Fechar modal com ESC ─────────────────────────────────────
document.addEventListener("keydown", e => {
    if (e.key === "Escape") fecharModal();
});

// ── Fechar modal ao clicar fora da box (backdrop) ────────────
document.addEventListener("click", e => {
    const overlay = document.getElementById("modalOverlay");
    if (overlay && overlay.classList.contains("open")) {
        // Só fechar se o clique foi directamente no overlay (backdrop), não na box
        if (e.target === overlay) fecharModal();
    }
});

// ── Segurança: resetar overlay se BFCache restaurar a página ─
window.addEventListener("pageshow", e => {
    if (e.persisted) {
        // Página restaurada do BFCache — garantir que o overlay não fica preso
        const overlay = document.getElementById("modalOverlay");
        if (overlay) overlay.classList.remove("open");
        _modalAberto = false;
        _reloadPending = false;
    }
});

// ── Swipe para baixo para fechar modal (mobile) ──────────────
(function() {
    let _ty0 = 0;
    function overlay() { return document.getElementById("modalOverlay"); }
    function box()     { return document.querySelector(".modal-box"); }

    document.addEventListener("touchstart", e => {
        var _ov = overlay(); if (_ov && _ov.classList.contains("open"))
            _ty0 = e.touches[0].clientY;
    }, { passive: true });

    document.addEventListener("touchmove", e => {
        var _ov = overlay(); if (!_ov || !_ov.classList.contains("open")) return;
        const dy = e.touches[0].clientY - _ty0;
        if (dy > 0) {
            const b = box();
            if (b) { b.style.transition = "none"; b.style.transform = `translateY(${dy}px)`; }
        }
    }, { passive: true });

    document.addEventListener("touchend", e => {
        var _ov = overlay(); if (!_ov || !_ov.classList.contains("open")) return;
        const dy = e.changedTouches[0].clientY - _ty0;
        const b = box();
        if (dy > 90) {
            if (b) { b.style.transition = ""; b.style.transform = ""; }
            fecharModal();
        } else if (b) {
            b.style.transition = "transform 0.25s";
            b.style.transform  = "";
            setTimeout(() => { if (b) b.style.transition = ""; }, 260);
        }
    }, { passive: true });
})();

// ── Linha "Agora" na agenda do dia ───────────────────────────
function injetarLinhaAgora() {
    if (window.location.pathname !== "/") return;
    const lista = document.querySelector(".lista-agendamentos");
    if (!lista) return;

    // Hora actual corrigida pelo offset servidor-cliente (actualiza a cada chamada)
    const agora    = typeof _horaServidor === "function" ? _horaServidor()
                   : (typeof _SERVER_NOW !== "undefined" ? new Date(_SERVER_NOW) : new Date());
    const minAtual = agora.getHours() * 60 + agora.getMinutes();
    const items   = Array.from(lista.querySelectorAll(".item-agendamento"));

    let pontoInsercao = null;
    for (const item of items) {
        var _ih = item.querySelector(".item-hora");
        const txt = _ih ? (_ih.textContent || "").trim().match(/^\d{2}:\d{2}/) : null;
        if (!txt) continue;
        const [h, m] = txt[0].split(":").map(Number);
        if (h * 60 + m > minAtual) { pontoInsercao = item; break; }
    }

    if (!pontoInsercao && items.length === 0) return; // agenda vazia

    const linha = document.createElement("li");
    linha.className = "agenda-agora-line";
    linha.innerHTML = `<span class="agenda-agora-pill">▶ agora</span><div class="agenda-agora-bar"></div>`;

    if (pontoInsercao) {
        lista.insertBefore(linha, pontoInsercao);
    } else {
        lista.appendChild(linha); // todos no passado — linha no fim
    }
}

// ── Dot de lembrete na nav ───────────────────────────────────
function atualizarBadgeLembrete(count) {
    document.querySelectorAll(".bnav-dot").forEach(d => d.remove());
    if (count <= 0) return;
    const navHoje = document.querySelector(".bnav-item:first-child");
    if (navHoje) {
        const dot = document.createElement("span");
        dot.className = "bnav-dot";
        dot.title = `${count} marcação(ões) próxima(s)`;
        navHoje.appendChild(dot);
    }
}

// ── Loading state para pickers de slots ─────────────────────
function setSlotLoading(listEl, wrapEl) {
    wrapEl.style.display = "block";
    listEl.innerHTML = `<div class="slots-loading"><div class="spinner"></div> A verificar horários…</div>`;
}

// ── Toggle tema claro/escuro ─────────────────────────────────
(function() {
    var html = document.documentElement;

    // Aplicar tema guardado (executado imediatamente, antes do paint)
    if (localStorage.getItem('cb-theme') === 'light') {
        html.setAttribute('data-theme', 'light');
    }

    function _aplicarTema() {
        var isLight = html.getAttribute('data-theme') === 'light';
        var label   = document.getElementById('tema-label');
        var btn     = document.getElementById('btn-tema');
        if (label) label.textContent = isLight ? '☀ Claro' : '🌙 Escuro';
        if (btn) {
            btn.style.background = isLight ? '#f0b429' : '';
            btn.style.color      = isLight ? '#000'    : '';
        }
    }

    document.addEventListener('DOMContentLoaded', function() {
        _aplicarTema();
        var btn = document.getElementById('btn-tema');
        if (btn) {
            btn.addEventListener('click', function() {
                var isLight = html.getAttribute('data-theme') === 'light';
                if (isLight) {
                    html.removeAttribute('data-theme');
                    localStorage.setItem('cb-theme', 'dark');
                } else {
                    html.setAttribute('data-theme', 'light');
                    localStorage.setItem('cb-theme', 'light');
                }
                _aplicarTema();
            });
        }
    });
})();

// ── Loading state global — form submits ───────────────────────────────────────
// Quando um form é submetido, o botão de submit fica desactivado e ganha um
// spinner inline. Excepções: forms hidden (modais, form-terminar-*) e forms
// cujo botão já tem a classe btn-loading (submissões em curso / modais próprios).
// Guard de 10 s: re-activa o botão caso a página não recarregue (erro de rede).
(function () {
    document.addEventListener('submit', function (e) {
        var form = e.target;
        // Ignorar forms hidden — forma-terminar-* e outros geridos por JS
        if (form.style.display === 'none' || form.hidden) return;
        // Encontrar botão submit (não optado-fora com data-no-load)
        var btn = form.querySelector('button[type="submit"]:not([data-no-load])');
        if (!btn || btn.disabled) return;
        btn.classList.add('btn-loading');
        btn.disabled = true;
        // Re-activar após 10 s (guard contra erros de rede sem redirect)
        setTimeout(function () {
            btn.classList.remove('btn-loading');
            btn.disabled = false;
        }, 10000);
    });
})();
