import io
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Indicador Operadores Logísticos", page_icon="🚚", layout="wide")

CORES = {"azul": "#0067B1", "azul2": "#1593FF", "verde": "#16A34A", "vermelho": "#DC2626", "amarelo": "#F59E0B"}

# De/Para disponibilizado para o indicador.
DE_PARA_OPERADOR = {
    "CAMPINAS": "TPC",
    "PALHOCA": "TPC",
    "SALVADOR": "TPC",
    "BARUERI": "POSTALGOW",
    "MANAUS": "POSTALGOW",
    "BRASILIA": "CELISTICS",
    "CONTAGEM": "CELISTICS",
    "JABOATAO": "CELISTICS",
    "RIO DE JANEIRO": "CELISTICS",
}

st.markdown("""
<style>
.block-container {padding-top: 1.1rem; padding-bottom: 2rem;}
[data-testid="stMetric"] {background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px;box-shadow:0 2px 8px rgba(15,23,42,.05)}
.alerta {padding:12px;border-radius:10px;background:#fff7ed;border-left:5px solid #f59e0b;margin-bottom:8px}
.ok {padding:12px;border-radius:10px;background:#f0fdf4;border-left:5px solid #16a34a;margin-bottom:8px}
</style>
""", unsafe_allow_html=True)


def sem_acento(valor):
    return unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")


def slug(valor):
    return re.sub(r"[^a-z0-9]+", "_", sem_acento(valor).lower()).strip("_")


def normalizar_cd(valor):
    if pd.isna(valor):
        return ""
    return re.sub(r"\s+", " ", sem_acento(valor).strip().upper())


def deduplicar_colunas(colunas):
    contagem, saida = {}, []
    for coluna in colunas:
        base = str(coluna).strip()
        n = contagem.get(base, 0)
        saida.append(base if n == 0 else f"{base}__{n + 1}")
        contagem[base] = n + 1
    return saida


def escolher_coluna(df, candidatos, preferir_ultima=False):
    encontradas = []
    for candidato in candidatos:
        alvo = slug(candidato)
        encontradas.extend([c for c in df.columns if slug(c) == alvo or slug(c).startswith(alvo + "_")])
    encontradas = list(dict.fromkeys(encontradas))
    if not encontradas:
        return None
    return encontradas[-1] if preferir_ultima else encontradas[0]


@st.cache_data(show_spinner="Carregando e preparando a base logística...", max_entries=2)
def carregar_dados(origem, assinatura):
    if isinstance(origem, bytes):
        df = pd.read_excel(io.BytesIO(origem), sheet_name=0, engine="openpyxl")
    else:
        caminho = Path(origem)
        if caminho.suffix.lower() == ".parquet":
            df = pd.read_parquet(caminho)
        elif caminho.suffix.lower() in (".xlsx", ".xlsm"):
            df = pd.read_excel(caminho, sheet_name=0, engine="openpyxl")
        elif caminho.suffix.lower() == ".xlsb":
            df = pd.read_excel(caminho, sheet_name=0, engine="pyxlsb")
        else:
            df = pd.read_csv(caminho, sep=None, engine="python", low_memory=False)

    df.columns = deduplicar_colunas(df.columns)
    df = df.dropna(how="all").copy()

    for c in [c for c in df.columns if slug(c).startswith("data_") or "mes_referencia" in slug(c)]:
        df[c] = pd.to_datetime(df[c], errors="coerce")

    c_cd = escolher_coluna(df, ["CD Origem"])
    c_pedido = escolher_coluna(df, ["Pedido"])
    c_real_total = escolher_coluna(df, ["Dias Realizado Total"], preferir_ultima=True)
    c_entrega = escolher_coluna(df, ["Data Entrega"])
    c_expedicao = escolher_coluna(df, ["Data Expedicao", "Data Expedição"])
    c_referencia = escolher_coluna(df, ["Mes Referencia", "Data Expedicao", "Data Entrega", "Data Pedido"])

    if not c_cd:
        raise ValueError("A coluna 'CD Origem' não foi encontrada na base.")

    # Nova dimensão do indicador, sem alterar fisicamente a planilha original.
    df["Operador Logístico"] = df[c_cd].map(lambda x: DE_PARA_OPERADOR.get(normalizar_cd(x), "NÃO MAPEADO"))

    if c_real_total:
        df["Lead Time"] = pd.to_numeric(df[c_real_total], errors="coerce")
    elif c_entrega and c_expedicao:
        df["Lead Time"] = (df[c_entrega] - df[c_expedicao]).dt.days
    else:
        df["Lead Time"] = np.nan

    df["Lead Time"] = df["Lead Time"].where(df["Lead Time"] >= 0)
    df["Status D+2"] = np.select([df["Lead Time"].le(2), df["Lead Time"].gt(2)], ["No Prazo", "Fora do Prazo"], default="Pendente")
    df["Status D+4"] = np.select([df["Lead Time"].le(4), df["Lead Time"].gt(4)], ["No Prazo", "Fora do Prazo"], default="Pendente")
    df["Impactado D+2"] = np.where(df["Lead Time"].between(3, 4, inclusive="both"), "Migra para Fora do Prazo", "Sem mudança")
    df["Faixa Lead Time"] = pd.cut(df["Lead Time"], [-0.1, 0, 1, 2, 3, 4, 7, np.inf], labels=["D+0", "D+1", "D+2", "D+3", "D+4", "D+5 a D+7", "> D+7"])

    if c_referencia:
        df["Data Referência"] = pd.to_datetime(df[c_referencia], errors="coerce")
        df["Mês Início"] = df["Data Referência"].dt.to_period("M").dt.to_timestamp()
        df["Mês/Ano"] = df["Mês Início"].dt.strftime("%m/%Y")
    else:
        df["Data Referência"] = pd.NaT
        df["Mês Início"] = pd.NaT
        df["Mês/Ano"] = "Sem data"

    df["Chave Volume"] = df[c_pedido].astype(str) if c_pedido else df.index.astype(str)
    return df


def fmt_pct(v):
    return "-" if pd.isna(v) else f"{v:.1f}%".replace(".", ",")


def fmt_num(v):
    return f"{int(v):,}".replace(",", ".")


def taxa(base, limite):
    s = base["Lead Time"].dropna()
    return s.le(limite).mean() * 100 if not s.empty else np.nan


def resumo_dimensao(base, dimensao):
    b = base[base[dimensao].notna() & base["Lead Time"].notna()].copy()
    if b.empty:
        return pd.DataFrame()
    r = b.groupby(dimensao, dropna=False).agg(
        Pedidos=("Chave Volume", "nunique"),
        Registros=("Chave Volume", "size"),
        Lead_Time_Médio=("Lead Time", "mean"),
        Mediana=("Lead Time", "median"),
        P90=("Lead Time", lambda s: s.quantile(.90)),
        Dentro_D2=("Lead Time", lambda s: s.le(2).sum()),
        Dentro_D4=("Lead Time", lambda s: s.le(4).sum()),
        Impactados=("Lead Time", lambda s: s.between(3, 4, inclusive="both").sum()),
    ).reset_index()
    r["SLA D+2"] = r["Dentro_D2"] / r["Registros"] * 100
    r["SLA D+4"] = r["Dentro_D4"] / r["Registros"] * 100
    r["Gap (p.p.)"] = r["SLA D+4"] - r["SLA D+2"]
    return r


def estilizar(fig, altura=420):
    fig.update_layout(height=altura, margin=dict(l=10, r=10, t=55, b=10), font=dict(family="Arial", size=13), legend_title_text="")
    fig.update_xaxes(title_text="", showgrid=False)
    fig.update_yaxes(title_text="", gridcolor="#E2E8F0")
    return fig


def baixar_csv(base, nome, rotulo):
    st.download_button(rotulo, base.to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig"), nome, "text/csv")


arquivos = ["Base Otif 2026.parquet", "Base Otif 2026.xlsx", "Base OTIF 2026.xlsx"]
padrao = next((a for a in arquivos if os.path.exists(a)), None)

with st.sidebar:
    st.header("🚚 Filtros")
    upload = st.file_uploader("Base OTIF", type=["xlsx", "xlsm", "xlsb", "csv", "parquet"])

try:
    if upload:
        conteudo = upload.getvalue()
        df = carregar_dados(conteudo, (upload.name, len(conteudo)))
    elif padrao:
        df = carregar_dados(padrao, os.path.getmtime(padrao))
    else:
        st.error("Base não encontrada. Coloque 'Base Otif 2026.xlsx' na raiz ou envie a base na barra lateral.")
        st.stop()
except Exception as e:
    st.error(f"Erro ao preparar a base: {e}")
    st.stop()

COL = {
    "CD Origem": escolher_coluna(df, ["CD Origem"]),
    "Empresa ICG": escolher_coluna(df, ["Empresa ICG"]),
    "Transporte": escolher_coluna(df, ["Transporte"]),
    "Região Destino": escolher_coluna(df, ["Regiao Destino", "Região Destino"]),
    "UF Destino": escolher_coluna(df, ["UF Destino"]),
    "Cidade Destino": escolher_coluna(df, ["Cidade Destino"]),
    "Status Faturamento": escolher_coluna(df, ["Status Faturamento"]),
    "Status Expedição": escolher_coluna(df, ["Status Expedicao"]),
    "Status Entrega": escolher_coluna(df, ["Status Entrega"]),
    "Status OTIF": escolher_coluna(df, ["Status Geral OTIF"]),
    "Pedido": escolher_coluna(df, ["Pedido"]),
}

with st.sidebar:
    meses_data = sorted(df["Mês Início"].dropna().unique(), reverse=True)
    rotulos = {pd.Timestamp(m): pd.Timestamp(m).strftime("%m/%Y") for m in meses_data}
    meses_escolhidos = st.multiselect("Mês/Ano", meses_data, default=meses_data[:1], format_func=lambda x: rotulos[pd.Timestamp(x)])
    filtros = {}
    for nome, c in [("Operador Logístico", "Operador Logístico"), ("CD Origem", COL["CD Origem"]), ("Empresa ICG", COL["Empresa ICG"]), ("Região Destino", COL["Região Destino"]), ("Transporte", COL["Transporte"]), ("UF Destino", COL["UF Destino"])]:
        if c:
            opcoes = sorted(df[c].dropna().astype(str).unique())
            if nome == "Transporte":
                valores_padrao = {"CORREIOS", "TRANSPORTADORA"}
                padrao = [opcao for opcao in opcoes if str(opcao).strip().upper() in valores_padrao]
            else:
                padrao = []
            filtros[c] = st.multiselect(nome, opcoes, default=padrao)
    st.divider()
    meta = st.number_input("Meta gerencial SLA D+2 (%)", 0.0, 100.0, 95.0, .5)
    volume_minimo = st.number_input(
        "Volume mínimo para rankings",
        min_value=0,
        value=0,
        step=10,
        help="Use 0 para exibir todas as operações nos rankings, sem aplicar volume mínimo.",
    )

mask = pd.Series(True, index=df.index)
if meses_escolhidos:
    mask &= df["Mês Início"].isin(pd.to_datetime(meses_escolhidos))
for coluna, valores in filtros.items():
    if valores:
        mask &= df[coluna].astype(str).isin(valores)
dff = df.loc[mask].copy()
validos = dff[dff["Lead Time"].notna()].copy()

st.title("🚚 Indicador de Performance dos Operadores Logísticos")
st.caption(f"Atualizado em {datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M')} | SLA proposto: até D+2")

vol = validos["Chave Volume"].nunique()
sla2, sla4 = taxa(validos, 2), taxa(validos, 4)
impactados = validos["Lead Time"].between(3, 4, inclusive="both").sum()
lead_medio = validos["Lead Time"].mean()

abas = st.tabs(["📌 Resumo Executivo", "📈 Nível de Serviço", "🔄 Cenários D+2 x D+4", "🚛 Operadores Logísticos", "🌎 Geografia e CDs", "⚙️ Fluxo Operacional", "🧭 Análises Gerenciais", "📋 Detalhamento"])

with abas[0]:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Pedidos", fmt_num(vol)); c2.metric("SLA D+2", fmt_pct(sla2), f"{sla2-meta:+.1f} p.p. vs meta" if pd.notna(sla2) else None)
    c3.metric("SLA D+4", fmt_pct(sla4)); c4.metric("Gap D+4 → D+2", fmt_pct(sla4-sla2) if pd.notna(sla2) else "-")
    c5.metric("Impactados D+3/D+4", fmt_num(impactados)); c6.metric("Lead time médio", f"{lead_medio:.2f} dias".replace(".", ",") if pd.notna(lead_medio) else "-")

    periodo = st.radio("Período da evolução", [3, 6, 9, 12], index=3, horizontal=True, format_func=lambda x: f"{x} meses")
    fim = df["Mês Início"].max()
    inicio = fim - pd.DateOffset(months=periodo-1) if pd.notna(fim) else pd.NaT
    evolucao_base = df[(df["Mês Início"] >= inicio) & (df["Mês Início"] <= fim)].copy() if pd.notna(fim) else df.iloc[0:0]
    # Mantém os demais filtros e ignora apenas a seleção pontual de mês para permitir a curva histórica.
    for coluna, valores in filtros.items():
        if valores:
            evolucao_base = evolucao_base[evolucao_base[coluna].astype(str).isin(valores)]
    mensal = evolucao_base[evolucao_base["Lead Time"].notna()].groupby("Mês Início").agg(
        Registros=("Lead Time", "size"),
        D2=("Lead Time", lambda s: s.le(2).sum()),
        D4=("Lead Time", lambda s: s.le(4).sum())
    ).reset_index()
    if not mensal.empty:
        mensal["SLA D+2"] = mensal["D2"] / mensal["Registros"] * 100
        mensal["SLA D+4"] = mensal["D4"] / mensal["Registros"] * 100
        mensal["Mês"] = mensal["Mês Início"].dt.strftime("%m/%Y")
        longo = mensal.melt("Mês", ["SLA D+2", "SLA D+4"], var_name="Cenário", value_name="SLA")
        fig = px.line(
            longo, x="Mês", y="SLA", color="Cenário", markers=True,
            title=f"Evolução do nível de serviço • últimos {periodo} meses",
            color_discrete_map={"SLA D+2": CORES["azul2"], "SLA D+4": CORES["azul"]},
            category_orders={"Mês": mensal["Mês"].tolist()}
        )
        fig.update_traces(mode="lines+markers+text", texttemplate="%{y:.1f}%", textposition="top center", cliponaxis=False)
        fig.update_yaxes(ticksuffix="%", range=[max(0, float(longo["SLA"].min()) - 8), min(105, float(longo["SLA"].max()) + 8)])
        fig.add_hline(y=meta, line_dash="dash", line_color=CORES["amarelo"], annotation_text="Meta D+2")
        st.plotly_chart(estilizar(fig, 470), use_container_width=True)

        # Evolução mensal somente do lead time médio, usando o mesmo período e os mesmos filtros.
        lead_mensal = evolucao_base[evolucao_base["Lead Time"].notna()].groupby("Mês Início").agg(
            Lead_Time_Médio=("Lead Time", "mean")
        ).reset_index()
        if not lead_mensal.empty:
            lead_mensal["Mês"] = lead_mensal["Mês Início"].dt.strftime("%m/%Y")
            fig_lead = px.line(
                lead_mensal,
                x="Mês",
                y="Lead_Time_Médio",
                markers=True,
                text="Lead_Time_Médio",
                title=f"Evolução do lead time médio • últimos {periodo} meses",
                category_orders={"Mês": lead_mensal["Mês"].tolist()},
                color_discrete_sequence=[CORES["azul2"]],
            )
            fig_lead.update_traces(
                mode="lines+markers+text",
                texttemplate="%{text:.1f} dias",
                textposition="top center",
                cliponaxis=False,
            )
            fig_lead.update_yaxes(ticksuffix=" dias", rangemode="tozero")
            st.plotly_chart(estilizar(fig_lead, 430), use_container_width=True)
    else:
        st.info("Não há histórico disponível para o período selecionado.")

    st.subheader("SLA D+2 por operação")
    sla_col1, sla_col2 = st.columns(2)
    with sla_col1:
        op = resumo_dimensao(validos, "Operador Logístico")
        op = op[op["Registros"] >= volume_minimo].sort_values("SLA D+2")
        if not op.empty:
            fig = px.bar(op, x="SLA D+2", y="Operador Logístico", orientation="h", text="SLA D+2", title="SLA D+2 por Operador Logístico", color="SLA D+2", color_continuous_scale="RdYlGn", range_color=[0, 100])
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="inside")
            fig.update_xaxes(ticksuffix="%", range=[0, 100])
            st.plotly_chart(estilizar(fig), use_container_width=True)
    with sla_col2:
        cd_col = COL["CD Origem"]
        if cd_col:
            cd_resumo = resumo_dimensao(validos, cd_col)
            cd_resumo = cd_resumo[cd_resumo["Registros"] >= volume_minimo].sort_values("SLA D+2")
            if not cd_resumo.empty:
                fig = px.bar(cd_resumo, x="SLA D+2", y=cd_col, orientation="h", text="SLA D+2", title="SLA D+2 por CD Origem", color="SLA D+2", color_continuous_scale="RdYlGn", range_color=[0, 100])
                fig.update_traces(texttemplate="%{text:.1f}%", textposition="inside")
                fig.update_xaxes(ticksuffix="%", range=[0, 100])
                st.plotly_chart(estilizar(fig), use_container_width=True)

    # Lead time médio por operador e CD, usando os mesmos filtros dos gráficos de SLA.
    st.subheader("Lead time médio por operação")
    lead_col1, lead_col2 = st.columns(2)
    with lead_col1:
        lead_operador = resumo_dimensao(validos, "Operador Logístico")
        lead_operador = lead_operador[lead_operador["Registros"] >= volume_minimo].sort_values("Lead_Time_Médio", ascending=False)
        if not lead_operador.empty:
            fig = px.bar(
                lead_operador, x="Lead_Time_Médio", y="Operador Logístico", orientation="h",
                text="Lead_Time_Médio", title="Lead time médio por Operador Logístico",
                color="Lead_Time_Médio", color_continuous_scale="Blues"
            )
            fig.update_traces(texttemplate="%{text:.1f} dias", textposition="inside", cliponaxis=False)
            fig.update_xaxes(ticksuffix=" dias", rangemode="tozero")
            st.plotly_chart(estilizar(fig), use_container_width=True)
    with lead_col2:
        cd_col = COL["CD Origem"]
        if cd_col:
            lead_cd = resumo_dimensao(validos, cd_col)
            lead_cd = lead_cd[lead_cd["Registros"] >= volume_minimo].sort_values("Lead_Time_Médio", ascending=False)
            if not lead_cd.empty:
                fig = px.bar(
                    lead_cd, x="Lead_Time_Médio", y=cd_col, orientation="h",
                    text="Lead_Time_Médio", title="Lead time médio por CD Origem",
                    color="Lead_Time_Médio", color_continuous_scale="Blues"
                )
                fig.update_traces(texttemplate="%{text:.1f} dias", textposition="inside", cliponaxis=False)
                fig.update_xaxes(ticksuffix=" dias", rangemode="tozero")
                st.plotly_chart(estilizar(fig), use_container_width=True)

    st.subheader("Distribuição por faixa de lead time")
    dist_col1, dist_col2 = st.columns(2)
    faixa = validos["Faixa Lead Time"].value_counts(sort=False).rename_axis("Faixa").reset_index(name="Volume")
    faixa["Percentual"] = faixa["Volume"] / faixa["Volume"].sum() * 100 if faixa["Volume"].sum() else 0
    with dist_col1:
        fig = px.bar(faixa, x="Faixa", y="Volume", text="Volume", title="Distribuição por faixa de lead time • volume", color="Faixa", color_discrete_sequence=px.colors.sequential.Blues_r)
        fig.update_traces(texttemplate="%{text:,.0f}", textposition="auto")
        st.plotly_chart(estilizar(fig), use_container_width=True)
    with dist_col2:
        fig = px.bar(faixa, x="Faixa", y="Percentual", text="Percentual", title="Distribuição por faixa de lead time • percentual", color="Faixa", color_discrete_sequence=px.colors.sequential.Blues_r)
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="auto")
        fig.update_yaxes(ticksuffix="%", range=[0, max(5, float(faixa["Percentual"].max()) * 1.18)])
        st.plotly_chart(estilizar(fig), use_container_width=True)

with abas[1]:
    dimensoes = {"Operador Logístico": "Operador Logístico", "CD Origem": COL["CD Origem"], "Empresa ICG": COL["Empresa ICG"], "Região Destino": COL["Região Destino"], "UF Destino": COL["UF Destino"], "Cidade Destino": COL["Cidade Destino"], "Transporte": COL["Transporte"]}
    dimensoes = {k: v for k, v in dimensoes.items() if v}
    nome = st.selectbox("Analisar por", list(dimensoes), index=0)
    coluna = dimensoes[nome]
    r = resumo_dimensao(validos, coluna)
    r = r[r["Registros"] >= volume_minimo].sort_values("SLA D+2")
    if not r.empty:
        longo = r.head(25).melt([coluna], ["SLA D+2", "SLA D+4"], var_name="Cenário", value_name="SLA")
        fig = px.bar(longo, x="SLA", y=coluna, color="Cenário", barmode="group", orientation="h", text="SLA", title=f"Comparativo por {nome}", color_discrete_map={"SLA D+2": CORES["azul2"], "SLA D+4": CORES["azul"]})
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="inside", cliponaxis=False)
        fig.update_xaxes(ticksuffix="%", range=[0, 105])
        st.plotly_chart(estilizar(fig, max(450, len(r.head(25))*28)), use_container_width=True)
        st.dataframe(r.style.format({"Lead_Time_Médio": "{:.2f}", "Mediana": "{:.1f}", "P90": "{:.1f}", "SLA D+2": "{:.1f}%", "SLA D+4": "{:.1f}%", "Gap (p.p.)": "{:.1f}"}), use_container_width=True, hide_index=True)

with abas[2]:
    st.subheader("Cenário atual D+4 versus cenário proposto D+2")
    a, b, c, d = st.columns(4)
    a.metric("Atual D+4", fmt_pct(sla4)); b.metric("Proposto D+2", fmt_pct(sla2)); c.metric("Redução", fmt_pct(sla4-sla2) if pd.notna(sla2) else "-"); d.metric("Registros reclassificados", fmt_num(impactados))
    cen_dims = {"Operador Logístico": "Operador Logístico", "CD Origem": COL["CD Origem"], "Região Destino": COL["Região Destino"], "UF Destino": COL["UF Destino"], "Cidade Destino": COL["Cidade Destino"], "Empresa ICG": COL["Empresa ICG"]}
    cen_dims = {k: v for k, v in cen_dims.items() if v}
    nome = st.selectbox("Detalhar impacto por", list(cen_dims), index=0, key="cen_dim")
    coluna = cen_dims[nome]
    rc = resumo_dimensao(validos, coluna)
    rc = rc[rc["Registros"] >= volume_minimo].sort_values(["Impactados", "Gap (p.p.)"], ascending=False).head(25)
    if not rc.empty:
        x, y = st.columns(2)
        with x:
            fig = px.bar(rc, x=coluna, y="Impactados", text_auto=True, title="Volume que muda de classificação", color="Impactados", color_continuous_scale="Oranges")
            st.plotly_chart(estilizar(fig), use_container_width=True)
        with y:
            fig = px.scatter(
                rc,
                x="SLA D+2",
                y="Gap (p.p.)",
                size="Registros",
                text="SLA D+2",
                hover_name=coluna,
                hover_data={
                    "SLA D+2": ":.1f",
                    "SLA D+4": ":.1f",
                    "Gap (p.p.)": ":.1f",
                    "Impactados": ":,.0f",
                    "Registros": ":,.0f",
                },
                title="Matriz de impacto: SLA x gap",
                size_max=48,
            )
            # Mantém a proporção por volume, melhora a visualização dos menores volumes
            # e identifica todos os pontos diretamente na matriz.
            fig.update_traces(
                textposition="middle center",
                texttemplate="%{text:.1f}%",
                textfont=dict(size=12, color="white"),
                marker=dict(
                    sizemin=24,
                    color="#C94F56",
                    opacity=0.85,
                    line=dict(width=1.5, color="white"),
                ),
                cliponaxis=False,
            )
            # Mantém o nome de cada dimensão visível acima da respectiva bolha.
            for _, ponto in rc.iterrows():
                fig.add_annotation(
                    x=ponto["SLA D+2"],
                    y=ponto["Gap (p.p.)"],
                    text=str(ponto[coluna]),
                    showarrow=False,
                    yshift=28,
                    font=dict(size=11, color="#475569"),
                )
            fig.update_xaxes(ticksuffix="%")
            fig.update_yaxes(ticksuffix=" p.p.")
            fig.update_layout(margin=dict(l=10, r=35, t=55, b=15))
            st.plotly_chart(estilizar(fig, 460), use_container_width=True)
        st.dataframe(rc.style.format({"Lead_Time_Médio": "{:.2f}", "SLA D+2": "{:.1f}%", "SLA D+4": "{:.1f}%", "Gap (p.p.)": "{:.1f}"}), use_container_width=True, hide_index=True)
        baixar_csv(rc, "cenario_d2_d4.csv", "⬇️ Baixar comparativo")

with abas[3]:
    st.subheader("Performance por Operador Logístico com abertura por CD de origem")
    rop = resumo_dimensao(validos, "Operador Logístico")
    rop = rop[rop["Registros"] >= volume_minimo]
    if not rop.empty:
        x, y = st.columns(2)
        with x:
            p = rop.sort_values("SLA D+2")
            fig = px.bar(p, x="SLA D+2", y="Operador Logístico", orientation="h", text="SLA D+2", title="Ranking SLA D+2", color="SLA D+2", color_continuous_scale="RdYlGn", range_color=[0, 100])
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="inside", cliponaxis=False)
            fig.update_xaxes(ticksuffix="%", range=[0, 105])
            fig.add_vline(x=meta, line_dash="dash", line_color=CORES["amarelo"])
            st.plotly_chart(estilizar(fig), use_container_width=True)
        with y:
            p = rop.sort_values("P90", ascending=False)
            fig = px.bar(p, x="P90", y="Operador Logístico", orientation="h", text_auto=".1f", title="P90 do lead time", color="P90", color_continuous_scale="Reds")
            st.plotly_chart(estilizar(fig), use_container_width=True)
    # Observação dinâmica do P90, calculada com os mesmos filtros da guia.
    if not rop.empty:
        st.markdown("### Como interpretar o P90")
        st.markdown(
            "**P90 significa Percentil 90 do lead time.** É o prazo dentro do qual "
            "aproximadamente **90% dos registros foram concluídos**, enquanto os outros "
            "10% apresentaram tempo superior."
        )
        st.markdown("**No resultado filtrado:**")
        for _, linha in rop.sort_values("Operador Logístico").iterrows():
            operador = str(linha["Operador Logístico"])
            p90 = float(linha["P90"])
            p90_txt = f"{p90:.1f}".replace(".", ",")
            st.markdown(
                f"- **{operador}: P90 de {p90_txt} dias.**  "
                f"Aproximadamente 90% dos registros de {operador} tiveram lead time de até {p90_txt} dias."
            )
        st.caption("A observação acima é atualizada automaticamente conforme os filtros selecionados.")

    cd = COL["CD Origem"]
    if cd:
        abertura = validos.groupby(["Operador Logístico", cd], dropna=False).agg(Registros=("Chave Volume", "size"), Pedidos=("Chave Volume", "nunique"), Lead_Time_Médio=("Lead Time", "mean"), Dentro_D2=("Lead Time", lambda s: s.le(2).sum()), Dentro_D4=("Lead Time", lambda s: s.le(4).sum()), Impactados=("Lead Time", lambda s: s.between(3, 4, inclusive="both").sum())).reset_index()
        abertura["SLA D+2"] = abertura["Dentro_D2"] / abertura["Registros"] * 100
        abertura["SLA D+4"] = abertura["Dentro_D4"] / abertura["Registros"] * 100
        abertura["Gap (p.p.)"] = abertura["SLA D+4"] - abertura["SLA D+2"]
        st.dataframe(abertura.sort_values(["Operador Logístico", "SLA D+2"]).style.format({"Lead_Time_Médio": "{:.2f}", "SLA D+2": "{:.1f}%", "SLA D+4": "{:.1f}%", "Gap (p.p.)": "{:.1f}"}), use_container_width=True, hide_index=True)

with abas[4]:
    geo_dims = {"Operador Logístico": "Operador Logístico", "CD Origem": COL["CD Origem"], "Região Destino": COL["Região Destino"], "UF Destino": COL["UF Destino"], "Cidade Destino": COL["Cidade Destino"]}
    geo_dims = {k: v for k, v in geo_dims.items() if v}
    nome = st.selectbox("Visão", list(geo_dims), index=1 if len(geo_dims) > 1 else 0, key="geo")
    coluna = geo_dims[nome]
    rg = resumo_dimensao(validos, coluna)
    rg = rg[rg["Registros"] >= volume_minimo].sort_values("SLA D+2").head(30)
    if not rg.empty:
        fig = px.bar(rg, x="SLA D+2", y=coluna, orientation="h", text="SLA D+2", title=f"SLA D+2 por {nome}", color="Registros", color_continuous_scale="Blues")
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="inside", cliponaxis=False)
        fig.update_xaxes(ticksuffix="%", range=[0, 105])
        st.plotly_chart(estilizar(fig, max(480, len(rg)*26)), use_container_width=True)
    if COL["CD Origem"]:
        piv = validos.pivot_table(index="Operador Logístico", columns=COL["CD Origem"], values="Lead Time", aggfunc=lambda s: s.le(2).mean()*100)
        if not piv.empty:
            # Monta os rótulos manualmente para não exibir apenas "%" nas células vazias.
            texto_heatmap = np.where(
                piv.notna(),
                np.vectorize(lambda valor: f"{valor:.1f}%")(piv.fillna(0).to_numpy()),
                "",
            )
            fig = px.imshow(piv, text_auto=False, aspect="auto", color_continuous_scale="RdYlGn", zmin=0, zmax=100, title="Heatmap SLA D+2: operador logístico x CD origem")
            fig.update_traces(
                text=texto_heatmap,
                texttemplate="%{text}",
                hovertemplate="Operador Logístico=%{y}<br>CD Origem=%{x}<br>SLA D+2=%{z:.1f}%<extra></extra>",
            )
            fig.update_layout(coloraxis_colorbar=dict(title="SLA D+2", ticksuffix="%"))
            st.plotly_chart(estilizar(fig, 500), use_container_width=True)

with abas[5]:
    st.subheader("Fluxo Operacional: Faturamento e Expedição")
    etapas = []
    for nome_etapa, coluna_status in [
        ("Faturamento", COL["Status Faturamento"]),
        ("Expedição", COL["Status Expedição"]),
    ]:
        if coluna_status:
            contagem = dff[coluna_status].fillna("Sem status").astype(str).value_counts(dropna=False)
            total_etapa = int(contagem.sum())
            for status_etapa, volume_etapa in contagem.items():
                etapas.append({
                    "Etapa": nome_etapa,
                    "Status": status_etapa,
                    "Volume": int(volume_etapa),
                    "Percentual": (volume_etapa / total_etapa * 100) if total_etapa else 0,
                })

    if etapas:
        fluxo_df = pd.DataFrame(etapas)
        graf_volume, graf_percentual = st.columns(2)
        with graf_volume:
            fig = px.bar(
                fluxo_df, x="Etapa", y="Volume", color="Status", text="Volume",
                barmode="stack", title="Faturamento e Expedição por volume"
            )
            fig.update_traces(texttemplate="%{text:,.0f}", textposition="inside")
            st.plotly_chart(estilizar(fig), use_container_width=True)
        with graf_percentual:
            fig = px.bar(
                fluxo_df, x="Etapa", y="Percentual", color="Status", text="Percentual",
                barmode="stack", title="Faturamento e Expedição por percentual"
            )
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="inside")
            fig.update_yaxes(ticksuffix="%", range=[0, 100])
            st.plotly_chart(estilizar(fig), use_container_width=True)

        st.dataframe(
            fluxo_df.style.format({"Volume": "{:,.0f}", "Percentual": "{:.1f}%"}),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("Não há dados de Faturamento ou Expedição para os filtros selecionados.")

with abas[6]:
    st.subheader("Leitura automática para preparação da reunião")
    rop = resumo_dimensao(validos, "Operador Logístico")
    elegiveis = rop[rop["Registros"] >= volume_minimo] if not rop.empty else pd.DataFrame()
    alertas = []
    if pd.notna(sla2) and sla2 < meta:
        alertas.append(f"SLA D+2 consolidado em {fmt_pct(sla2)}, {meta-sla2:.1f} p.p. abaixo da meta de {fmt_pct(meta)}.")
    if impactados:
        alertas.append(f"{fmt_num(impactados)} registros em D+3/D+4 mudam para Fora do Prazo no cenário D+2.")
    if not elegiveis.empty:
        pior = elegiveis.sort_values("SLA D+2").iloc[0]
        maior = elegiveis.sort_values("Impactados", ascending=False).iloc[0]
        alertas.append(f"Operador com menor SLA D+2: {pior['Operador Logístico']}, com {fmt_pct(pior['SLA D+2'])}.")
        alertas.append(f"Maior impacto na mudança para D+2: {maior['Operador Logístico']}, com {fmt_num(maior['Impactados'])} registros em D+3/D+4.")
    for alerta in alertas:
        st.markdown(f'<div class="alerta">⚠️ {alerta}</div>', unsafe_allow_html=True)
    if not alertas:
        st.markdown('<div class="ok">✅ Nenhum alerta automático nos filtros atuais.</div>', unsafe_allow_html=True)

    acoes = []
    if not elegiveis.empty:
        for _, r in elegiveis.sort_values(["SLA D+2", "Impactados"], ascending=[True, False]).iterrows():
            prioridade = "Alta" if r["SLA D+2"] < meta-10 or r["Impactados"] >= elegiveis["Impactados"].quantile(.75) else "Média"
            acoes.append({"Prioridade": prioridade, "Operador Logístico": r["Operador Logístico"], "Evidência": f"SLA D+2 {fmt_pct(r['SLA D+2'])}; {fmt_num(r['Impactados'])} em D+3/D+4; P90 {r['P90']:.1f} dias", "Ação recomendada": "Validar causas por CD/rota, definir contramedida e acompanhar semanalmente.", "Responsável": "A definir", "Prazo": "A definir", "Status": "Não iniciado"})
    plano = pd.DataFrame(acoes)
    if not plano.empty:
        st.data_editor(plano, use_container_width=True, hide_index=True, num_rows="dynamic", key="plano")
        baixar_csv(plano, "plano_de_acao.csv", "⬇️ Baixar plano de ação")

with abas[7]:
    exibicao = [c for c in [COL["Pedido"], "Operador Logístico", COL["CD Origem"], COL["Empresa ICG"], COL["Transporte"], COL["Região Destino"], COL["UF Destino"], COL["Cidade Destino"], "Data Referência", "Lead Time", "Status D+2", "Status D+4", "Impactado D+2"] if c]
    status = st.multiselect("Classificação D+2", ["No Prazo", "Fora do Prazo", "Pendente"], default=["No Prazo", "Fora do Prazo"])
    detalhe = dff[dff["Status D+2"].isin(status)][exibicao]
    st.dataframe(detalhe, use_container_width=True, hide_index=True, height=520)
    baixar_csv(detalhe, "detalhamento_sla.csv", "⬇️ Baixar detalhamento")

with st.expander("Conferência do De/Para de Operadores Logísticos"):
    conferencia = pd.DataFrame([{"CD Origem": cd.title(), "Operador Logístico": op} for cd, op in DE_PARA_OPERADOR.items()])
    st.dataframe(conferencia, use_container_width=True, hide_index=True)
    nao_mapeados = sorted(df.loc[df["Operador Logístico"].eq("NÃO MAPEADO"), COL["CD Origem"]].dropna().astype(str).unique())
    if nao_mapeados:
        st.warning("CDs sem mapeamento: " + ", ".join(nao_mapeados))
