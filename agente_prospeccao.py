# Agente Digisac + OpenAI - PROSPECÇÃO (Farmácias e Escolas)
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

# Número de telefone específico para prospecção (CONFIGURAR)
TELEFONE_PROSPECCAO = os.getenv('TELEFONE_PROSPECCAO', '')  # Ex: "5599988206465"

# OpenAI - Usa variável de ambiente (configurar no Render)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')

# Arquivos
ARQUIVO_CONHECIMENTO = "base_conhecimento_prospeccao.json"
ARQUIVO_LOG = "agente_prospeccao_log.txt"
ARQUIVO_LEADS = "leads_qualificados.json"

# Controle de mensagens processadas
mensagens_processadas = {}  # {message_id: timestamp}

# Horário de funcionamento (Brasília GMT-3)
# Segunda a Sexta, 8h às 18h
HORA_INICIO = 8
HORA_FIM = 18
TIMEZONE = pytz.timezone('America/Sao_Paulo')

app = Flask(__name__)

# Memória de conversas por cliente (armazena histórico + dados coletados)
conversas_clientes = {}  # {contact_id: {"historico": [...], "dados": {...}}}

# ========== FUNÇÕES DE LOG ==========

def log(mensagem):
    """Registra mensagem no log com timestamp"""
    timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] [PROSPECÇÃO] {mensagem}"
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
    
    # Identificar tipo de lead (farmácia ou escola)
    palavras_farmacias = ['farmácia', 'farmacia', 'drogaria', 'farmarcas', 'ultrapopular', 'maxipopular']
    palavras_escolas = ['escola', 'colégio', 'colegio', 'alunos', 'educação', 'ensino']
    
    eh_farmacia = any(palavra in pergunta_lower for palavra in palavras_farmacias)
    eh_escola = any(palavra in pergunta_lower for palavra in palavras_escolas)
    
    # Calcular relevância de cada registro
    resultados = []
    
    for conhecimento in ativos:
        score = 0
        categoria = conhecimento.get('categoria', '')
        conteudo = conhecimento.get('conteudo', '').lower()
        titulo = conhecimento.get('titulo', '').lower()
        
        # Pontuação por palavras-chave (PESO ALTO)
        palavras_chave = conhecimento.get('palavras_chave', [])
        for palavra in palavras_chave:
            if palavra.lower() in pergunta_lower:
                score += 15
        
        # Pontuação por categoria
        if categoria.lower() in pergunta_lower:
            score += 5
        
        # Pontuação por título (PESO MÉDIO-ALTO)
        for palavra in palavras_pergunta:
            if len(palavra) > 3 and palavra in titulo:
                score += 12
        
        # Pontuação por conteúdo (PESO MÉDIO)
        for palavra in palavras_pergunta:
            if len(palavra) > 3 and palavra in conteudo:
                score += 8
        
        # Pontuação por prioridade
        prioridade = conhecimento.get('prioridade', 'media')
        if prioridade == 'alta':
            score += 5
        
        # BOOST para categoria específica do lead
        if eh_farmacia and categoria == 'farmacias':
            score += 25
        if eh_escola and categoria == 'escolas':
            score += 25
        
        # SEMPRE incluir registros de qualificação e processo
        if categoria in ['qualificacao', 'processo']:
            score += 15
        
        # Incluir TODOS os registros com score > 0
        if score > 0:
            resultados.append({
                'conhecimento': conhecimento,
                'score': score,
                'data': conhecimento.get('data_atualizacao')
            })
    
    # Ordenar por score (relevância) e depois por data (mais recente)
    resultados.sort(key=lambda x: (x['score'], x['data']), reverse=True)
    
    # Retornar TODOS os resultados ordenados (sem limite)
    log(f"🔍 Busca: '{pergunta[:50]}...' → {len(resultados)} resultados (farmácia: {eh_farmacia}, escola: {eh_escola})")
    
    return [r['conhecimento'] for r in resultados]

# ========== FUNÇÕES DE LEADS ==========

def carregar_leads():
    """Carrega leads qualificados do arquivo JSON"""
    try:
        with open(ARQUIVO_LEADS, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"leads": []}
    except Exception as e:
        log(f"❌ Erro ao carregar leads: {e}")
        return {"leads": []}

def salvar_lead(contact_id, dados_lead):
    """Salva lead qualificado no arquivo JSON"""
    try:
        leads_data = carregar_leads()
        
        lead = {
            "contact_id": contact_id,
            "data_qualificacao": datetime.now(TIMEZONE).isoformat(),
            **dados_lead
        }
        
        leads_data['leads'].append(lead)
        
        with open(ARQUIVO_LEADS, 'w', encoding='utf-8') as f:
            json.dump(leads_data, f, indent=2, ensure_ascii=False)
        
        log(f"✅ Lead salvo: {dados_lead.get('nome', 'N/A')} - {dados_lead.get('segmento', 'N/A')}")
        return True
    except Exception as e:
        log(f"❌ Erro ao salvar lead: {e}")
        return False

def extrair_dados_conversa(historico):
    """Extrai dados do lead a partir do histórico de conversa"""
    dados = {
        "segmento": None,  # farmacia ou escola
        "porte": None,  # número de funcionários ou alunos
        "nome": None,
        "email": None,
        "cnpj": None,
        "reuniao_agendada": False
    }
    
    # Analisar histórico para extrair informações
    texto_completo = " ".join([msg.get('content', '') for msg in historico]).lower()
    
    # Identificar segmento
    if any(palavra in texto_completo for palavra in ['farmácia', 'farmacia', 'drogaria']):
        dados['segmento'] = 'farmacia'
    elif any(palavra in texto_completo for palavra in ['escola', 'colégio', 'colegio']):
        dados['segmento'] = 'escola'
    
    # Tentar extrair números (porte)
    import re
    numeros = re.findall(r'\b\d+\b', texto_completo)
    if numeros:
        dados['porte'] = numeros[0]  # Primeiro número encontrado
    
    # Tentar extrair nome (procurar por padrões como "meu nome é", "sou", "me chamo")
    nome_patterns = [
        r'(?:meu nome é|me chamo|sou o|sou a|sou)\s+([A-ZÁÉÍÓÚ][a-záéíóú]+(?:\s+[A-ZÁÉÍÓÚ][a-záéíóú]+)*)',
        r'([A-ZÁÉÍÓÚ][a-záéíóú]+(?:\s+[A-ZÁÉÍÓÚ][a-záéíóú]+)+)(?=\s*,|\s*\.|$)'  # Nome com sobrenome
    ]
    for pattern in nome_patterns:
        nomes = re.findall(pattern, texto_completo, re.IGNORECASE)
        if nomes:
            dados['nome'] = nomes[0].strip()
            break
    
    # Tentar extrair email
    emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', texto_completo)
    if emails:
        dados['email'] = emails[0]
    
    # Tentar extrair CNPJ
    cnpjs = re.findall(r'\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b', texto_completo)
    if cnpjs:
        dados['cnpj'] = cnpjs[0]
    
    return dados

# ========== FUNÇÕES DE IA (OPENAI) ==========

def gerar_resposta_ia(pergunta, contexto_conhecimento, historico_conversa=None, dados_lead=None):
    """
    Gera resposta usando OpenAI GPT-4o-mini
    Usa conhecimento da base como contexto + histórico da conversa + dados já coletados
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
        
        # Informações sobre dados já coletados
        dados_coletados = ""
        if dados_lead:
            dados_coletados = "\n\nDADOS JÁ COLETADOS DO LEAD:\n"
            if dados_lead.get('segmento'):
                dados_coletados += f"- Segmento: {dados_lead['segmento']}\n"
            if dados_lead.get('porte'):
                dados_coletados += f"- Porte: {dados_lead['porte']}\n"
            if dados_lead.get('nome'):
                dados_coletados += f"- Nome: {dados_lead['nome']}\n"
            if dados_lead.get('email'):
                dados_coletados += f"- Email: {dados_lead['email']}\n"
            if dados_lead.get('cnpj'):
                dados_coletados += f"- CNPJ: {dados_lead['cnpj']}\n"
            dados_coletados += "\n⚠️ NÃO PEÇA NOVAMENTE informações já coletadas!\n"
        
        # Prompt do sistema
        system_prompt = f"""Você é Manu, consultora da Difarda Moda Corporativa, especializada em uniformes para farmácias e escolas privadas.

OBJETIVO:
Conversar naturalmente com o lead, entender suas necessidades e, se houver fit, agendar uma reunião online.

TOM E PERSONALIDADE:
- **ACOLHEDORA e EDUCADA**: Sempre cordial e respeitosa
- **CURIOSA de forma NATURAL**: Faça perguntas como se estivesse genuinamente interessada em ajudar
- **CONSULTIVA**: Primeiro entenda, depois apresente soluções
- **PACIENTE**: Não tenha pressa, deixe a conversa fluir
- **HUMANA**: Converse como uma pessoa real, não como um robô

COMO CONVERSAR:
- **RESPOSTAS CURTAS**: 1-2 linhas (30-50 palavras)
- **UMA pergunta por vez**: Nunca bombardeie o cliente
- **LEIA O HISTÓRICO**: Reconheça o que já foi dito
- **SEJA NATURAL**: Use expressões como "Que legal!", "Entendo", "Interessante!"
- **GUIE SUAVEMENTE**: Faça perguntas que naturalmente levem às informações que precisa
- EVITE emojis e asteriscos

DADOS QUE VOCÊ PRECISA COLETAR (na ordem natural da conversa):
1. Segmento (farmácia ou escola) - geralmente já vem na primeira mensagem
2. Porte (nº de funcionários/lojas OU nº de alunos)
3. Nome do responsável
4. Email
5. CNPJ

REGRAS CRÍTICAS:
1. **INTELIGÊNCIA CONVERSACIONAL**: NÃO faça todas as perguntas de uma vez!
2. **CONTEXTO**: Se cliente já mencionou algo, reconheça e não peça novamente
3. **QUALIFICAÇÃO**: Identifique se está no perfil ideal antes de agendar reunião
4. **OBJETIVO**: Após coletar os dados, SEMPRE ofereça agendamento de reunião online

PERFIL IDEAL:
- Farmácias: Redes com múltiplas lojas OU 10+ funcionários
- Escolas: Qualquer porte (temos soluções para pequenas e grandes)

{contexto_texto}

{dados_coletados}

EXEMPLOS DE ABORDAGEM ACOLHEDORA:

Cliente: "Olá"
Você: "Oi! Tudo bem? Como posso te ajudar hoje?"

Cliente: "Tenho uma rede de farmácias"
Você: "Que legal! Vocês trabalham com uniformes para a equipe?"

Cliente: "Sim, mas é complicado"
Você: "Imagino... O que costuma ser mais desafiador pra vocês?"

Cliente: "Sempre falta uniforme quando entra gente nova"
Você: "Entendo, isso é bem comum mesmo. Quantas lojas vocês têm?"

Cliente: "5 lojas"
Você: "Legal! Trabalhamos com várias redes e temos um modelo de planejamento anual que resolve isso. Posso te contar mais?"

Cliente: "Pode sim"
Você: "Perfeito! Pra eu preparar algo mais personalizado, qual seu nome?"

---

Cliente: "Sou de uma escola"
Você: "Que bacana! É escola particular?"

Cliente: "Sim"
Você: "Legal! Quantos alunos vocês têm mais ou menos?"

Cliente: "Uns 300"
Você: "Entendi! Vocês já trabalham com uniformes ou estão começando agora?"

QUANDO TIVER TODOS OS DADOS:
"Perfeito, [Nome]! Olha, acho que temos uma solução bem interessante pro seu caso. Que tal a gente marcar uma conversa online pra eu te apresentar nossa equipe e a gente ver isso com mais calma? Você tem disponibilidade essa semana?"

IMPORTANTE:
- Use APENAS as informações da base de conhecimento
- Seja consultivo mas OBJETIVO
- Não invente informações técnicas ou comerciais
- Foque em QUALIFICAR e AGENDAR"""

        # Montar mensagens com histórico
        messages = [{"role": "system", "content": system_prompt}]
        
        # Adicionar histórico de conversa (se existir)
        if historico_conversa:
            # Limitar a últimas 15 mensagens para não exceder tokens
            historico_limitado = historico_conversa[-15:]
            messages.extend(historico_limitado)
            log(f"📚 Usando histórico: {len(historico_limitado)} mensagens anteriores")
        else:
            log("🆕 Primeira mensagem do cliente (sem histórico)")
        
        # Chamar OpenAI
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.7,
            max_tokens=200
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
    dia_semana = agora.weekday()  # 0=segunda, 6=domingo
    
    # Verificar se é dia útil (seg-sex)
    if dia_semana >= 5:  # sábado ou domingo
        return False, f"Fim de semana ({hora_atual}h)"
    
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

# ========== WEBHOOK ENDPOINT ==========

@app.route('/webhook/prospeccao', methods=['POST'])
def webhook_prospeccao():
    """Recebe mensagens do Digisac via webhook - APENAS para número de prospecção"""
    print("[DEBUG] Webhook chamado!")
    log("[DEBUG] Iniciando webhook_prospeccao()")
    try:
        print("[DEBUG] Tentando pegar JSON...")
        dados = request.get_json()
        print(f"[DEBUG] JSON recebido: {dados}")
        log(f"📥 Webhook PROSPECÇÃO recebido - Dados: {str(dados)[:200]}")
        
        # Verificar tipo de evento
        evento = dados.get('event', '')
        print(f"[DEBUG] Evento: {evento}")
        log(f"[DEBUG] Evento recebido: {evento}")
        if evento != 'message.created':
            log(f"⏭️ Evento '{evento}' ignorado")
            return jsonify({"status": "ignored"}), 200
        
        # Extrair informações
        data = dados.get('data', {})
        print(f"[DEBUG] Data: {data}")
        mensagem_texto = data.get('text', '')
        contact_id = data.get('contactId', '')
        is_from_me = data.get('isFromMe', False)
        is_from_bot = data.get('isFromBot', False)
        phone_number = data.get('phoneNumber', '')
        print(f"[DEBUG] Mensagem: {mensagem_texto}, Contact: {contact_id}, Phone: {phone_number}")
        log(f"[DEBUG] Mensagem: '{mensagem_texto[:50]}', Contact: {contact_id}, Phone: {phone_number}")
        
        # Filtrar por número de telefone específico (se configurado)
        print(f"[DEBUG] TELEFONE_PROSPECCAO: {TELEFONE_PROSPECCAO}")
        print(f"[DEBUG] phone_number: {phone_number}")
        if TELEFONE_PROSPECCAO and phone_number != TELEFONE_PROSPECCAO:
            log(f"⏭️ Mensagem para outro número ({phone_number}), ignorando")
            return jsonify({"status": "wrong_number"}), 200
        print("[DEBUG] Telefone OK ou não configurado")
        
        # Verificar se há atendente humano
        ticket_user_id = data.get('ticketUserId')
        if ticket_user_id:
            log(f"⏸️ Atendente humano presente (ID: {ticket_user_id}) - Bot não atua")
            return jsonify({"status": "human_attending"}), 200
        
        # Ignorar mensagens do bot/próprias
        if is_from_me or is_from_bot:
            log("⏭️ Mensagem do bot/própria, ignorando")
            return jsonify({"status": "ignored"}), 200
        
        # Verificar mensagem vazia
        if not mensagem_texto or mensagem_texto.strip() == "":
            log("⏭️ Mensagem vazia, ignorando")
            return jsonify({"status": "empty_message"}), 200
        
        log(f"💬 Mensagem do lead: '{mensagem_texto[:50]}...'")
        
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
        
        # Marcar como processada
        mensagens_processadas[message_id] = agora
        
        # Verificar horário de funcionamento
        dentro_horario, status_horario = verificar_horario_funcionamento()
        log(f"⏰ Status horário: {status_horario}")
        
        if not dentro_horario:
            enviar_mensagem_digisac(contact_id, mensagem_fora_horario())
            return jsonify({"status": "outside_hours"}), 200
        
        # Buscar conhecimento relevante
        conhecimento = buscar_conhecimento(mensagem_texto)
        
        # Gerenciar histórico de conversa
        if contact_id not in conversas_clientes:
            conversas_clientes[contact_id] = {
                "historico": [],
                "dados": {}
            }
            log(f"🆕 Novo cliente: {contact_id}")
        else:
            log(f"🔄 Cliente recorrente: {contact_id} ({len(conversas_clientes[contact_id]['historico'])} msgs no histórico)")
        
        # Adicionar mensagem do cliente ao histórico
        conversas_clientes[contact_id]["historico"].append({
            "role": "user",
            "content": mensagem_texto
        })
        log(f"➕ Mensagem adicionada ao histórico")
        
        # Limitar histórico a últimas 20 mensagens
        if len(conversas_clientes[contact_id]["historico"]) > 20:
            conversas_clientes[contact_id]["historico"] = conversas_clientes[contact_id]["historico"][-20:]
        
        # Extrair dados do lead do histórico
        dados_lead = extrair_dados_conversa(conversas_clientes[contact_id]["historico"])
        conversas_clientes[contact_id]["dados"] = dados_lead
        
        # Log dos dados extraídos
        dados_coletados = [k for k, v in dados_lead.items() if v]
        if dados_coletados:
            log(f"📋 Dados coletados até agora: {', '.join(dados_coletados)}")
        else:
            log("📋 Nenhum dado coletado ainda")
        
        # Gerar resposta com IA
        resposta = gerar_resposta_ia(
            mensagem_texto,
            conhecimento,
            conversas_clientes[contact_id]["historico"],
            dados_lead
        )
        
        # Adicionar resposta do bot ao histórico
        conversas_clientes[contact_id]["historico"].append({
            "role": "assistant",
            "content": resposta
        })
        
        # DELAY de 15 segundos para parecer mais humano
        log("⏳ Aguardando 15 segundos para parecer mais humano...")
        time.sleep(15)
        
        # Enviar resposta
        enviar_mensagem_digisac(contact_id, resposta)
        
        # Verificar se lead está qualificado (tem dados mínimos)
        if dados_lead.get('segmento') and dados_lead.get('porte') and dados_lead.get('email'):
            log(f"✅ Lead qualificado: {contact_id}")
            salvar_lead(contact_id, dados_lead)
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        print(f"[DEBUG] ERRO NO WEBHOOK: {e}")
        import traceback
        traceback.print_exc()
        log(f"❌ Erro no webhook: {e}")
        log(f"❌ Traceback: {traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== ROTA DE SAÚDE ==========

@app.route('/health', methods=['GET'])
def health():
    """Endpoint de saúde para monitoramento"""
    return jsonify({
        "status": "ok",
        "tipo": "prospeccao",
        "timestamp": datetime.now(TIMEZONE).isoformat()
    }), 200

# ========== INICIALIZAÇÃO ==========

if __name__ == '__main__':
    log("🚀 Agente de Prospecção iniciado")
    log(f"📞 Telefone configurado: {TELEFONE_PROSPECCAO if TELEFONE_PROSPECCAO else 'TODOS'}")
    log(f"⏰ Horário: {HORA_INICIO}h-{HORA_FIM}h (seg-sex)")
    app.run(host='0.0.0.0', port=5000, debug=False)
