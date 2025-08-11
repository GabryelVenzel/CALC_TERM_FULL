import streamlit as st
import math
import time
from PIL import Image
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import json

# --- CONFIGURAÇÕES GERAIS E ESTILO (Definido uma única vez) ---
st.set_page_config(page_title="Calculadora IsolaFácil", layout="wide")

st.markdown("""
<style>
    .main { background-color: #FFFFFF; }
    .block-container { padding-top: 2rem; }
    h1, h2, h3, h4 { color: #003366; }
    .stButton>button { background-color: #198754; color: white; border-radius: 8px; height: 3em; width: 100%; }
    .stMetric { border: 1px solid #E0E0E0; padding: 10px; border-radius: 8px; text-align: center; }
    input[type="radio"], input[type="checkbox"] { accent-color: #003366; }
</style>
""", unsafe_allow_html=True)

# --- CONSTANTES GLOBAIS ---
e = 0.9  # Emissividade
sigma = 5.67e-8  # Constante de Stefan-Boltzmann

# --- CONEXÃO COM GOOGLE SHEETS E CACHING ---
@st.cache_resource(ttl=600)
def autorizar_cliente_gspread():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    gcp_json = json.loads(st.secrets["GCP_JSON"])
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(gcp_json, scope)
    return gspread.authorize(credentials)

def get_worksheet():
    client = autorizar_cliente_gspread()
    sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1W1JHXAnGJeWbGVK0AmORux5I7CYTEwoBIvBfVKO40aY")
    return sheet.worksheet("Isolantes")

@st.cache_data(ttl=600)
def carregar_isolantes():
    try:
        worksheet = get_worksheet()
        records = worksheet.get_all_records()
        return pd.DataFrame(records)
    except Exception as ex:
        st.error(f"Erro ao conectar com o Google Sheets: {ex}")
        return pd.DataFrame()

# --- FUNÇÕES DE ADMINISTRAÇÃO DA PLANILHA ---
def cadastrar_isolante(nome, k_func):
    try:
        worksheet = get_worksheet()
        worksheet.append_row([nome, k_func])
        st.cache_data.clear()
        st.success(f"Isolante '{nome}' cadastrado com sucesso!")
    except Exception as ex:
        st.error(f"Falha ao cadastrar: {ex}")

def excluir_isolante(nome):
    try:
        worksheet = get_worksheet()
        cell = worksheet.find(nome)
        if cell:
            worksheet.delete_rows(cell.row)
            st.cache_data.clear()
            st.success(f"Isolante '{nome}' excluído com sucesso!")
            time.sleep(1)
            st.rerun()
        else:
            st.warning("Isolante não encontrado para exclusão.")
    except Exception as ex:
        st.error(f"Falha ao excluir: {ex}")

# --- FUNÇÕES DE CÁLCULO E LÓGICA ---
def calcular_k(k_func_str, T_media):
    """
    Calcula a condutividade térmica k(T) a partir da string da fórmula.
    NOTA: O uso de `eval` requer que as fórmulas na planilha sejam confiáveis.
    """
    try:
        # CORREÇÃO: Substitui vírgulas por pontos para compatibilidade decimal
        k_func_safe = str(k_func_str).replace(',', '.')
        return eval(k_func_safe, {"math": math, "T": T_media})
    except Exception as ex:
        st.error(f"Erro na fórmula k(T) '{k_func_str}': {ex}")
        return None

def calcular_h_conv(Tf, To, L_caracteristico):
    Tf_K, To_K = Tf + 273.15, To + 273.15
    T_film_K = (Tf_K + To_K) / 2
    g, beta = 9.81, 1 / T_film_K
    nu = 1.589e-5 * (T_film_K / 293.15)**0.7
    alpha = 2.25e-5 * (T_film_K / 293.15)**0.8
    k_ar = 0.0263
    delta_T = abs(Tf - To)
    if delta_T == 0: return 0
    Ra = (g * beta * delta_T * L_caracteristico**3) / (nu * alpha)
    Nu = (0.825 + (0.387 * Ra**(1/6)) / (1 + (0.492 / (nu/alpha))**(9/16))**(8/27))**2
    return (Nu * k_ar / L_caracteristico)

def encontrar_temperatura_face_fria(Tq, To, L_total, k_func_str):
    Tf = To + 10.0
    max_iter, step, min_step, tolerancia = 1000, 50.0, 0.001, 0.5
    erro_anterior = None
    for i in range(max_iter):
        T_media = (Tq + Tf) / 2
        k = calcular_k(k_func_str, T_media)
        if k is None or k <= 0:
            return None, None, False
        q_conducao = k * (Tq - Tf) / L_total
        Tf_K, To_K = Tf + 273.15, To + 273.15
        h_conv = calcular_h_conv(Tf, To, L_total)
        q_rad = e * sigma * (Tf_K**4 - To_K**4)
        q_conv = h_conv * (Tf - To)
        q_transferencia = q_conv + q_rad
        erro = q_conducao - q_transferencia
        if abs(erro) < tolerancia:
            return Tf, q_transferencia, True
        if erro_anterior is not None and erro * erro_anterior < 0:
            step = max(min_step, step * 0.5)
        Tf += step if erro > 0 else -step
        erro_anterior = erro
    return Tf, None, False

# --- INICIALIZAÇÃO DO SESSION STATE E INTERFACE PRINCIPAL ---
if 'convergiu' not in st.session_state: st.session_state.convergiu = None
if 'Tf' not in st.session_state: st.session_state.Tf = None

try:
    logo = Image.open("logo.png")
    st.image(logo, width=300)
except FileNotFoundError:
    st.warning("Arquivo 'logo.png' não encontrado.")

st.title("Calculadora IsolaFácil")

df_isolantes = carregar_isolantes()
if df_isolantes.empty:
    st.error("Não foi possível carregar materiais. Verifique a conexão e a planilha.")
    st.stop()

# --- INTERFACE LATERAL (ADMIN) ---
with st.sidebar.expander("Opções de Administrador", expanded=False):
    senha = st.text_input("Digite a senha", type="password", key="senha_admin")
    if senha == "Priner123":
        aba_admin = st.radio("Escolha a opção", ["Cadastrar Isolante", "Gerenciar Isolantes"])
        if aba_admin == "Cadastrar Isolante":
            st.subheader("Cadastrar Novo Isolante")
            nome = st.text_input("Nome do Isolante", key="novo_nome")
            modelo_k = st.radio("Modelo de função k(T)", ["Constante", "Linear", "Polinomial", "Exponencial"])
            k_func, equacao_latex = "", ""
            if modelo_k == "Constante":
                k0 = st.text_input("k₀", "0,035")
                k_func = f"{k0}"
            # ... (código para os outros modelos, similar ao seu original, construindo a string k_func)
            elif modelo_k == "Linear":
                k0 = st.text_input("k₀", "0,030")
                k1 = st.text_input("k₁ (coef. de T)", "0,0001")
                k_func = f"{k0} + {k1} * T"
            elif modelo_k == "Polinomial":
                k0 = st.text_input("k₀", "0,025")
                k1 = st.text_input("k₁ (T¹)", "0,0001")
                k2 = st.text_input("k₂ (T²)", "0.0")
                k3 = st.text_input("k₃ (T³)", "0.0")
                k4 = st.text_input("k₄ (T⁴)", "0.0")
                k_func = f"{k0} + {k1}*T + {k2}*T**2 + {k3}*T**3 + {k4}*T**4"
            elif modelo_k == "Exponencial":
                a = st.text_input("a", "0,0387")
                b = st.text_input("b", "0,0019")
                k_func = f"{a} * math.exp({b} * T)"

            if st.button("Cadastrar"):
                if nome.strip() and k_func.strip():
                    if nome in df_isolantes['nome'].tolist():
                        st.warning("Já existe um isolante com esse nome.")
                    else:
                        cadastrar_isolante(nome, k_func)
                else:
                    st.error("Nome e fórmula são obrigatórios.")

        elif aba_admin == "Gerenciar Isolantes":
            st.subheader("Isolantes Cadastrados")
            for nome_isolante in df_isolantes['nome']:
                if st.button(f"Excluir {nome_isolante}", key=f"del_{nome_isolante}"):
                    excluir_isolante(nome_isolante)

# --- INTERFACE COM TABS ---
abas = st.tabs(["🔥 Cálculo Térmico Quente", "🧊 Cálculo Térmico Frio", "💰 Cálculo Financeiro"])

# ... O restante do código para as abas permanece muito similar,
# mas adaptado para usar as funções refatoradas e passar a `k_func_str` ...

with abas[0]: # ABA QUENTE
    materiais = df_isolantes['nome'].tolist()
    material_selecionado_nome = st.selectbox("Escolha o material do isolante", materiais, key="mat_quente")
    k_func_str = df_isolantes[df_isolantes['nome'] == material_selecionado_nome]['k_func'].iloc[0]
    
    col1, col2, col3 = st.columns(3)
    Tq = col1.number_input("Temperatura da face quente [°C]", value=250.0)
    To = col2.number_input("Temperatura ambiente [°C]", value=30.0)
    numero_camadas = col3.number_input("Número de camadas", 1, 3, 1)
    
    espessuras = []
    cols = st.columns(numero_camadas)
    for i in range(numero_camadas):
        esp = cols[i].number_input(f"Espessura camada {i+1} [mm]", value=51.0/numero_camadas, key=f"L{i+1}_quente")
        espessuras.append(esp)
    L_total = sum(espessuras) / 1000

    if st.button("Calcular Face Fria"):
        with st.spinner("Calculando..."):
            Tf, q, conv = encontrar_temperatura_face_fria(Tq, To, L_total, k_func_str)
            st.session_state.Tf, st.session_state.convergiu = Tf, conv
            if conv:
                st.subheader("Resultados")
                st.success(f"🌡️ Temperatura da face fria: {Tf:.1f} °C".replace('.', ','))
                if numero_camadas > 1:
                    st.subheader("Temperaturas Intermediárias")
                    T_atual, k_medio = Tq, calcular_k(k_func_str, (Tq + Tf) / 2)
                    for i in range(numero_camadas - 1):
                        resistencia = (espessuras[i] / 1000) / k_medio
                        T_interface = T_atual - q * resistencia
                        st.info(f"Temp. entre camada {i+1} e {i+2}: {T_interface:.1f} °C".replace('.', ','))
                        T_atual = T_interface
            else:
                st.error("❌ O cálculo não convergiu.")
    st.markdown("--- \n> **Observação:** Emissividade de 0.9 e convecção em placa vertical consideradas.")

with abas[1]: # ABA FRIO
    material_frio_nome = st.selectbox("Escolha o material do isolante", df_isolantes['nome'].tolist(), key="mat_frio")
    k_func_str_frio = df_isolantes[df_isolantes['nome'] == material_frio_nome]['k_func'].iloc[0]
    col1, col2, col3 = st.columns(3)
    Ti_frio = col1.number_input("Temperatura interna [°C]", value=5.0, key="Ti_frio")
    Ta_frio = col2.number_input("Temperatura ambiente [°C]", value=25.0, key="Ta_frio")
    UR = col3.number_input("Umidade relativa do ar [%]", 0.0, 100.0, 70.0)

    if st.button("Calcular Espessura Mínima"):
        with st.spinner("Iterando para encontrar espessura..."):
            a, b = 17.27, 237.7
            alfa = ((a * Ta_frio) / (b + Ta_frio)) + math.log(UR / 100.0)
            T_orvalho = (b * alfa) / (a - alfa)
            st.info(f"💧 Temperatura de orvalho: {T_orvalho:.1f} °C")
            espessura_final = None
            for L_teste in [i * 0.001 for i in range(1, 501)]:
                Tf, _, conv = encontrar_temperatura_face_fria(Ti_frio, Ta_frio, L_teste, k_func_str_frio)
                if conv and Tf >= T_orvalho:
                    espessura_final = L_teste
                    break
            if espessura_final:
                st.success(f"✅ Espessura mínima para evitar condensação: {espessura_final * 1000:.1f} mm".replace('.',','))
            else:
                st.error("❌ Não foi possível encontrar uma espessura que evite condensação até 500 mm.")

with abas[2]: # ABA FINANCEIRO
    combustiveis = { "Óleo BPF (kg)": {"v": 3.50, "pc": 11.34, "ef": 0.80}, "Gás Natural (m³)": {"v": 3.60, "pc": 9.65, "ef": 0.75}, "Eletricidade (kWh)": {"v": 0.75, "pc": 1.00, "ef": 1.00} }
    material_fin_nome = st.selectbox("Escolha o material", df_isolantes['nome'].tolist(), key="mat_fin")
    k_func_str_fin = df_isolantes[df_isolantes['nome'] == material_fin_nome]['k_func'].iloc[0]
    
    comb_sel = st.selectbox("Tipo de combustível", list(combustiveis.keys()))
    comb = combustiveis[comb_sel]
    valor_comb = st.number_input("Custo combustível (R$)", value=comb['v'], step=0.01)
    
    col1, col2, col3 = st.columns(3)
    Tq_fin = col1.number_input("Temp. operação [°C]", 250.0, key="Tq_fin")
    To_fin = col2.number_input("Temp. ambiente [°C]", 30.0, key="To_fin")
    esp_fin = col3.number_input("Espessura [mm]", 51.0, key="esp_fin") / 1000
    
    st.subheader("Parâmetros de Retorno")
    col1, col2, col3 = st.columns(3)
    m2 = col1.number_input("Área do projeto (m²)", 1.0, value=10.0)
    h_dia = col2.number_input("Horas/dia", 1.0, 24.0, 8.0)
    d_sem = col3.number_input("Dias/semana", 1, 7, 5)

    if st.button("Calcular Economia"):
        with st.spinner("Calculando economia financeira..."):
            Tf, q_com, conv = encontrar_temperatura_face_fria(Tq_fin, To_fin, esp_fin, k_func_str_fin)
            if conv:
                perda_com_kw = q_com / 1000
                h_sem = calcular_h_conv(Tq_fin, To_fin, 1.0)
                q_rad_sem = e * sigma * ((Tq_fin + 273.15)**4 - (To_fin + 273.15)**4)
                q_conv_sem = h_sem * (Tq_fin - To_fin)
                perda_sem_kw = (q_rad_sem + q_conv_sem) / 1000
                
                economia_kw_m2 = perda_sem_kw - perda_com_kw
                custo_kwh = valor_comb / (comb['pc'] * comb['ef'])
                eco_mensal = economia_kw_m2 * custo_kwh * m2 * h_dia * d_sem * 4.33
                
                st.subheader("Resultados Financeiros")
                m1, m2, m3 = st.columns(3)
                m1.metric("Economia Mensal", f"R$ {eco_mensal:,.2f}".replace(',','X').replace('.',',').replace('X','.'))
                m2.metric("Redução de Perda", f"{(economia_kw_m2 / perda_sem_kw * 100):.1f} %")
                m3.metric("Temp. Superfície", f"{Tf:.1f} °C", delta=f"{(Tf - Tq_fin):.1f} °C vs. sem isolante", delta_color="inverse")

                st.subheader("Comparativo de Perda Térmica (kW/m²)")
                df_perdas = pd.DataFrame({"Situação": ["Sem Isolante", "Com Isolante"], "Perda": [perda_sem_kw, perda_com_kw]}).set_index("Situação")
                st.bar_chart(df_perdas)
            else:
                st.error("Cálculo não convergiu. Verifique os dados.")



