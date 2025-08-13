import streamlit as st
import math
import time
from PIL import Image
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import json
from fpdf import FPDF
from datetime import datetime
from io import BytesIO
import os

# --- CONFIGURAÇÕES GERAIS E ESTILO ---
st.set_page_config(page_title="Calculadora IsolaFácil", layout="wide")

st.markdown("""
<style>
    .main { background-color: #FFFFFF; }
    .block-container { padding-top: 2rem; }
    h1, h2, h3, h4 { color: #003366; }
    .stButton>button { background-color: #198754; color: white; border-radius: 8px; height: 3em; width: 100%; }
    .stMetric { border: 1px solid #E0E0E0; padding: 10px; border-radius: 8px; text-align: center; }
    input[type="radio"], input[type="checkbox"] { accent-color: #003366; }
    .stSuccess, .stInfo, .stWarning { border-radius: 8px; padding: 1rem; }
    .stSuccess { background-color: #e6f2e6; color: #1a4d2e; border: 1px solid #1a4d2e; }
    .stInfo { background-color: #e6eef2; color: #1f3c58; border: 1px solid #1f3c58; }
    .stWarning { background-color: #f2f2e6; color: #514e21; border: 1px solid #514e21; }
</style>
""", unsafe_allow_html=True)

# --- CONSTANTE GLOBAL ---
sigma = 5.67e-8

# --- CONEXÃO E FUNÇÕES DO GOOGLE SHEETS ---
@st.cache_resource(ttl=600)
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    gcp_json = json.loads(st.secrets["GCP_JSON"])
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(gcp_json, scope)
    return gspread.authorize(credentials)

def get_worksheet(sheet_name):
    client = get_gspread_client()
    sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1W1JHXAnGJeWbGVK0AmORux5I7CYTEwoBIvBfVKO40aY")
    return sheet.worksheet(sheet_name)

@st.cache_data(ttl=300)
def carregar_isolantes():
    try:
        worksheet = get_worksheet("Isolantes 2")
        df = pd.DataFrame(worksheet.get_all_records())
        df['T_min'] = pd.to_numeric(df['T_min'], errors='coerce').fillna(-999)
        df['T_max'] = pd.to_numeric(df['T_max'], errors='coerce').fillna(9999)
        return df
    except Exception as ex:
        st.error(f"Erro ao carregar materiais isolantes: {ex}")
        return pd.DataFrame()

@st.cache_data(ttl=300)
def carregar_acabamentos():
    try:
        worksheet = get_worksheet("Emissividade")
        df = pd.DataFrame(worksheet.get_all_records())
        df['emissividade'] = pd.to_numeric(df['emissividade'].astype(str).str.replace(',', '.'), errors='coerce').fillna(0.9)
        return df
    except Exception as ex:
        st.error(f"Erro ao carregar acabamentos: {ex}")
        return pd.DataFrame()

# --- FUNÇÕES DE CÁLCULO ---
def calcular_k(k_func_str, T_media):
    try:
        k_func_safe = str(k_func_str).replace(',', '.')
        return eval(k_func_safe, {"math": math, "T": T_media})
    except Exception as ex:
        st.error(f"Erro na fórmula k(T) '{k_func_str}': {ex}")
        return None

def calcular_h_conv(Tf, To, geometry, outer_diameter_m=None, wind_speed_ms=0):
    Tf_K, To_K = Tf + 273.15, To + 273.15
    T_film_K = (Tf_K + To_K) / 2
    g, beta = 9.81, 1 / T_film_K
    nu = 1.589e-5 * (T_film_K / 293.15)**0.7
    alpha = 2.25e-5 * (T_film_K / 293.15)**0.8
    k_ar = 0.0263
    Pr = nu / alpha
    delta_T = abs(Tf - To)
    if delta_T == 0: return 0
    
    if wind_speed_ms >= 1.0:
        L_c = 1.0 if geometry == "Superfície Plana" else outer_diameter_m
        if L_c is None or L_c == 0: L_c = 1.0
        Re = (wind_speed_ms * L_c) / nu
        if Re < 5e5:
            Nu = 0.664 * (Re**0.5) * (Pr**(1/3))
        else:
            Nu = (0.037 * (Re**0.8) - 871) * (Pr**(1/3))
    else:
        if geometry == "Superfície Plana":
            L_c = 0.1
            Ra = (g * beta * delta_T * L_c**3) / (nu * alpha)
            Nu = 0.27 * Ra**(1/4)
        elif geometry == "Tubulação":
            L_c = outer_diameter_m
            Ra = (g * beta * delta_T * L_c**3) / (nu * alpha)
            term1 = 0.60
            term2 = (0.387 * Ra**(1/6)) / ((1 + (0.559 / Pr)**(9/16))**(8/27))
            Nu = (term1 + term2)**2
        else:
            Nu = 0
    
    return (Nu * k_ar) / L_c

def encontrar_temperatura_face_fria(Tq, To, L_total, k_func_str, geometry, emissividade, pipe_diameter_m=None, wind_speed_ms=0):
    Tf = To + 10.0
    max_iter, step, min_step, tolerancia = 1000, 50.0, 0.001, 0.5
    erro_anterior = None
    
    for i in range(max_iter):
        T_media = (Tq + Tf) / 2
        k = calcular_k(k_func_str, T_media)
        if k is None or k <= 0: return None, None, False

        if geometry == "Superfície Plana":
            q_conducao = k * (Tq - Tf) / L_total
            outer_surface_diameter = L_total
        elif geometry == "Tubulação":
            r_inner = pipe_diameter_m / 2
            r_outer = r_inner + L_total
            if r_inner <= 0 or r_outer <= r_inner: return None, None, False
            q_conducao = (k * (Tq - Tf)) / (r_outer * math.log(r_outer / r_inner))
            outer_surface_diameter = r_outer * 2

        Tf_K, To_K = Tf + 273.15, To + 273.15
        h_conv = calcular_h_conv(Tf, To, geometry, outer_surface_diameter, wind_speed_ms)
        q_rad = emissividade * sigma * (Tf_K**4 - To_K**4)
        q_conv = h_conv * (Tf - To)
        q_transferencia = q_conv + q_rad
        
        erro = q_conducao - q_transferencia
        if abs(erro) < tolerancia: return Tf, q_transferencia, True

        if erro_anterior is not None and erro * erro_anterior < 0:
            step = max(min_step, step * 0.5)
        Tf += step if erro > 0 else -step
        erro_anterior = erro
        
    return Tf, None, False

# --- FUNÇÕES DE GERAÇÃO DE PDF ---
def preparar_pdf():
    pdf = FPDF()
    pdf.add_page()
    try:
        pdf.add_font('DejaVu', '', 'DejaVuSans.ttf', uni=True)
        pdf.add_font('DejaVu', 'B', 'DejaVuSans-Bold.ttf', uni=True)
        font_family = 'DejaVu'
    except RuntimeError:
        # Não mostra mais aviso na tela principal para não poluir
        font_family = 'Arial'
    return pdf, font_family

def gerar_pdf(dados):
    pdf, font_family = preparar_pdf()
    
    pdf.set_font(font_family, 'B', 16)
    pdf.cell(0, 10, "Relatório de Cálculo Térmico - IsolaFácil", 0, 1, "C")
    pdf.ln(10)
    
    pdf.set_font(font_family, '', 10)
    data_simulacao = datetime.now().strftime("%d/%m/%Y")
    pdf.cell(0, 5, f"Data da Simulação: {data_simulacao}", 0, 1, "R")
    pdf.ln(5)
    
    # --- Bloco de Texto para Entradas ---
    pdf.set_font(font_family, 'B', 12)
    pdf.cell(0, 8, "1. Parâmetros de Entrada", ln=1)
    pdf.set_font(font_family, '', 11)
    
    texto_entradas = f"""
    Material do Isolante: {dados.get('material', '')}
    Acabamento Externo: {dados.get('acabamento', '')}
    Tipo de Superfície: {dados.get('geometria', '')}
    """
    if dados.get("geometria") == "Tubulação":
        texto_entradas += f"Diâmetro da Tubulação: {dados.get('diametro_tubo', 0)} mm\n"
    texto_entradas += f"""
    Número de Camadas: {dados.get('num_camadas', '')}
    Espessura Total: {dados.get('esp_total', 0)} mm
    Temp. da Face Quente: {dados.get('tq', 0)} °C
    Temp. Ambiente: {dados.get('to', 0)} °C
    Emissividade (e): {dados.get('emissividade', '')}
    """
    pdf.multi_cell(0, 6, texto_entradas.strip())
    pdf.ln(5)

    # --- Bloco de Texto para Resultados ---
    pdf.set_font(font_family, 'B', 12)
    pdf.cell(0, 8, "2. Resultados do Cálculo Térmico", ln=1)
    pdf.set_font(font_family, '', 11)

    texto_resultados = (
        f"Temperatura da Face Fria: {dados.get('tf', 0):.1f} °C\n"
        f"Perda de Calor com Isolante: {dados.get('perda_com_kw', 0):.3f} kW/m²\n"
        f"Perda de Calor sem Isolante: {dados.get('perda_sem_kw', 0):.3f} kW/m²\n"
    )
    pdf.multi_cell(0, 6, texto_resultados.strip())
    pdf.ln(5)

    # --- Bloco de Texto para Análise Financeira ---
    if dados.get("calculo_financeiro", False):
        pdf.set_font(font_family, 'B', 12)
        pdf.cell(0, 8, "3. Análise Financeira", ln=1)
        pdf.set_font(font_family, '', 11)
        texto_financeiro = (
            f"Economia Mensal: R$ {dados.get('eco_mensal', 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') + "\n"
            f"Economia Anual: R$ {dados.get('eco_anual', 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') + "\n"
            f"Redução de Perda: {dados.get('reducao_pct', 0):.1f} %\n"
        )
        pdf.multi_cell(0, 6, texto_financeiro.strip())

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()

def gerar_pdf_frio(dados):
    pdf, font_family = preparar_pdf()

    pdf.set_font(font_family, 'B', 16)
    pdf.cell(0, 10, "Relatório de Cálculo de Condensação - IsolaFácil", 0, 1, "C")
    pdf.ln(10)
    
    pdf.set_font(font_family, '', 10)
    data_simulacao = datetime.now().strftime("%d/%m/%Y")
    pdf.cell(0, 5, f"Data da Simulação: {data_simulacao}", 0, 1, "R")
    pdf.ln(5)

    pdf.set_font(font_family, 'B', 12)
    pdf.cell(0, 8, "1. Parâmetros de Entrada", ln=1)
    pdf.set_font(font_family, '', 11)

    texto_entradas = (
        f"Material do Isolante: {dados.get('material', '')}\n"
        f"Tipo de Superfície: {dados.get('geometria', '')}\n"
    )
    if dados.get("geometria") == "Tubulação":
        texto_entradas += f"Diâmetro da Tubulação: {dados.get('diametro_tubo', 0)} mm\n"
    texto_entradas += (
        f"Temp. Interna: {dados.get('ti', 0)} °C\n"
        f"Temp. Ambiente: {dados.get('ta', 0)} °C\n"
        f"Umidade Relativa: {dados.get('ur', 0)} %\n"
        f"Velocidade do Vento: {dados.get('vento', 0)} m/s\n"
    )
    pdf.multi_cell(0, 6, texto_entradas.strip())
    pdf.ln(5)

    pdf.set_font(font_family, 'B', 12)
    pdf.cell(0, 8, "2. Resultados do Cálculo", ln=1)
    pdf.set_font(font_family, '', 11)

    texto_resultados = (
        f"Temperatura de Orvalho: {dados.get('t_orvalho', 0):.1f} °C\n"
        f"Espessura Mínima Recomendada: {dados.get('espessura_final', 0):.1f} mm\n"
    )
    pdf.multi_cell(0, 6, texto_resultados.strip())

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()
    
# --- INICIALIZAÇÃO E INTERFACE PRINCIPAL ---
try:
    logo = Image.open("logo.png")
    st.image(logo, width=300)
except FileNotFoundError:
    st.warning("Arquivo 'logo.png' não encontrado.")

st.title("Análise de Isolamento Térmico")

# Inicialização do session state para os botões de PDF
if 'calculo_realizado' not in st.session_state:
    st.session_state.calculo_realizado = False
if 'calculo_frio_realizado' not in st.session_state:
    st.session_state.calculo_frio_realizado = False

df_isolantes = carregar_isolantes()
df_acabamentos = carregar_acabamentos()

if df_isolantes.empty or df_acabamentos.empty:
    st.error("Não foi possível carregar os dados da planilha. Verifique as abas 'Isolantes 2' e 'Emissividade'.")
    st.stop()

# --- INTERFACE COM TABS ---
abas = st.tabs(["🔥 Cálculo Térmico e Financeiro", "🧊 Cálculo Térmico Frio"])

with abas[0]:
    st.subheader("Parâmetros do Isolamento Térmico")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        material_selecionado_nome = st.selectbox("Escolha o material do isolante", df_isolantes['nome'].tolist(), key="mat_quente")
    with col2:
        acabamento_selecionado_nome = st.selectbox("Tipo de isolamento / acabamento", df_acabamentos['acabamento'].tolist(), key="acab_quente")
    with col3:
        geometry = st.selectbox("Tipo de Superfície", ["Superfície Plana", "Tubulação"], key="geom_quente")

    isolante_selecionado = df_isolantes[df_isolantes['nome'] == material_selecionado_nome].iloc[0]
    k_func_str = isolante_selecionado['k_func']
    
    acabamento_selecionado = df_acabamentos[df_acabamentos['acabamento'] == acabamento_selecionado_nome].iloc[0]
    emissividade_selecionada = acabamento_selecionado['emissividade']

    pipe_diameter_mm = 0
    if geometry == "Tubulação":
        pipe_diameter_mm = st.number_input("Diâmetro externo da tubulação [mm]", min_value=1.0, value=88.9, step=0.1, format="%.1f")

    col_temp1, col_temp2, col_temp3 = st.columns(3)
    Tq = col_temp1.number_input("Temperatura da face quente [°C]", value=250.0)
    To = col_temp2.number_input("Temperatura ambiente [°C]", value=30.0)
    numero_camadas = col_temp3.number_input("Número de camadas de isolante", 1, 3, 1)

    espessuras = []
    cols_esp = st.columns(numero_camadas)
    for i in range(numero_camadas):
        esp = cols_esp[i].number_input(f"Espessura camada {i+1} [mm]", value=51.0/numero_camadas, key=f"L{i+1}_quente", min_value=0.1)
        espessuras.append(esp)
    L_total = sum(espessuras)

    st.markdown("---")
    
    calcular_financeiro = st.checkbox("Calcular retorno financeiro")
    if calcular_financeiro:
        st.subheader("Parâmetros do Cálculo Financeiro")
        st.info("💡 Os custos de combustível são pré-configurados com valores médios de mercado...")
        combustiveis = {"Óleo BPF (kg)": {"v": 3.50, "pc": 11.34, "ef": 0.80}, "Gás Natural (m³)": {"v": 3.60, "pc": 9.65, "ef": 0.75},"Lenha Eucalipto 30% umidade (ton)": {"v": 200.00, "pc": 3500.00, "ef": 0.70},"Eletricidade (kWh)": {"v": 0.75, "pc": 1.00, "ef": 1.00}}
        comb_sel_nome = st.selectbox("Tipo de combustível", list(combustiveis.keys()))
        comb_sel_obj = combustiveis[comb_sel_nome]
        editar_valor = st.checkbox("Editar custo do combustível/energia")
        if editar_valor:
            valor_comb = st.number_input("Custo combustível (R$)", min_value=0.10, value=comb_sel_obj['v'], step=0.01, format="%.2f")
        else:
            valor_comb = comb_sel_obj['v']
        col_fin1, col_fin2, col_fin3 = st.columns(3)
        m2 = col_fin1.number_input("Área do projeto (m²)", 1.0, value=10.0)
        h_dia = col_fin2.number_input("Horas de operação/dia", 1.0, 24.0, 8.0)
        d_sem = col_fin3.number_input("Dias de operação/semana", 1, 7, 5)

    st.markdown("---")

    if st.button("Calcular", key="btn_quente"):
        st.session_state.calculo_realizado = False
        if not (isolante_selecionado['T_min'] <= Tq <= isolante_selecionado['T_max']):
            st.error(f"Material inadequado! A temperatura de operação ({Tq}°C) está fora dos limites para '{material_selecionado_nome}' (Mín: {isolante_selecionado['T_min']}°C, Máx: {isolante_selecionado['T_max']}°C).")
        elif Tq <= To:
            st.error("Erro: A temperatura da face quente deve ser maior do que a temperatura ambiente.")
        else:
            with st.spinner("Realizando cálculos..."):
                Tf, q_com_isolante, convergiu = encontrar_temperatura_face_fria(Tq, To, L_total / 1000, k_func_str, geometry, emissividade_selecionada, pipe_diameter_mm / 1000)
                if convergiu:
                    st.session_state.calculo_realizado = True
                    perda_com_kw = q_com_isolante / 1000
                    h_sem = calcular_h_conv(Tq, To, geometry, (pipe_diameter_mm / 1000) if geometry == "Tubulação" else None)
                    q_rad_sem = emissividade_selecionada * sigma * ((Tq + 273.15)**4 - (To + 273.15)**4)
                    q_conv_sem = h_sem * (Tq - To)
                    perda_sem_kw = (q_rad_sem + q_conv_sem) / 1000
                    dados_para_relatorio = {"material": material_selecionado_nome, "acabamento": acabamento_selecionado_nome, "geometria": geometry, "diametro_tubo": pipe_diameter_mm, "num_camadas": numero_camadas, "esp_total": L_total, "tq": Tq, "to": To, "emissividade": emissividade_selecionada, "tf": Tf, "perda_com_kw": perda_com_kw, "perda_sem_kw": perda_sem_kw, "calculo_financeiro": calcular_financeiro}
                    if calcular_financeiro:
                        economia_kw_m2 = perda_sem_kw - perda_com_kw
                        custo_kwh = valor_comb / (comb_sel_obj['pc'] * comb_sel_obj['ef'])
                        eco_mensal = economia_kw_m2 * custo_kwh * m2 * h_dia * d_sem * 4.33
                        eco_anual = eco_mensal * 12
                        reducao_pct = ((economia_kw_m2 / perda_sem_kw) * 100) if perda_sem_kw > 0 else 0
                        dados_para_relatorio.update({"eco_mensal": eco_mensal, "eco_anual": eco_anual, "reducao_pct": reducao_pct})
                    st.session_state.dados_ultima_simulacao = dados_para_relatorio
                else:
                    st.session_state.calculo_realizado = False
                    st.error("❌ O cálculo não convergiu. Verifique os dados de entrada.")
    
    if st.session_state.get('calculo_realizado', False):
        dados = st.session_state.dados_ultima_simulacao
        st.subheader("Resultados")
        st.success(f"🌡️ Temperatura da face fria: {dados['tf']:.1f} °C".replace('.', ','))
        if dados['num_camadas'] > 1:
            T_atual = dados['tq']
            k_medio = calcular_k(k_func_str, (dados['tq'] + dados['tf']) / 2)
            if k_medio and q_com_isolante is not None:
                for i in range(dados['num_camadas'] - 1):
                    if dados['geometria'] == "Superfície Plana":
                        resistencia_camada = (espessuras[i] / 1000) / k_medio
                        delta_T_camada = q_com_isolante * resistencia_camada
                    elif dados['geometria'] == "Tubulação":
                        r_camada_i = (dados['diametro_tubo'] / 2000) + sum(espessuras[:i]) / 1000
                        r_camada_o = r_camada_i + espessuras[i] / 1000
                        q_linha = q_com_isolante * (2 * math.pi * ((dados['diametro_tubo']/2000) + L_total/1000))
                        resistencia_termica_linha = math.log(r_camada_o / r_camada_i) / (2 * math.pi * k_medio)
                        delta_T_camada = q_linha * resistencia_termica_linha
                    T_interface = T_atual - delta_T_camada
                    st.success(f"↪️ Temp. entre camada {i+1} e {i+2}: {T_interface:.1f} °C".replace('.', ','))
                    T_atual = T_interface
        st.info(f"⚡ Perda de calor com isolante: {dados['perda_com_kw']:.3f} kW/m²".replace('.', ','))
        st.warning(f"⚡ Perda de calor sem isolante: {dados['perda_sem_kw']:.3f} kW/m²".replace('.', ','))
        if dados.get('calculo_financeiro', False):
            st.subheader("Retorno Financeiro")
            m1, m2, m3 = st.columns(3)
            m1.metric("Economia Mensal", f"R$ {dados['eco_mensal']:,.2f}".replace(',','X').replace('.',',').replace('X','.'))
            m2.metric("Economia Anual", f"R$ {dados['eco_anual']:,.2f}".replace(',','X').replace('.',',').replace('X','.'))
            m3.metric("Redução de Perda", f"{dados['reducao_pct']:.1f} %")
        st.markdown("---")
        pdf_bytes = gerar_pdf(dados)
        st.download_button(label="Download Relatório PDF", data=pdf_bytes, file_name=f"Relatorio_IsolaFacil_{datetime.now().strftime('%Y%m%d')}.pdf", mime="application/pdf", key="pdf_quente")

    st.markdown("---")
    st.markdown("""
    > **Nota:** Os cálculos são realizados de acordo com as práticas recomendadas pelas normas **ASTM C680** e **ISO 12241**, em conformidade com os procedimentos da norma brasileira **ABNT NBR 16281**.
    """)

with abas[1]:
    st.subheader("Cálculo de Espessura Mínima para Minimizar Condensação")
    
    col1, col2 = st.columns(2)
    with col1:
        material_frio_nome = st.selectbox("Escolha o material do isolante", df_isolantes['nome'].tolist(), key="mat_frio")
    with col2:
        geometry_frio = st.selectbox("Tipo de Superfície", ["Superfície Plana", "Tubulação"], key="geom_frio")

    isolante_frio_selecionado = df_isolantes[df_isolantes['nome'] == material_frio_nome].iloc[0]
    k_func_str_frio = isolante_frio_selecionado['k_func']

    pipe_diameter_mm_frio = 0
    if geometry_frio == "Tubulação":
        pipe_diameter_mm_frio = st.number_input("Diâmetro externo da tubulação [mm]", min_value=1.0, value=88.9, step=0.1, format="%.1f", key="diam_frio")

    col1, col2, col3 = st.columns(3)
    Ti_frio = col1.number_input("Temperatura interna [°C]", value=5.0, key="Ti_frio")
    Ta_frio = col2.number_input("Temperatura ambiente [°C]", value=25.0, key="Ta_frio")
    UR = col3.number_input("Umidade relativa do ar [%]", 0.0, 100.0, 70.0)

    wind_speed = st.number_input("Velocidade do vento (m/s)", min_value=0.0, value=0.0, step=0.5, format="%.1f", key="wind_speed_frio")
    if wind_speed == 0:
        st.info("💡 Com velocidade do vento igual a 0 m/s, o cálculo considera convecção natural.")

    if st.button("Calcular Espessura Mínima", key="btn_frio"):
        st.session_state.calculo_frio_realizado = False
        if not (isolante_frio_selecionado['T_min'] <= Ti_frio <= isolante_frio_selecionado['T_max']):
            st.error(f"Material inadequado! A temperatura de operação ({Ti_frio}°C) está fora dos limites para '{material_frio_nome}' (Mín: {isolante_frio_selecionado['T_min']}°C, Máx: {isolante_frio_selecionado['T_max']}°C).")
        elif Ta_frio <= Ti_frio:
            st.error("Erro: A temperatura ambiente deve ser maior que a temperatura interna para o cálculo de condensação.")
        else:
            with st.spinner("Iterando para encontrar espessura..."):
                a_mag, b_mag = 17.27, 237.7
                alfa = ((a_mag * Ta_frio) / (b_mag + Ta_frio)) + math.log(UR / 100.0)
                T_orvalho = (b_mag * alfa) / (a_mag - alfa)
                st.info(f"💧 Temperatura de orvalho calculada: {T_orvalho:.1f} °C")

                espessura_final = None
                for L_teste in [i * 0.001 for i in range(1, 501)]:
                    Tf, _, convergiu = encontrar_temperatura_face_fria(
                        Ti_frio, Ta_frio, L_teste, k_func_str_frio, 
                        geometry_frio, 0.9, pipe_diameter_mm_frio / 1000, wind_speed_ms=wind_speed
                    )
                    if convergiu and Tf >= T_orvalho:
                        espessura_final = L_teste
                        break

                if espessura_final:
                    st.success(f"✅ Espessura mínima para Minimizar condensação: {espessura_final * 1000:.1f} mm".replace('.',','))
                    st.session_state.calculo_frio_realizado = True
                    dados_para_relatorio_frio = {
                        "material": material_frio_nome, "geometria": geometry_frio, "diametro_tubo": pipe_diameter_mm_frio,
                        "ti": Ti_frio, "ta": Ta_frio, "ur": UR, "vento": wind_speed,
                        "t_orvalho": T_orvalho, "espessura_final": espessura_final * 1000
                    }
                    st.session_state.dados_ultima_simulacao_frio = dados_para_relatorio_frio
                else:
                    st.session_state.calculo_frio_realizado = False
                    st.error("❌ Não foi possível encontrar uma espessura que evite condensação até 500 mm.")
    
    if st.session_state.get('calculo_frio_realizado', False):
        st.markdown("---")
        pdf_bytes_frio = gerar_pdf_frio(st.session_state.dados_ultima_simulacao_frio)
        st.download_button(
            label="Download Relatório PDF",
            data=pdf_bytes_frio,
            file_name=f"Relatorio_Condensacao_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            key="btn_pdf_frio"
        )
