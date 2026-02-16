# Agente Difarda - Prospeccao de Novos Clientes (v2.0 - Reescrito)
# Webhook: /webhook/prospeccao
import pytz
import time
import requests
import json
import hashlib
import os
from datetime import datetime
from flask import Flask, request, jsonify
from openai import OpenAI

# ========== CONFIGURACOES ==========

DIGISAC_URL = "https://difardamodacorporativa.digisac.me"
DIGISAC_TOKEN = "8177228f681aa4c27ee4b5e585fe1eaddb7098a6"
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
ARQUIVO_CONHECIMENTO = "base_conhecimento_prospeccao.json"
ARQUIVO_LOG = "agente_prospeccao_log.txt"

HORA_INICIO = 0
HORA_FIM = 24
TIMEZONE = pytz.timezone('America/Sao_Paulo')
DELAY_RESPOSTA = 15  # segundos

app = Flask(__name__)

# Memoria de conversas por cliente
conversas_clientes = {}  # {contact_id: [{"role": "user/assistant", "content": "..."}]}

# Controle de mensagens processadas
mensagens_processadas = {}

# ========== LOG ==========

def log(mensagem):
    timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [PROSP] {mensagem}"
    print(log_msg)
    try:
        with open(ARQUIVO_LOG, 'a', encoding='utf-8') as f:
            f.write(log_msg + '\n')
    except:
        pass

# ========== BASE DE CONHECIMENTO ==========

def compilar_base_conhecimento():
    """Compila TODA a base em texto para incluir no prompt"""
    try:
        with open(ARQUIVO_CONHECIMENTO, 'r', encoding='utf-8') as f:
            base = json.load(f)
    except:
        log("⚠️ Erro ao carregar base")
        return "Base de conhecimento indisponivel."
    
    registros = base.get('conhecimento', [])
    ativos = [r for r in registros if r.get('ativo', True)]
    
    if not ativos:
        return "Base vazia."
    
    ordem = {'alta': 0, 'media': 1, 'baixa': 2}
    ativos.sort(key=lambda r: ordem.get(r.get('prioridade', 'media'), 1))
    
    texto = ""
    for r in ativos:
        titulo = r.get('titulo', '')
        conteudo = r.get('conteudo', '')
        categoria = r.get('categoria', '').upper()
        texto += f"[{categoria}] {titulo}\n{conteudo}\n\n"
    
    log(f"📚 Base compilada: {len(ativos)} registros")
    return texto

BASE_COMPILADA = compilar_base_conhecimento()
ULTIMA_COMPILACAO = time.time()

def obter_base():
    global BASE_COMPILADA, ULTIMA_COMPILACAO
    if time.time() - ULTIMA_COMPILACAO > 1800:
        BASE_COMPILADA = compilar_base_conhecimento()
        ULTIMA_COMPILACAO = time.time()
    return BASE_COMPILADA

# ========== PROMPT DO SISTEMA ==========

def montar_system_prompt():
    base = obter_base()
    
    return f"""Voce e Manu, consultora comercial da Difarda Moda Corporativa.

QUEM VOCE E:
Voce e uma consultora experiente que conversa com novos contatos pelo WhatsApp. Seu objetivo e entender a necessidade do cliente e, quando fizer sentido, agendar uma reuniao online com os gestores da Difarda. Voce nao e um robo. Voce e uma pessoa real, educada e genuinamente interessada em ajudar.

SUA BASE DE CONHECIMENTO:
{base}

CONTEXTO IMPORTANTE - VOCE ATENDE DOIS TIPOS DE CLIENTES:

TIPO 1: EMPRESAS (B2B)
Empresas que precisam uniformizar equipes. Podem ser farmacias, escolas (a instituicao), oticas, restaurantes, clinicas, industrias ou qualquer outro segmento.
- Pedido minimo: 80 pecas
- Prazo medio: 30 dias uteis
- Objetivo: qualificar o lead e agendar reuniao online com gestores da Difarda
- Informacoes que voce precisa coletar (ao longo da conversa, NAO de uma vez):
  * Segmento de atuacao
  * Porte (numero de funcionarios, lojas ou alunos)
  * Como funciona a operacao (escritorio, campo, exposto ao sol, etc)
  * Como esta o fornecimento atual de uniformes
  * Quantos modelos de uniforme tem no guarda-roupa
  * Prazo medio que recebem uniformes hoje
  * Se e primeiro pedido ou ja tem fornecedor
  * Se ja tem modelos definidos
  * Nome do responsavel
  * Email
  * CNPJ

TIPO 2: PAIS DE ALUNOS (B2C)
Pais que querem comprar uniforme escolar para seus filhos. As escolas parceiras sao:
- Colegio Elelyon (loja: https://colegioelelyon.lojavirtualnuvem.com.br/)
- Colegio Querubins (loja: https://colegioquerubins.lojavirtualnuvem.com.br/)
- Colegio Interativo
- Colegio Alegria do Saber
Para esses clientes:
- NAO peca CNPJ
- NAO fale de pedido minimo ou 80 pecas
- NAO pergunte sobre funcionarios
- Direcione para a loja virtual da escola
- Pergunte: qual escola, nome do aluno, serie, tamanho

COMO IDENTIFICAR O TIPO:
- Se menciona escola parceira + filho/filha/aluno = PAI (B2C)
- Se menciona empresa/rede/funcionarios/lojas = EMPRESA (B2B)
- Se nao esta claro, converse naturalmente ate entender. NAO assuma. NAO pergunte "voce e empresa ou pai?"

COMO VOCE DEVE CONVERSAR:

1. PRIMEIRA MENSAGEM DO CLIENTE:
   - Responda de forma acolhedora e simples
   - "Oi! Tudo bem? Como posso te ajudar?"
   - NAO pergunte segmento, NAO peca dados, apenas acolha

2. ENTENDA ANTES DE PERGUNTAR:
   - Deixe o cliente falar
   - Faca perguntas que surgem naturalmente da conversa
   - Se ele disse "tenho uma otica com 2 lojas", voce ja sabe o segmento E o porte. NAO pergunte de novo.
   - Se ele disse "meu filho estuda no Elelyon", voce ja sabe que e pai. NAO peca CNPJ.

3. UMA PERGUNTA POR VEZ:
   - Nunca faca duas perguntas na mesma mensagem
   - Espere a resposta antes de perguntar outra coisa
   - Reconheca o que o cliente disse antes de fazer nova pergunta

4. FORMATO:
   - Respostas CURTAS: 1 a 3 linhas
   - Sem emojis
   - Sem asteriscos ou negrito
   - Sem menus numerados
   - Tom natural, como conversa entre pessoas
   - Trate por "voce"

5. QUANDO TIVER INFORMACOES SUFICIENTES (B2B):
   - Sugira naturalmente uma reuniao online
   - "Acho que temos uma solucao bem interessante pro seu caso. Que tal a gente marcar uma conversa online pra eu te apresentar nossa equipe?"
   - NAO force. Se o cliente nao quiser, respeite.

6. QUANDO NAO SOUBER:
   - "Vou verificar com minha equipe e te retorno, tudo bem?"
   - NUNCA invente informacoes

7. SE NAO FOR CLIENTE:
   - Fornecedor, parceiro, vendedor = "Vou te direcionar para nossa equipe de gestao, tudo bem?"

CASES DE SUCESSO (use quando fizer sentido, NAO force):
- Farmacias: Atendemos a Rede Farmarcas (Febrafar), marcas Ultrapopular e Maxipopular
- Escolas grandes (500+ alunos): Guarda-roupa completo, bercario ao ensino medio, planejamento anual
- Escolas pequenas (-500 alunos): Venda direta para pais com bonus em material para a escola

ENDERECO:
R. Eduardo Gomes, 2245 - Maranhao Novo, Imperatriz - MA
Google Maps: https://maps.app.goo.gl/g92MZGtzoM2CqVM9A"""

# ========== GERAR RESPOSTA ==========

def gerar_resposta(mensagem, contact_id):
    if not OPENAI_API_KEY:
        return "Desculpe, estou com dificuldades tecnicas. Um atendente ira te ajudar em breve."
    
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        messages = [{"role": "system", "content": montar_system_prompt()}]
        
        # Historico (ultimas 20 mensagens)
        historico = conversas_clientes.get(contact_id, [])
        if historico:
            messages.extend(historico[-20:])
        
        # Mensagem atual
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

@app.route('/webhook/prospeccao', methods=['POST'])
def webhook_prospeccao():
    try:
        dados = request.get_json()
        
        # Verificar evento
        evento = dados.get('event', '')
        if evento != 'message.created':
            return jsonify({"status": "ignored"}), 200
        
        data = dados.get('data', {})
        
        # Se atendente humano assumiu, bot para
        if data.get('ticketUserId'):
            log("⏸️ Atendente humano - Bot parado")
            return jsonify({"status": "human_attending"}), 200
        
        # Ignorar mensagens do bot
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
        keys_antigas = [k for k, v in mensagens_processadas.items() if agora - v > 3600]
        for k in keys_antigas:
            del mensagens_processadas[k]
        
        if message_id in mensagens_processadas:
            return jsonify({"status": "duplicate"}), 200
        mensagens_processadas[message_id] = agora
        
        log(f"💬 [{contact_id}] {mensagem[:60]}")
        
        # Verificar horario
        hora_atual = datetime.now(TIMEZONE).hour
        dia_semana = datetime.now(TIMEZONE).weekday()
        
        if dia_semana >= 5 or not (HORA_INICIO <= hora_atual < HORA_FIM):
            enviar_mensagem_digisac(contact_id,
                "Ola! Nosso horario de atendimento e de segunda a sexta, das 8h as 18h. "
                "Deixe sua mensagem que retornaremos assim que possivel!"
            )
            return jsonify({"status": "outside_hours"}), 200
        
        # Gerar resposta
        resposta = gerar_resposta(mensagem, contact_id)
        
        # Salvar historico
        if contact_id not in conversas_clientes:
            conversas_clientes[contact_id] = []
        conversas_clientes[contact_id].append({"role": "user", "content": mensagem})
        conversas_clientes[contact_id].append({"role": "assistant", "content": resposta})
        
        if len(conversas_clientes[contact_id]) > 30:
            conversas_clientes[contact_id] = conversas_clientes[contact_id][-30:]
        
        # Delay
        log(f"⏳ Aguardando {DELAY_RESPOSTA}s...")
        time.sleep(DELAY_RESPOSTA)
        
        # Enviar
        enviar_mensagem_digisac(contact_id, resposta)
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        log(f"❌ Erro webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "tipo": "prospeccao",
        "timestamp": datetime.now(TIMEZONE).isoformat()
    }), 200

# ========== INICIALIZACAO ==========

if __name__ == '__main__':
    log("🚀 Agente Difarda v2.0 - Prospeccao")
    log(f"📚 Base: {ARQUIVO_CONHECIMENTO}")
    log(f"⏰ Horario: {HORA_INICIO}h-{HORA_FIM}h (seg-sex)")
    log(f"⏳ Delay: {DELAY_RESPOSTA}s")
    
    if OPENAI_API_KEY:
        log("✅ OpenAI configurado")
    else:
        log("⚠️ OpenAI NAO configurado")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
