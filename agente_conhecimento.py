# Agente Difarda - Atendimento ao Cliente (v3.0 - Com Buffer)
# Webhook: /webhook/digisac
import pytz
import time
import requests
import json
import hashlib
import os
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from openai import OpenAI

# ========== CONFIGURAÇÕES ==========

DIGISAC_URL = "https://difardamodacorporativa.digisac.me"
DIGISAC_TOKEN = "8177228f681aa4c27ee4b5e585fe1eaddb7098a6"
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
ARQUIVO_CONHECIMENTO = "base_conhecimento.json"
ARQUIVO_LOG = "agente_conhecimento_log.txt"

HORA_INICIO = 0
HORA_FIM = 24
TIMEZONE = pytz.timezone('America/Sao_Paulo')
BUFFER_TIMEOUT = 25  # segundos para aguardar mais mensagens antes de responder

app = Flask(__name__)

# Memória de conversas por cliente
conversas_clientes = {}  # {contact_id: [{"role": "user/assistant", "content": "..."}]}

# Controle de mensagens processadas (evita duplicatas)
mensagens_processadas = {}  # {message_id: timestamp}

# ========== SISTEMA DE BUFFER ==========
# Acumula mensagens do mesmo cliente antes de processar
# Quando chega uma mensagem, aguarda BUFFER_TIMEOUT segundos
# Se chegarem mais mensagens nesse periodo, o timer reinicia
# So processa quando o cliente parar de digitar

buffer_mensagens = {}  # {contact_id: {"mensagens": [...], "timer": Timer}}
buffer_lock = threading.Lock()

def adicionar_ao_buffer(contact_id, mensagem):
    """Adiciona mensagem ao buffer do cliente e reinicia o timer"""
    with buffer_lock:
        if contact_id not in buffer_mensagens:
            buffer_mensagens[contact_id] = {
                "mensagens": [],
                "timer": None
            }
        
        buf = buffer_mensagens[contact_id]
        
        # Cancelar timer anterior (cliente ainda esta digitando)
        if buf["timer"] is not None:
            buf["timer"].cancel()
            log(f"🔄 [{contact_id}] Timer reiniciado (nova mensagem recebida)")
        
        # Adicionar mensagem ao buffer
        buf["mensagens"].append(mensagem)
        log(f"📥 [{contact_id}] Buffer: {len(buf['mensagens'])} mensagem(ns) acumulada(s)")
        
        # Criar novo timer
        buf["timer"] = threading.Timer(BUFFER_TIMEOUT, processar_buffer, args=[contact_id])
        buf["timer"].start()
        log(f"⏳ [{contact_id}] Aguardando {BUFFER_TIMEOUT}s por mais mensagens...")

def processar_buffer(contact_id):
    """Processa todas as mensagens acumuladas do cliente como uma unica mensagem"""
    with buffer_lock:
        if contact_id not in buffer_mensagens:
            return
        
        buf = buffer_mensagens[contact_id]
        mensagens = buf["mensagens"].copy()
        buf["mensagens"] = []
        buf["timer"] = None
    
    if not mensagens:
        return
    
    # Juntar todas as mensagens em uma so
    if len(mensagens) == 1:
        mensagem_completa = mensagens[0]
        log(f"💬 [{contact_id}] Processando 1 mensagem: {mensagem_completa[:60]}")
    else:
        mensagem_completa = " | ".join(mensagens)
        log(f"💬 [{contact_id}] Processando {len(mensagens)} mensagens agrupadas: {mensagem_completa[:80]}")
    
    # Gerar resposta
    resposta = gerar_resposta(mensagem_completa, contact_id)
    
    # Salvar historico
    if contact_id not in conversas_clientes:
        conversas_clientes[contact_id] = []
    conversas_clientes[contact_id].append({"role": "user", "content": mensagem_completa})
    conversas_clientes[contact_id].append({"role": "assistant", "content": resposta})
    
    if len(conversas_clientes[contact_id]) > 30:
        conversas_clientes[contact_id] = conversas_clientes[contact_id][-30:]
    
    # Enviar resposta
    enviar_mensagem_digisac(contact_id, resposta)

# ========== LOG ==========

def log(mensagem):
    timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {mensagem}"
    print(log_msg)
    try:
        with open(ARQUIVO_LOG, 'a', encoding='utf-8') as f:
            f.write(log_msg + '\n')
    except:
        pass

# ========== BASE DE CONHECIMENTO ==========

def compilar_base_conhecimento():
    """
    Compila TODA a base de conhecimento em um texto estruturado.
    Isso é incluído no system prompt para que a IA SEMPRE tenha acesso completo.
    """
    try:
        with open(ARQUIVO_CONHECIMENTO, 'r', encoding='utf-8') as f:
            base = json.load(f)
    except:
        log("⚠️ Erro ao carregar base de conhecimento")
        return "Base de conhecimento indisponível."
    
    registros = base.get('conhecimento', [])
    ativos = [r for r in registros if r.get('ativo', True)]
    
    if not ativos:
        return "Base de conhecimento vazia."
    
    # Ordenar por prioridade (alta primeiro) e data de atualização (mais recente primeiro)
    ordem_prioridade = {'alta': 0, 'media': 1, 'baixa': 2}
    ativos.sort(key=lambda r: (
        ordem_prioridade.get(r.get('prioridade', 'media'), 1),
        r.get('data_atualizacao', '')
    ), reverse=False)
    
    texto = ""
    for r in ativos:
        titulo = r.get('titulo', '')
        conteudo = r.get('conteudo', '')
        categoria = r.get('categoria', '').upper()
        atualizado = r.get('data_atualizacao', '')[:10]
        texto += f"[{categoria}] {titulo}\n{conteudo}\n(Atualizado: {atualizado})\n\n"
    
    log(f"📚 Base compilada: {len(ativos)} registros")
    return texto

# Compilar base uma vez na inicialização (recarrega a cada 30 min)
BASE_COMPILADA = compilar_base_conhecimento()
ULTIMA_COMPILACAO = time.time()

def obter_base():
    """Retorna base compilada, recarregando se necessário"""
    global BASE_COMPILADA, ULTIMA_COMPILACAO
    if time.time() - ULTIMA_COMPILACAO > 1800:  # 30 minutos
        BASE_COMPILADA = compilar_base_conhecimento()
        ULTIMA_COMPILACAO = time.time()
    return BASE_COMPILADA

# ========== PROMPT DO SISTEMA ==========

def montar_system_prompt():
    """Monta o prompt do sistema com toda a base de conhecimento"""
    base = obter_base()
    
    return f"""Voce e Manu, assistente de atendimento da Difarda Moda Corporativa.

QUEM VOCE E:
Voce e uma assistente humana e acolhedora que atende clientes pelo WhatsApp. Voce conhece profundamente a empresa e seus produtos. Voce nao e um robo, voce e uma pessoa real da equipe Difarda.

SUA BASE DE CONHECIMENTO COMPLETA:
{base}

COMO VOCE DEVE SE COMPORTAR:

1. ENTENDA PRIMEIRO, RESPONDA DEPOIS
   - Leia TODA a conversa anterior antes de responder
   - Identifique o que o cliente quer ANTES de fazer perguntas
   - Se o cliente ja disse algo, NAO pergunte de novo
   - Se o cliente menciona uma escola (Elelyon, Querubins, Interativo, Alegria do Saber), ele provavelmente e pai/mae buscando uniforme escolar
   - Se o cliente fala de empresa, funcionarios, rede, lojas, ele e um cliente corporativo (B2B)

2. DOIS TIPOS DE CLIENTES (identifique naturalmente):

   PAIS DE ALUNOS (escolas parceiras):
   - Escolas: Colegio Elelyon, Colegio Querubins, Colegio Interativo, Colegio Alegria do Saber
   - NAO peca CNPJ, NAO fale de pedido minimo, NAO fale de 80 pecas
   - Direcione para a loja virtual da escola quando disponivel
   - Pergunte: qual escola, nome do aluno, serie, tamanho
   - Seja acolhedora e pratica

   EMPRESAS (B2B):
   - Pedido minimo: 80 pecas
   - Prazo de entrega: 30 dias uteis
   - Para orcamento: modelo + quantidade + CNPJ
   - Seja consultiva e profissional

3. FORMATO DAS RESPOSTAS:
   - Respostas CURTAS: 1 a 3 linhas no maximo
   - UMA pergunta por vez
   - Sem emojis
   - Sem asteriscos ou negrito
   - Sem menus numerados (1, 2, 3)
   - Tom natural e humano, como se estivesse conversando pessoalmente
   - Trate o cliente por "voce"

4. REGRAS IMPORTANTES:
   - Se nao souber algo: "Vou verificar com minha equipe e te retorno, tudo bem?"
   - NUNCA invente precos, prazos especificos ou disponibilidade
   - Se o assunto NAO for sobre uniformes/clientes (fornecedor, parceiro, vendedor): informe educadamente que vai transferir para a equipe de gestao
   - NAO sugira ao cliente ligar para o numero que ele ja esta conversando
   - Se um atendente humano assumir, pare de responder

5. CONTEXTO DA CONVERSA:
   - Voce tem acesso ao historico completo da conversa
   - USE o historico para dar continuidade natural
   - Se o cliente ja informou algo (nome, escola, quantidade), reconheca e avance
   - NAO repita perguntas ja respondidas

6. MENSAGENS AGRUPADAS:
   - As vezes o cliente envia varias mensagens curtas seguidas (separadas por " | ")
   - Trate como UMA UNICA mensagem
   - Leia tudo antes de responder e de UMA UNICA resposta que enderece todos os pontos

ENDERECO DA EMPRESA:
R. Eduardo Gomes, 2245 - Maranhao Novo, Imperatriz - MA
Google Maps: https://maps.app.goo.gl/g92MZGtzoM2CqVM9A"""

# ========== GERAR RESPOSTA ==========

def gerar_resposta(mensagem, contact_id):
    """Gera resposta usando OpenAI com contexto completo"""
    if not OPENAI_API_KEY:
        return "Desculpe, estou com dificuldades tecnicas. Vou transferir voce para um atendente."
    
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Montar mensagens: system + historico + mensagem atual
        messages = [{"role": "system", "content": montar_system_prompt()}]
        
        # Adicionar historico (ultimas 20 mensagens)
        historico = conversas_clientes.get(contact_id, [])
        if historico:
            messages.extend(historico[-20:])
        
        # Adicionar mensagem atual
        messages.append({"role": "user", "content": mensagem})
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.4,
            max_tokens=350
        )
        
        resposta = response.choices[0].message.content
        log(f"🤖 Resposta: {resposta[:80]}...")
        return resposta
        
    except Exception as e:
        log(f"❌ Erro OpenAI: {e}")
        return "Desculpe, estou com dificuldades no momento. Vou verificar com minha equipe e te retorno."

# ========== DIGISAC ==========

def enviar_mensagem_digisac(contact_id, texto):
    """Envia mensagem via API Digisac"""
    url = f"{DIGISAC_URL}/api/v1/messages"
    headers = {
        "Authorization": f"Bearer {DIGISAC_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "text": texto,
        "type": "chat",
        "contactId": contact_id,
        "origin": "bot"
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code in [200, 201]:
            log(f"✅ Enviado para {contact_id}")
            return True
        else:
            log(f"❌ Erro Digisac: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Erro envio: {e}")
        return False

# ========== WEBHOOK ==========

@app.route('/webhook', methods=['POST'])
@app.route('/webhook/digisac', methods=['POST'])
def webhook():
    """Recebe mensagens do Digisac"""
    try:
        dados = request.get_json()
        
        # Verificar evento
        evento = dados.get('event', '')
        if evento != 'message.created':
            return jsonify({"status": "ignored"}), 200
        
        data = dados.get('data', {})
        
        # Se atendente humano assumiu, bot nao atua
        if data.get('ticketUserId'):
            log("⏸️ Atendente humano presente - Bot parado")
            return jsonify({"status": "human_attending"}), 200
        
        # Ignorar mensagens do bot/proprias
        if data.get('isFromMe') or data.get('isFromBot'):
            return jsonify({"status": "ignored"}), 200
        
        # Extrair dados
        mensagem = data.get('text', '').strip()
        contact_id = data.get('contactId', '')
        
        if not mensagem or not contact_id:
            return jsonify({"status": "empty"}), 200
        
        # Deduplicacao
        message_id = data.get('id') or hashlib.md5(
            f"{contact_id}_{mensagem}_{data.get('timestamp', '')}".encode()
        ).hexdigest()
        
        agora = time.time()
        # Limpar processadas antigas (1h)
        keys_antigas = [k for k, v in mensagens_processadas.items() if agora - v > 3600]
        for k in keys_antigas:
            del mensagens_processadas[k]
        
        if message_id in mensagens_processadas:
            return jsonify({"status": "duplicate"}), 200
        mensagens_processadas[message_id] = agora
        
        log(f"📨 [{contact_id}] Recebido: {mensagem[:60]}")
        
        # Verificar horario
        hora_atual = datetime.now(TIMEZONE).hour
        dia_semana = datetime.now(TIMEZONE).weekday()
        
        if dia_semana >= 5 or not (HORA_INICIO <= hora_atual < HORA_FIM):
            enviar_mensagem_digisac(contact_id,
                "Ola! Nosso horario de atendimento e de segunda a sexta, das 8h as 18h. "
                "Deixe sua mensagem que retornaremos assim que possivel!"
            )
            return jsonify({"status": "outside_hours"}), 200
        
        # Adicionar ao buffer (NAO processa imediatamente)
        # O buffer aguarda BUFFER_TIMEOUT segundos por mais mensagens
        # Se o cliente enviar mais mensagens, o timer reinicia
        # Quando o cliente parar de digitar, todas as mensagens sao processadas juntas
        adicionar_ao_buffer(contact_id, mensagem)
        
        return jsonify({"status": "buffered"}), 200
        
    except Exception as e:
        log(f"❌ Erro webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "tipo": "atendimento",
        "versao": "3.0",
        "buffer_timeout": BUFFER_TIMEOUT,
        "timestamp": datetime.now(TIMEZONE).isoformat()
    }), 200

# ========== INICIALIZACAO ==========

if __name__ == '__main__':
    log("🚀 Agente Difarda v3.0 - Atendimento (com Buffer)")
    log(f"📚 Base: {ARQUIVO_CONHECIMENTO}")
    log(f"⏰ Horario: {HORA_INICIO}h-{HORA_FIM}h (seg-sex)")
    log(f"⏳ Buffer: {BUFFER_TIMEOUT}s (aguarda cliente parar de digitar)")
    
    if OPENAI_API_KEY:
        log("✅ OpenAI configurado")
    else:
        log("⚠️ OpenAI NAO configurado")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
