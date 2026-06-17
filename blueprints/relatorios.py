"""blueprints/relatorios.py — Estatísticas e relatório PDF.

Rotas:
  /estatisticas
  /estatisticas/barbeiro/<id>
  /relatorio-pdf
"""
import calendar as _cal
from datetime import datetime
from collections import defaultdict as _dd
from flask import render_template, request, redirect, url_for, session, flash, Response
import database as db
from database import ST_CONCLUIDO as _ST_CONC
from helpers import (
    _log, _agora, bid,
    staff_required, chefe_required,
    get_vocab, DIAS_PT, _MOEDA_MAP,
    _PDF_OK,
)


def register(app) -> None:

    @app.route("/estatisticas")
    @staff_required
    def estatisticas():
        barbearia_id = bid()
        if session.get("role") != "chefe":
            return redirect(url_for("estatisticas_barbeiro", id=session.get("user_id")))
        stats = db.estatisticas(barbearia_id)
        alertas_perf = []
        for s in (stats.get("top_servicos") or []):
            if s.get("media_real") and s["media_real"] > s["duracao_estimada"] + 5 and s.get("count", 0) >= 3:
                delta = round(s["media_real"] - s["duracao_estimada"], 1)
                alertas_perf.append({
                    "nome":     s["nome"],
                    "estimado": s["duracao_estimada"],
                    "real":     s["media_real"],
                    "delta":    delta,
                    "count":    s["count"],
                })
        tendencia     = db.tendencia_semanal(barbearia_id, semanas=10)
        barbeiros_pdf = db.listar_barbeiros(barbearia_id, incluir_chefe=True)
        agora_mes     = _agora(barbearia_id).strftime("%Y-%m")
        taxa_cancel   = db.taxa_cancelamentos(barbearia_id, agora_mes)
        clientes_top  = db.top_clientes(barbearia_id, limite=10)
        return render_template("estatisticas.html", stats=stats, alertas_perf=alertas_perf,
                               tendencia=tendencia, barbeiros_pdf=barbeiros_pdf,
                               agora_mes=agora_mes, taxa_cancel=taxa_cancel,
                               clientes_top=clientes_top, pdf_ok=_PDF_OK)


    @app.route("/estatisticas/barbeiro/<int:id>")
    @staff_required
    def estatisticas_barbeiro(id):
        barbearia_id = bid()
        if session.get("role") != "chefe" and session.get("user_id") != id:
            _log(f"IDOR_STATS user={session.get('user_id')} tentou ver stats de barbeiro={id}")
            return redirect(url_for("estatisticas_barbeiro", id=session.get("user_id")))
        b = db.get_barbeiro(id)
        if not b or b.get("barbearia_id") != barbearia_id:
            return redirect(url_for("estatisticas"))
        det = db.estatisticas_detalhadas_barbeiro(id, barbearia_id)
        if not det["barbeiro"]:
            return redirect(url_for("estatisticas"))
        return render_template("estatisticas_barbeiro.html", det=det, dias_pt=DIAS_PT,
                               is_chefe=session.get("role") == "chefe")


    @app.route("/relatorio-pdf")
    @chefe_required
    def relatorio_pdf():
        if not _PDF_OK:
            flash("Módulo PDF não disponível neste servidor.", "erro")
            return redirect(url_for("estatisticas"))

        barbearia_id = bid()
        agora_now    = _agora(barbearia_id)

        # ── Filtros ────────────────────────────────────────────
        data_ini_p = request.args.get("data_ini", "").strip()
        data_fim_p = request.args.get("data_fim", "").strip()
        mes_param  = request.args.get("mes", "").strip()
        filtro_bid = request.args.get("barbeiro_id", "").strip()
        filtro_bid = int(filtro_bid) if filtro_bid.isdigit() else None

        try:
            if data_ini_p and data_fim_p:
                d_inicio  = datetime.strptime(data_ini_p, "%Y-%m-%d").strftime("%Y-%m-%d 00:00:00")
                d_fim     = datetime.strptime(data_fim_p, "%Y-%m-%d").strftime("%Y-%m-%d 23:59:59")
                mes_label = f"{data_ini_p} → {data_fim_p}"
                mes_str   = data_ini_p[:7]
            elif mes_param:
                mes_dt    = datetime.strptime(mes_param, "%Y-%m")
                ultimo    = _cal.monthrange(mes_dt.year, mes_dt.month)[1]
                d_inicio  = mes_dt.strftime("%Y-%m-01 00:00:00")
                d_fim     = mes_dt.strftime(f"%Y-%m-{ultimo:02d} 23:59:59")
                mes_label = mes_dt.strftime("%B %Y").capitalize()
                mes_str   = mes_param
            else:
                mes_dt    = agora_now.replace(day=1)
                ultimo    = _cal.monthrange(mes_dt.year, mes_dt.month)[1]
                d_inicio  = mes_dt.strftime("%Y-%m-01 00:00:00")
                d_fim     = mes_dt.strftime(f"%Y-%m-{ultimo:02d} 23:59:59")
                mes_label = mes_dt.strftime("%B %Y").capitalize()
                mes_str   = mes_dt.strftime("%Y-%m")
        except ValueError:
            mes_dt    = agora_now.replace(day=1)
            ultimo    = _cal.monthrange(mes_dt.year, mes_dt.month)[1]
            d_inicio  = mes_dt.strftime("%Y-%m-01 00:00:00")
            d_fim     = mes_dt.strftime(f"%Y-%m-{ultimo:02d} 23:59:59")
            mes_label = mes_dt.strftime("%B %Y").capitalize()
            mes_str   = mes_dt.strftime("%Y-%m")

        # ── Dados ──────────────────────────────────────────────
        barbearia      = db.get_barbearia(barbearia_id)
        nome_barbearia = barbearia["nome"] if barbearia else "Barbearia"
        vocab          = get_vocab(barbearia.get("tipo") if barbearia else None,
                                   barbearia.get("vocab_custom") if barbearia else None)

        with db._read() as conn:
            _q_params = [barbearia_id, d_inicio, d_fim]
            _q_extra  = ""
            if filtro_bid:
                _q_extra = " AND a.barbeiro_id=?"
                _q_params.append(filtro_bid)

            ags = conn.execute(f"""
                SELECT a.*, b.nome AS barbeiro_nome, s.nome AS servico_nome
                FROM agendamentos a
                LEFT JOIN barbeiros b ON b.id = a.barbeiro_id
                LEFT JOIN servicos  s ON s.id = a.servico_id
                WHERE a.barbearia_id=? AND a.data_hora BETWEEN ? AND ?
                  AND a.status='{_ST_CONC}'{_q_extra}
                ORDER BY a.data_hora
            """, _q_params).fetchall()

            barbeiros = conn.execute(
                "SELECT * FROM barbeiros WHERE barbearia_id=? AND ativo=1 AND role IN ('chefe','barbeiro')",
                (barbearia_id,)).fetchall()

        por_barbeiro = _dd(lambda: {"nome": "", "count": 0, "valor": 0.0, "avaliacoes": []})
        for a in ags:
            k = a["barbeiro_id"]
            por_barbeiro[k]["nome"]   = a["barbeiro_nome"] or "—"
            por_barbeiro[k]["count"] += 1
            por_barbeiro[k]["valor"] += (a["valor"] or 0)
            if a["avaliacao"]:
                por_barbeiro[k]["avaliacoes"].append(a["avaliacao"])

        total_ags  = len(ags)
        total_val  = sum(a["valor"] or 0 for a in ags)
        clientes_u = len({a["telefone"] for a in ags if a["telefone"]})
        por_dia    = _dd(int)
        for a in ags:
            por_dia[a["data_hora"][:10]] += 1

        # ── PDF ────────────────────────────────────────────────
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                         Paragraph, Spacer, HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER
        import io as _io

        buf    = _io.BytesIO()
        styles = getSampleStyleSheet()
        doc    = SimpleDocTemplate(buf, pagesize=A4,
                                   leftMargin=2*cm, rightMargin=2*cm,
                                   topMargin=2*cm, bottomMargin=2*cm)

        cor_escura = colors.HexColor("#1a1a1a")
        cor_accent = colors.HexColor("#e8c46a")
        cor_cinza  = colors.HexColor("#666666")
        cor_linha  = colors.HexColor("#e5e7eb")

        titulo_style = ParagraphStyle("titulo", parent=styles["Heading1"],
                                      fontSize=20, textColor=cor_escura,
                                      spaceAfter=4, alignment=TA_CENTER)
        sub_style    = ParagraphStyle("sub", parent=styles["Normal"],
                                      fontSize=11, textColor=cor_cinza,
                                      spaceAfter=2, alignment=TA_CENTER)
        sec_style    = ParagraphStyle("sec", parent=styles["Heading2"],
                                      fontSize=13, textColor=cor_escura,
                                      spaceBefore=12, spaceAfter=4)
        body_style   = styles["Normal"]
        body_style.fontSize = 9

        elems = []
        _barb_nome_filtro = ""
        if filtro_bid:
            _bf = next((dict(b) for b in barbeiros if b["id"] == filtro_bid), None)
            _barb_nome_filtro = f" · {_bf['nome']}" if _bf else ""

        elems.append(Paragraph(nome_barbearia, titulo_style))
        elems.append(Paragraph(f"Relatório — {mes_label}{_barb_nome_filtro}", sub_style))
        elems.append(Paragraph(f"Gerado em {_agora(barbearia_id).strftime('%d/%m/%Y %H:%M')}", sub_style))
        elems.append(Spacer(1, 0.4*cm))
        elems.append(HRFlowable(width="100%", color=cor_accent, thickness=2))
        elems.append(Spacer(1, 0.4*cm))

        moeda = db.get_config("moeda", barbearia_id, "ECV") or "ECV"

        # Termos do vocabulário do tipo de estabelecimento
        lbl_profissional  = vocab["profissional"]    # Barbeiro / Esteticista / Técnico…
        lbl_profissionais = vocab["profissionais"]
        lbl_agendamentos  = vocab["agendamentos"]    # Marcações / Consultas…
        lbl_agendamento   = vocab["agendamento"]

        # Resumo
        elems.append(Paragraph("Resumo do Mês", sec_style))
        t_resumo = Table([
            ["Indicador", "Valor"],
            [f"Total de {lbl_agendamentos.lower()} concluídos", str(total_ags)],
            ["Receita total", f"{total_val:,.0f} {moeda}"],
            ["Clientes únicos (por telefone)", str(clientes_u)],
            [f"Média por {lbl_agendamento.lower()}", f"{(total_val/total_ags):,.0f} {moeda}" if total_ags else "—"],
        ], colWidths=[10*cm, 6*cm])
        t_resumo.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), cor_accent),
            ("TEXTCOLOR",     (0, 0), (-1, 0), cor_escura),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
            ("GRID",          (0, 0), (-1, -1), 0.5, cor_linha),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ]))
        elems.append(t_resumo)
        elems.append(Spacer(1, 0.5*cm))

        # Desempenho por profissional
        elems.append(Paragraph(f"Desempenho por {lbl_profissional}", sec_style))
        barb_data = [[lbl_profissional, lbl_agendamentos, "Receita", "Avaliação média"]]
        for k, info in sorted(por_barbeiro.items(), key=lambda x: -x[1]["valor"]):
            av = info["avaliacoes"]
            barb_data.append([
                info["nome"], str(info["count"]),
                f"{info['valor']:,.0f} {moeda}",
                f"{sum(av)/len(av):.1f} ★" if av else "—",
            ])
        if len(barb_data) == 1:
            barb_data.append(["Sem dados", "", "", ""])
        t_barb = Table(barb_data, colWidths=[7*cm, 2.5*cm, 4*cm, 3*cm])
        t_barb.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), cor_accent),
            ("TEXTCOLOR",     (0, 0), (-1, 0), cor_escura),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
            ("GRID",          (0, 0), (-1, -1), 0.5, cor_linha),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ]))
        elems.append(t_barb)
        elems.append(Spacer(1, 0.5*cm))

        # Actividade diária
        if por_dia:
            elems.append(Paragraph("Actividade Diária", sec_style))
            col_data = [["Dia", lbl_agendamentos]] + [
                [d[8:] + "/" + d[5:7], str(cnt)]
                for d, cnt in sorted(por_dia.items())
            ]
            t_dias = Table(col_data, colWidths=[3*cm, 3*cm])
            t_dias.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0), cor_accent),
                ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
                ("GRID",          (0, 0), (-1, -1), 0.3, cor_linha),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING",   (0, 0), (-1, -1), 6),
                ("ALIGN",         (1, 0), (1, -1), "CENTER"),
            ]))
            elems.append(t_dias)

        doc.build(elems)
        buf.seek(0)

        _sufx = f"_barb{filtro_bid}" if filtro_bid else ""
        nome_ficheiro = f"carecabarber_{mes_str}{_sufx}.pdf"
        resp = Response(buf.read(), mimetype="application/pdf")
        resp.headers["Content-Disposition"] = f'attachment; filename="{nome_ficheiro}"'
        resp.headers["Cache-Control"] = "no-store"
        return resp
