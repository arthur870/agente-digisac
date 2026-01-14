# Agente Digisac + OpenAI - Base de Conhecimento Versionada
import pytz
import time
import requests
import json
import hashlib
import os
import random
import threading
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

# Memória de conversas por cliente (armazena histórico)
conversas_clientes = {}  # {contact_id: [{"role": "user", "content": "..."}, ...]}

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

def buscar_conhecimento(pergunta, max_resultados=None):
    """Busca conhecimentos relevantes na base - CONSULTA TODA A BASE sem limite de resultados"""
    base = carregar_conhecimento()
    conhecimentos = base.get('conhecimento', [])
    
    # Filtrar apenas ativos
    ativos = [c for c in conhecimentos if c.get('ativo', True)]
    
    if not ativos:
        log("⚠️ Base de conhecimento vazia")
        return []
    
    # Normalizar pergunta
    pergunta_lower = pergunta.lower()
    palavras_pergunta = pergunta_lower.split()
    
    # Identificar se é pergunta comercial (preço, pedido, orçamento, prazo, escola, etc)
    palavras_comerciais = ['preço', 'valor', 'quanto', 'custa', 'pedido', 'orçamento', 
                           'prazo', 'entrega', 'demora', 'peças', 'quantidade', 'minimo',
                           'comprar', 'contratar', 'pagar', 'pagamento', 'escola', 'colégio',
                           'elelyon', 'querubins', 'uniforme escolar', 'loja', 'site']
    eh_comercial = any(palavra in pergunta_lower for palavra in palavras_comerciais)
    
    # Calcular relevância de cada registro
    resultados = []
    regras_comerciais = []  # Separar regras comerciais críticas
    
    for conhecimento in ativos:
        score = 0
        categoria = conhecimento.get('categoria', '')
        conteudo = conhecimento.get('conteudo', '').lower()
        titulo = conhecimento.get('titulo', '').lower()
        
        # Pontuação por palavras-chave (PESO ALTO)
        palavras_chave = conhecimento.get('palavras_chave', [])
        for palavra in palavras_chave:
            if palavra.lower() in pergunta_lower:
                score += 15  # Aumentado de 10 para 15
        
        # Pontuação por categoria
        if categoria.lower() in pergunta_lower:
            score += 5
        
        # Pontuação por título (PESO MÉDIO-ALTO)
        for palavra in palavras_pergunta:
            if len(palavra) > 3 and palavra in titulo:
                score += 12  # Aumentado de 3 para 12
        
        # Pontuação por conteúdo (PESO MÉDIO)
        for palavra in palavras_pergunta:
            if len(palavra) > 3 and palavra in conteudo:
                score += 8  # Novo: busca no conteúdo
        
        # Pontuação por prioridade
        prioridade = conhecimento.get('prioridade', 'media')
        if prioridade == 'alta':
            score += 5  # Aumentado de 2 para 5
        
        # BOOST para regras comerciais críticas
        if categoria == 'comercial':
            score += 20  # Aumentado de 15 para 20
            regras_comerciais.append({
                'conhecimento': conhecimento,
                'score': score,
                'data': conhecimento.get('data_atualizacao')
            })
        
        # Incluir TODOS os registros com score > 0 OU comerciais em perguntas comerciais
        if score > 0 or (eh_comercial and categoria == 'comercial'):
            resultados.append({
                'conhecimento': conhecimento,
                'score': score if score > 0 else 10,  # Mínimo 10 para comerciais
                'data': conhecimento.get('data_atualizacao')
            })
    
    # Se for pergunta comercial, SEMPRE incluir regras comerciais críticas
    if eh_comercial and regras_comerciais:
        # Garantir que pedido mínimo e prazo estejam no resultado
        ids_criticos = ['kb_005', 'kb_006', 'kb_007']  # Pedido mínimo, Prazo, Orçamento
        for id_critico in ids_criticos:
            conhecimento_critico = next((c for c in ativos if c.get('id') == id_critico), None)
            if conhecimento_critico:
                # Verificar se já está nos resultados
                if not any(r['conhecimento'].get('id') == id_critico for r in resultados):
                    resultados.append({
                        'conhecimento': conhecimento_critico,
                        'score': 100,  # Score altíssimo para garantir inclusão
                        'data': conhecimento_critico.get('data_atualizacao')
                    })
    
    # Ordenar por score (relevância) e depois por data (mais recente)
    resultados.sort(key=lambda x: (x['score'], x['data']), reverse=True)
    
    # Retornar TODOS os resultados ordenados (sem limite)
    log(f"🔍 Busca: '{pergunta[:50]}...' → {len(resultados)} resultados encontrados (comercial: {eh_comercial})")
    
    return [r['conhecimento'] for r in resultados]

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

def gerar_resposta_ia(pergunta, contexto_conhecimento, historico_conversa=None):
    """
    Gera resposta usando OpenAI GPT-4
    Usa conhecimento da base como contexto + histórico da conversa
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
- **RESPOSTAS CURTAS**: MÁXIMO 2-3 linhas (40-60 palavras)
- Entre em detalhes apenas quando necessário
- Faça UMA pergunta por vez
- **LEIA O HISTÓRICO**: Você tem acesso às mensagens anteriores do cliente
- **NÃO REPITA**: Se já disse algo, não repita
- **ENTENDA CONTEXTO**: Se cliente já respondeu algo, não pergunte novamente

PERSONALIDADE E TOM:
- Cordial, empático e profissional
- Linguagem natural e humanizada (sem menus numerados)
- Proativo em oferecer ajuda (moderadamente, sem forçar)
- EVITE o uso de emojis
- EVITE asteriscos e negrito
- Seja direto e objetivo
- Trate o cliente por "você"

{contexto_texto}

REGRAS CRÍTICAS (SEMPRE VERIFICAR):
1. PEDIDO MÍNIMO: 80 peças
   - Se cliente mencionar quantidade MENOR que 80, SEMPRE informe: "Nosso pedido mínimo é de 80 peças para garantir viabilidade de produção e melhores condições comerciais."
   - Seja direto e claro sobre essa regra

2. PRAZO DE ENTREGA: 30 dias úteis
   - Sempre mencione quando cliente perguntar sobre prazo ou entrega

3. ORÇAMENTO: Precisa de modelo + quantidade + CNPJ
   - Se cliente pedir orçamento, pergunte essas 3 informações

4. HORÁRIO: Segunda a Sexta, 8h às 18h
   - Fora desse horário, apenas informe que empresa está fechada

EXEMPLOS DE RESPOSTAS CORRETAS:

Cliente: "Quero fazer 50 camisas"
Você: "Nosso pedido mínimo é de 80 peças para garantir viabilidade de produção e melhores condições comerciais. Você consegue aumentar a quantidade?"

Cliente: "Quanto custa?"
Você: "Para preparar um orçamento personalizado, preciso saber: qual modelo de uniforme você procura, a quantidade de peças e o CNPJ da empresa. Pode me passar essas informações?"

Cliente: "Quanto tempo demora?"
Você: "O prazo médio de entrega é de 30 dias úteis após aprovação do pedido e confirmação de pagamento."

EXEMPLOS DE USO DE CONTEXTO (IMPORTANTE!):

Cliente: "Quero 15 camisetas e 28 calças"
Você: "Nosso pedido mínimo é de 80 peças. Você consegue aumentar a quantidade?"

Cliente (próxima mensagem): "Pode ser 40 camisetas e 40 calças"
Você: "Perfeito! 80 peças atende nosso mínimo. Qual modelo de uniforme você procura?"
❌ NÃO REPITA: "Nosso pedido mínimo é de 80 peças..." (cliente já aumentou!)

Cliente: "Quero orçamento"
Você: "Para preparar o orçamento, preciso do modelo, quantidade e CNPJ. Pode me passar?"

Cliente (próxima mensagem): "Camisa polo, 100 peças"
Você: "Ótimo! Só falta o CNPJ da empresa para eu preparar o orçamento."
❌ NÃO REPITA: "Preciso do modelo, quantidade e CNPJ" (cliente já passou 2 de 3!)

QUANDO NÃO SOUBER:
- NUNCA invente preços, prazos específicos ou disponibilidade
- Responda: "Ótima pergunta! Deixa eu verificar com minha equipe e já te retorno, ok?"

IMPORTANTE: 
- Use APENAS as informações da base de conhecimento acima
- SEMPRE verifique se a pergunta envolve quantidade de peças e compare com o mínimo de 80
- Seja assertivo e direto ao informar regras comerciais
- Não peça mais informações se a base já tem a resposta"""

        # Montar mensagens com histórico
        messages = [{"role": "system", "content": system_prompt}]
        
        # Adicionar histórico de conversa (se existir)
        if historico_conversa:
            # Limitar a últimas 10 mensagens para não exceder tokens
            messages.extend(historico_conversa[-10:])
        
        # Adicionar pergunta atual
        messages.append({"role": "user", "content": pergunta})
        
        # Chamar OpenAI
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Modelo mais acessível e rápido
            messages=messages,
            temperature=0.7,
            max_tokens=200  # Reduzido para respostas mais curtas
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
@app.route('/webhook/digisac', methods=['POST'])  # Rota alternativa
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
        
        # 2. Obter histórico de conversa do cliente
        if contact_id not in conversas_clientes:
            conversas_clientes[contact_id] = []
        
        historico = conversas_clientes[contact_id]
        
        # 3. Gerar resposta com IA (incluindo histórico)
        resposta = gerar_resposta_ia(mensagem_texto, conhecimentos, historico)
        # DELAY de 15 segundos para parecer mais humano
        log("⏳ Aguardando 15 segundos para parecer mais humano...")
        time.sleep(15)
        # 4. Atualizar histórico com mensagem do cliente e resposta do bot
        conversas_clientes[contact_id].append({"role": "user", "content": mensagem_texto})
        conversas_clientes[contact_id].append({"role": "assistant", "content": resposta})
        
        # Limitar histórico a últimas 20 mensagens (10 pares)
        if len(conversas_clientes[contact_id]) > 20:
            conversas_clientes[contact_id] = conversas_clientes[contact_id][-20:]
        
        # 5. Registrar uso dos conhecimentos
        for conhecimento in conhecimentos:
            registrar_uso_conhecimento(conhecimento.get('id'))
        
        # 6. Enviar resposta imediatamente
        log(f"📤 Enviando resposta...")
        if enviar_mensagem_digisac(contact_id, resposta):
            log(f"✅ Resposta enviada com sucesso")
        else:
            log(f"❌ Erro ao enviar resposta")
            return jsonify({"status": "send_failed"}), 500
        
        # 7. Marcar como processada
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
