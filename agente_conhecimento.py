# Agente Digisac + OpenAI - Base de Conhecimento Versionada
import pytz
import time
import requests
import json
import hashlib
import os
from datetime import datetime
from flask import Flask, request, jsonify
from openai import OpenAI

# ========== CONFIGURAÇÕES ==========

# Digisac
DIGISAC_URL = "https://difardamodacorporativa.digisac.me"
DIGISAC_TOKEN = "8177228f681aa4c27ee4b5e585fe1eaddb7098a6"

# OpenAI - Usa variável de ambiente (configurar no Render)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')

# Arquivos
ARQUIVO_CONHECIMENTO = "base_conhecimento.json"
ARQUIVO_LOG = "agente_log.txt"

# Controle de mensagens processadas
mensagens_processadas = {}  # {message_id: timestamp}

# Horário de funcionamento (Brasília GMT-3)
# Segunda a Sexta, 8h às 18h
HORA_INICIO = 8
HORA_FIM = 18
TIMEZONE = pytz.timezone('America/Sao_Paulo')

app = Flask(__name__)

# ========== FUNÇÕES DE LOG ==========

def log(mensagem):
    """Registra mensagem no log com timestamp"""
    timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {mensagem}"
    print(log_msg)
    
    try:
        with open(ARQUIVO_LOG, 'a', encoding='utf-8') as f:
            f.write(log_msg + '\n')
    except Exception as e:
        print(f"Erro ao escrever log: {e}")

# ========== FUNÇÕES DE CONHECIMENTO ==========

def carregar_conhecimento():
    """Carrega base de conhecimento do arquivo JSON"""
    try:
        with open(ARQUIVO_CONHECIMENTO, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        log("⚠️ Arquivo de conhecimento não encontrado")
        return {"conhecimento": [], "estatisticas": {}}
    except Exception as e:
        log(f"❌ Erro ao carregar conhecimento: {e}")
        return {"conhecimento": [], "estatisticas": {}}

def salvar_conhecimento(dados):
    """Salva base de conhecimento no arquivo JSON"""
    try:
        dados['ultima_atualizacao'] = datetime.now(TIMEZONE).isoformat()
        with open(ARQUIVO_CONHECIMENTO, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
        log("✅ Base de conhecimento salva")
        return True
    except Exception as e:
        log(f"❌ Erro ao salvar conhecimento: {e}")
        return False

def buscar_conhecimento(pergunta, max_resultados=3):
    """
    Busca conhecimento relevante na base
    Retorna registros ordenados por relevância e data
    """
    base = carregar_conhecimento()
    conhecimentos = base.get('conhecimento', [])
    
    # Filtrar apenas registros ativos
    ativos = [k for k in conhecimentos if k.get('ativo', True)]
    
    if not ativos:
        log("⚠️ Nenhum conhecimento ativo encontrado")
        return []
    
    # Extrair palavras-chave da pergunta
    pergunta_lower = pergunta.lower()
    palavras_pergunta = set(pergunta_lower.split())
    
    # Calcular relevância de cada registro
    resultados = []
    for conhecimento in ativos:
        score = 0
        
        # Pontuação por palavras-chave
        palavras_chave = conhecimento.get('palavras_chave', [])
        for palavra in palavras_chave:
            if palavra.lower() in pergunta_lower:
                score += 10
        
        # Pontuação por categoria
        categoria = conhecimento.get('categoria', '')
        if categoria.lower() in pergunta_lower:
            score += 5
        
        # Pontuação por título
        titulo = conhecimento.get('titulo', '')
        if any(palavra in titulo.lower() for palavra in palavras_pergunta):
            score += 3
        
        # Pontuação por prioridade
        prioridade = conhecimento.get('prioridade', 'media')
        if prioridade == 'alta':
            score += 2
        
        if score > 0:
            resultados.append({
                'conhecimento': conhecimento,
                'score': score,
                'data': conhecimento.get('data_atualizacao')
            })
    
    # Ordenar por score (relevância) e depois por data (mais recente)
    resultados.sort(key=lambda x: (x['score'], x['data']), reverse=True)
    
    # Retornar top N resultados
    top_resultados = resultados[:max_resultados]
    
    log(f"🔍 Busca: '{pergunta[:50]}...' → {len(top_resultados)} resultados")
    
    return [r['conhecimento'] for r in top_resultados]

def registrar_uso_conhecimento(conhecimento_id):
    """Registra que um conhecimento foi usado"""
    base = carregar_conhecimento()
    
    # Atualizar estatísticas
    if 'uso_conhecimento' not in base:
        base['uso_conhecimento'] = {}
    
    if conhecimento_id not in base['uso_conhecimento']:
        base['uso_conhecimento'][conhecimento_id] = {
            'total_usos': 0,
            'ultimo_uso': None
        }
    
    base['uso_conhecimento'][conhecimento_id]['total_usos'] += 1
    base['uso_conhecimento'][conhecimento_id]['ultimo_uso'] = datetime.now(TIMEZONE).isoformat()
    
    salvar_conhecimento(base)

# ========== FUNÇÕES DE IA (OPENAI) ==========

def gerar_resposta_ia(pergunta, contexto_conhecimento):
    """
    Gera resposta usando OpenAI GPT-4
    Usa conhecimento da base como contexto
    """
    if not OPENAI_API_KEY or OPENAI_API_KEY == "":
        log("⚠️ OpenAI API Key não configurada")
        return "Desculpe, estou com dificuldades técnicas no momento. Um atendente humano irá ajudá-lo em breve."
    
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Montar contexto a partir do conhecimento encontrado
        contexto_texto = ""
        if contexto_conhecimento:
            contexto_texto = "INFORMAÇÕES RELEVANTES DA BASE DE CONHECIMENTO:\n\n"
            for i, conhecimento in enumerate(contexto_conhecimento, 1):
                titulo = conhecimento.get('titulo', 'Sem título')
                conteudo = conhecimento.get('conteudo', '')
                contexto_texto += f"{i}. {titulo}\n{conteudo}\n\n"
        
        # Prompt do sistema
        system_prompt = f"""Você é Manu, assistente da Difarda Moda Corporativa, especializada em moda corporativa.

COMPORTAMENTO:
- Não precisa se identificar como virtual, apenas assistente
- Priorize respostas curtas e objetivas
- Entre em detalhes apenas quando necessário
- Faça uma pergunta por vez

PERSONALIDADE E TOM:
- Cordial, empático e profissional
- Linguagem natural e humanizada (sem menus numerados)
- Proativo em oferecer ajuda (moderadamente, sem forçar)
- EVITE o uso de emojis
- EVITE asteriscos e negrito
- Após primeiras mensagens, seja mais objetivo mas gentil
- Trate o cliente por "você"

{contexto_texto}

QUANDO NÃO SOUBER:
- NUNCA invente preços, prazos ou disponibilidade
- Responda: "Ótima pergunta! Deixa eu verificar com minha equipe e já te retorno, ok?"

IMPORTANTE: Use APENAS as informações da base de conhecimento acima. Se não tiver informação suficiente, peça para verificar com a equipe."""

        # Chamar OpenAI
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": pergunta}
            ],
            temperature=0.7,
            max_tokens=300
        )
        
        resposta = response.choices[0].message.content
        log(f"🤖 Resposta IA gerada: {resposta[:100]}...")
        
        return resposta
        
    except Exception as e:
        log(f"❌ Erro ao gerar resposta IA: {e}")
        return "Desculpe, estou com dificuldades no momento. Vou transferir você para um atendente humano."

# ========== FUNÇÕES DE HORÁRIO ==========

def verificar_horario_funcionamento():
    """Verifica se está dentro do horário de funcionamento"""
    agora = datetime.now(TIMEZONE)
    hora_atual = agora.hour
    
    dentro_horario = HORA_INICIO <= hora_atual < HORA_FIM
    
    if dentro_horario:
        return True, f"Dentro do horário ({hora_atual}h)"
    else:
        return False, f"Fora do horário ({hora_atual}h)"

def mensagem_fora_horario():
    """Retorna mensagem para horário fora do expediente"""
    return f"""Olá!

Nosso horário de atendimento é de segunda a sexta-feira, das {HORA_INICIO}h às {HORA_FIM}h.

Deixe sua mensagem que retornaremos assim que possível!"""

# ========== FUNÇÕES DIGISAC ==========

def enviar_mensagem_digisac(contact_id, texto):
    """Envia mensagem via API Digisac"""
    log(f"📤 Digisac: '{texto[:50]}...' (contact: {contact_id})")
    
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
            log("✅ Mensagem enviada Digisac")
            return True
        else:
            log(f"❌ Erro Digisac: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Erro ao enviar Digisac: {e}")
        return False

def verificar_atendente_humano(dados):
    """Verifica se há atendente humano no chamado"""
    data = dados.get('data', {})
    ticket_user_id = data.get('ticketUserId')
    return ticket_user_id is not None

# ========== WEBHOOK ENDPOINT ==========

@app.route('/webhook', methods=['POST'])
@app.route('/webhook/digisac', methods=['POST'])
def webhook():
    """Recebe mensagens do Digisac via webhook"""
    try:
        dados = request.get_json()
        log(f"📥 Webhook recebido")
        
        # Verificar área (Fila vs Chat)
        data = dados.get('data', {})
        ticket_user_id = data.get('ticketUserId')
        
        if ticket_user_id:
            log(f"⏸️ Chamado no Chat (atendente: {ticket_user_id}) - Bot não atua")
            return jsonify({"status": "chat_area"}), 200
        
        log(f"✅ Chamado na Fila/Contatos - Bot atua")
        
        # Verificar tipo de evento
        evento = dados.get('event', '')
        if evento != 'message.created':
            log(f"⏭️ Evento '{evento}' ignorado")
            return jsonify({"status": "ignored"}), 200
        
        # Extrair informações
        mensagem_texto = data.get('text', '')
        contact_id = data.get('contactId', '')
        is_from_me = data.get('isFromMe', False)
        is_from_bot = data.get('isFromBot', False)
        
        # Ignorar mensagens do bot/próprias
        if is_from_me or is_from_bot:
            log("⏭️ Mensagem do bot/própria, ignorando")
            return jsonify({"status": "ignored"}), 200
        
        # Verificar mensagem vazia
        if not mensagem_texto or mensagem_texto.strip() == "":
            log("⏭️ Mensagem vazia, ignorando")
            return jsonify({"status": "empty_message"}), 200
        
        log(f"💬 Mensagem do cliente: '{mensagem_texto[:50]}...'")
        
        # Extrair ID único da mensagem
        message_id = data.get('id')
        if not message_id:
            message_id = hashlib.md5(f"{contact_id}_{mensagem_texto}_{data.get('timestamp', '')}".encode()).hexdigest()
        
        # Verificar se já foi processada
        if message_id in mensagens_processadas:
            log(f"⏭️ Mensagem já processada (ID: {message_id})")
            return jsonify({"status": "already_processed"}), 200
        
        # Limpar mensagens antigas (mais de 1 hora)
        agora = time.time()
        mensagens_processadas.update({mid: ts for mid, ts in mensagens_processadas.items() if agora - ts < 3600})
        
        # Verificar horário de funcionamento
        dentro_horario, msg_horario = verificar_horario_funcionamento()
        
        if not dentro_horario:
            log(f"⏰ Fora do horário - {msg_horario}")
            resposta_horario = mensagem_fora_horario()
            enviar_mensagem_digisac(contact_id, resposta_horario)
            mensagens_processadas[message_id] = time.time()
            return jsonify({"status": "fora_horario"}), 200
        
        log(f"✅ Dentro do horário - {msg_horario}")
        
        # Verificar dados mínimos
        if not contact_id:
            log("⚠️ Contact ID ausente")
            return jsonify({"status": "incomplete_data"}), 400
        
        # ===== PROCESSAR MENSAGEM COM IA =====
        
        # 1. Buscar conhecimento relevante
        conhecimentos = buscar_conhecimento(mensagem_texto)
        
        # 2. Gerar resposta com IA
        resposta = gerar_resposta_ia(mensagem_texto, conhecimentos)
        
        # 3. Registrar uso dos conhecimentos
        for conhecimento in conhecimentos:
            registrar_uso_conhecimento(conhecimento.get('id'))
        
        # 4. Enviar resposta
        if enviar_mensagem_digisac(contact_id, resposta):
            log(f"✅ Resposta enviada")
        else:
            return jsonify({"status": "send_failed"}), 500
        
        # 5. Marcar como processada
        mensagens_processadas[message_id] = time.time()
        
        log(f"✅ Processamento completo")
        return jsonify({"status": "success"}), 200
            
    except Exception as e:
        log(f"❌ Erro no webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Endpoint de health check"""
    return jsonify({
        "status": "online",
        "timestamp": datetime.now(TIMEZONE).isoformat(),
        "openai_configurado": bool(OPENAI_API_KEY and OPENAI_API_KEY != "")
    }), 200

if __name__ == '__main__':
    log("🚀 Iniciando Agente Difarda com Base de Conhecimento")
    log(f"📚 Arquivo de conhecimento: {ARQUIVO_CONHECIMENTO}")
    
    # Verificar se base de conhecimento existe
    base = carregar_conhecimento()
    total_registros = len(base.get('conhecimento', []))
    log(f"✅ Base carregada: {total_registros} registros")
    
    # Verificar OpenAI
    if OPENAI_API_KEY and OPENAI_API_KEY != "":
        log("✅ OpenAI configurado")
    else:
        log("⚠️ OpenAI não configurado - configure OPENAI_API_KEY")
    
    app.run(host='0.0.0.0', port=5000)
