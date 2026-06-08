// ── Modal de terminar ───────────────────────────────────────
let modalAtualId  = null;
let _modalAberto  = false;  // impede auto-refresh enquanto modal está aberto

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
    document.getElementById("modalOverlay").classList.add("open");
    setTimeout(() => { inp.focus(); inp.select(); }, 300);
}

function fecharModal() {
    document.getElementById("modalOverlay").classList.remove("open");
    modalAtualId = null;
    _modalAberto = false;
}

function confirmarTerminar() {
    if (!modalAtualId) return;
    const valorInput = document.getElementById("valor-input-" + modalAtualId);
    const form       = document.getElementById("form-terminar-" + modalAtualId);
    if (!valorInput || !form) { fecharModal(); return; }
    valorInput.value = document.getElementById("modalValor").value || "0";
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
        document.getElementById("fabBtn")?.classList.remove("open");
        document.getElementById("fabMenu")?.classList.remove("open");
    }
});

// ── Toast ───────────────────────────────────────────────────
let toastTimer = null;

function mostrarToast(msg, tipo) {
    let toast = document.getElementById("toast-global");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "toast-global";
        toast.className = "toast";
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    if (tipo === "aviso") {
        toast.style.background  = "var(--blue)";
        toast.style.boxShadow   = "0 4px 16px rgba(52,152,219,0.4)";
    } else if (tipo === "sucesso") {
        toast.style.background  = "var(--green)";
        toast.style.boxShadow   = "0 4px 16px rgba(46,204,113,0.4)";
    } else {
        toast.style.background  = "var(--red)";
        toast.style.boxShadow   = "0 4px 16px rgba(231,76,60,0.4)";
    }
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 5000);
}

// ── Notificação nativa ──────────────────────────────────────
function pedirPermissaoNotificacao() {
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }
}

function notificar(titulo, corpo) {
    if ("Notification" in window && Notification.permission === "granted") {
        new Notification(titulo, { body: corpo });
    }
}

// ── Cronómetros ─────────────────────────────────────────────
const atrasoNotificado = new Set();

function pad(n) { return String(n).padStart(2, "0"); }

function formatar(s) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h > 0 ? `${pad(h)}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

// ── Lembretes de marcações próximas (staff) ─────────────────
const lembretesNotificados = new Set();

function verificarLembretes() {
    fetch("/api/lembretes")
        .then(r => r.json())
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
        .catch(() => {});
}

// ── Init ────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    pedirPermissaoNotificacao();
    injetarLinhaAgora();

    // Auto-refresh quando estado dos agendamentos muda (a cada 8s)
    verificarEstadoPagina();
    setInterval(verificarEstadoPagina, 8000);

    // Cronómetros dos atendimentos em curso
    document.querySelectorAll(".cronometro").forEach(el => {
        const id       = el.dataset.id;
        const estimado = parseInt(el.dataset.estimado, 10) || 0;
        let segundos   = parseInt(el.dataset.segundos,  10) || 0;
        const alertaEl = document.getElementById(`alerta-${id}`);
        // Buscar o nome do cliente dentro do mesmo card (evita usar o primeiro da página)
        const card   = el.closest(".card-active") || el.closest(".card");
        const nomeEl = card ? card.querySelector(".cliente-nome") : null;
        const nome   = nomeEl ? nomeEl.textContent.trim() : "Cliente";

        function atualizar() {
            el.textContent = formatar(segundos);
            const emAtraso = estimado > 0 && segundos > estimado;
            el.classList.toggle("em-atraso", emAtraso);
            if (alertaEl) alertaEl.style.display = emAtraso ? "block" : "none";
            if (emAtraso && !atrasoNotificado.has(id)) {
                atrasoNotificado.add(id);
                const min = Math.round((segundos - estimado) / 60);
                mostrarToast(`⚠️ ${nome} — tempo estimado ultrapassado!`, "aviso");
                notificar("⚠️ Atendimento em atraso", `${nome} — ${min} min acima do estimado`);
            }
        }

        atualizar();
        setInterval(() => { segundos++; atualizar(); }, 1000);
    });

    // Verificar lembretes a cada 60 segundos (só para staff)
    verificarLembretes();
    setInterval(verificarLembretes, 60000);
});

// ── Auto-refresh da página quando o estado muda ─────────────
let _hashAtual = null;

function verificarEstadoPagina() {
    const path = window.location.pathname;
    // Activo na dashboard de staff (/) e na área do cliente (/cliente/<slug>/area)
    if (path !== "/" && !path.match(/^\/cliente\/[a-z0-9-]+\/area$/)) return;

    fetch("/api/estado")
        .then(r => r.json())
        .then(data => {
            if (!data.h) return;
            if (_hashAtual === null) {
                _hashAtual = data.h;   // regista estado inicial
                return;
            }
            if (data.h !== _hashAtual) {
                if (_modalAberto) {
                    _hashAtual = data.h; // atualiza hash mas não recarrega enquanto modal está aberto
                } else {
                    location.reload();
                }
            }
        })
        .catch(() => {});
}

// ── Polling de status do cliente (página cliente_home) ──────
const _statusIdsConhecidos = new Set();
let _primeiraVerificacao = true;

function iniciarPollingStatusCliente() {
    function verificar() {
        fetch("/api/meu-status")
            .then(r => r.json())
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
    setInterval(verificar, 8000);  // a cada 8s, em sincronia com o auto-refresh
}


// ── Fechar modal com ESC ─────────────────────────────────────
document.addEventListener("keydown", e => {
    if (e.key === "Escape") fecharModal();
});

// ── Swipe para baixo para fechar modal (mobile) ──────────────
(function() {
    let _ty0 = 0;
    function overlay() { return document.getElementById("modalOverlay"); }
    function box()     { return document.querySelector(".modal-box"); }

    document.addEventListener("touchstart", e => {
        if (overlay()?.classList.contains("open"))
            _ty0 = e.touches[0].clientY;
    }, { passive: true });

    document.addEventListener("touchmove", e => {
        if (!overlay()?.classList.contains("open")) return;
        const dy = e.touches[0].clientY - _ty0;
        if (dy > 0) {
            const b = box();
            if (b) { b.style.transition = "none"; b.style.transform = `translateY(${dy}px)`; }
        }
    }, { passive: true });

    document.addEventListener("touchend", e => {
        if (!overlay()?.classList.contains("open")) return;
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

    const agora   = new Date();
    const minAtual = agora.getHours() * 60 + agora.getMinutes();
    const items   = Array.from(lista.querySelectorAll(".item-agendamento"));

    let pontoInsercao = null;
    for (const item of items) {
        const txt = item.querySelector(".item-hora")?.textContent?.trim()?.match(/^\d{2}:\d{2}/);
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
