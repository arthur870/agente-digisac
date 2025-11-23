# Agente Digisac + Manus AI - Atendimento Automatizado
import time
import requests
import json
from datetime import datetime
from flask import Flask, request, jsonify

# ========== CONFIGURAÇÕES ==========

# Digisac
DIGISAC_URL = "https://difardamodacorporativa.digisac.me"
DIGISAC_TOKEN = "8177228f681aa4c27ee4b5e585fe1eaddb7098a6"
DIGISAC_USER_ID = None  # Bot responde sem usuário específico

# Manus API
MANUS_API_URL = "https://api.manus.ai/v1/tasks"
MANUS_API_KEY = "sk-GSVUEUMHfpkVNn8Ql7v-RXsb8gbd1Aq3ZIfWvzOzFfJClHwS3RhX0rxJiminQy5Dl5FwVYzYcmVBC338h7WzJOJWZENo".strip()

# Task IDs por contato (cada conversa tem sua própria task)
tasks_por_contato = {}  # {contactId: task_id}
ARQUIVO_TASKS = "tasks_por_contato.json"
ARQUIVO_LOG = "agente_digisac_log.txt"

# Controle de mensagens processadas
mensagens_processadas = set()

# Histórico de conversas por contato (últimas 24h)
historico_conversas = {}  # {contactId: [(timestamp, role, mensagem), ...]}

# Perguntas pendentes para o gestor
perguntas_pendentes = {}  # {pergunta_id: {contact_id, nome, pergunta, timestamp, respondida}}
ARQUIVO_PERGUNTAS = "perguntas_pendentes.json"

# ========== NOVO: Controle de chamados com atendente humano ==========
chamados_com_atendente_humano = set()  # {contactId} - chamados assumidos por humanos
ARQUIVO_CHAMADOS_HUMANOS = "chamados_com_atendente_humano.json"

# Flask app
app = Flask(__name__)

# ========== INSTRUÇÕES DO AGENTE ==========
INSTRUCOES_AGENTE = """
Você é um assistente da Difarda, especializada em moda corporativa.
- Não precisa se identificar como virtual, apenas assistente. Use sempre o nome "Manu" para si
- Varie o tempo de resposta entre alguns segundos para ter mais humanidade no atendimento.
- Aguarde pelo menos 30 segundo para gerar uma resposta, as vezes o cliente vai enviar as mensagens de maneira espaçada mas para dizer uma coisa só
- Para orçamentos, será indispensável a definição do modelo, quantidade e ter o número do CNPJ do cliente
- Procure priorizar respostas mais curtas para que a conversa não fique cansativa
- Entre em detalhes apenas quando necessário
- Não precisa dar informações sem ser solicitado

PERSONALIDADE E TOM:
- Seja sempre cordial, empático e profissional
- Use uma linguagem natural e humanizada (sem menus numerados)
- Seja proativo em oferecer ajuda, de maneira moderada sem ser forçado
- Use emojis variados moderadamente para tornar a conversa mais agradável, mas não em todas as mensagens
- Dê preferência para mensagens mais curtas e objetivas, normalmente faça uma pergunta por vez
- Nosso pedido mínimo atual são 80 peças
- O prazo médio de entrega fica em 30 dias úteis
- Atendemos alguns setores com um planejamento estratégico anual e um contrato de fornecimento de uniformes que nos permite melhores condições de preço e prazo
- Para o setor de educação temos uma modalidade de atendimento que atende direto aos pais, garantindo uma gestão e garantia de produtos
- Evite o uso de asteriscos e sentenças em negrito
- Não precisa colocar uma introdução em todas as mensagens, depois de um certo momento, seja mais objetivo mas gentil
- Colégio e Curso Interativo nós atendemos diretamente aos pais por meio de um site que será lançado em breve
- Colégio Querubins está em negociação com a Difarda para nova produção de uniformes
- Colégio EAS está em negociação com a Difarda para nova produção de uniformes
- Colégio El Elyon está em negociação com a Difarda para nova produção de uniformes

INFORMAÇÕES DA EMPRESA:
- Nome: Difarda Moda Corporativa
- Segmento: Uniformes corporativos e moda profissional
- WhatsApp: (99) 98270-6201
- Horário de funcionamento: Segunda a Sexta, 8h às 18h 

PRODUTOS E SERVIÇOS:
- Uniformes corporativos (camisas polo, camisetas, jalecos, aventais)
- Bordados e personalizações
- Uniformes escolares
- EPIs (Equipamentos de Proteção Individual)

QUANDO NÃO TIVER CERTEZA:
- NUNCA invente informações sobre preços, prazos ou disponibilidade
- Responda: "Essa é uma ótima pergunta! Deixa eu verificar essa informação com minha equipe e já te retorno, ok?"
"""

# ========== FUNÇÕES DE LOG ==========

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")
    with open(ARQUIVO_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")

# ========== NOVAS FUNÇÕES: Gerenciamento de Atendente Humano ==========

def carregar_chamados_humanos():
    """Carrega lista de chamados com atendente humano do arquivo"""
    global chamados_com_atendente_humano
    try:
        with open(ARQUIVO_CHAMADOS_HUMANOS, "r") as f:
            chamados_com_atendente_humano = set(json.load(f))
            log(f"✅ {len(chamados_com_atendente_humano)} chamado(s) com atendente humano carregado(s)")
    except:
        chamados_com_atendente_humano = set()
        log("ℹ️ Nenhum chamado com atendente humano anterior encontrado")

def salvar_chamados_humanos():
    """Salva lista de chamados com atendente humano no arquivo"""
    with open(ARQUIVO_CHAMADOS_HUMANOS, "w") as f:
        json.dump(list(chamados_com_atendente_humano), f, indent=2)

def marcar_chamado_com_atendente_humano(contact_id):
    """Marca um chamado como assumido por atendente humano"""
    chamados_com_atendente_humano.add(contact_id)
    salvar_chamados_humanos()
    log(f"👤 Chamado {contact_id[:8]}... marcado como atendido por humano")

def desmarcar_chamado_com_atendente_humano(contact_id):
    """Remove a marcação de atendente humano de um chamado"""
    if contact_id in chamados_com_atendente_humano:
        chamados_com_atendente_humano.remove(contact_id)
        salvar_chamados_humanos()
        log(f"🤖 Chamado {contact_id[:8]}... desmarcado - automação pode reassumir")

def chamado_tem_atendente_humano(contact_id):
    """Verifica se um chamado está sendo atendido por humano"""
    return contact_id in chamados_com_atendente_humano

def verificar_atendente_humano_no_webhook(dados):
    """
    Verifica se o webhook indica que um atendente humano assumiu o chamado.
    
    Campos verificados:
    - ticket.userId: ID do usuário atribuído ao ticket
    - ticket.assignedUserId: ID do usuário que assumiu o ticket
    - data.userId: ID do usuário no contexto da mensagem
    - event: eventos específicos como 'ticket.assigned' ou 'ticket.transferred'
    
    Retorna: (bool, user_id ou None)
    """
    evento = dados.get('event', '')
    data = dados.get('data', {})
    
    # Verificar eventos específicos de atribuição/transferência
    if evento in ['ticket.assigned', 'ticket.transferred', 'ticket.updated']:
        log(f"🔔 Evento detectado: {evento}")
        
        # Verificar se há um userId atribuído (e não é o bot)
        ticket = data.get('ticket', {})
        user_id = ticket.get('userId') or ticket.get('assignedUserId') or data.get('userId')
        
        if user_id and user_id != DIGISAC_USER_ID:
            log(f"👤 Atendente humano detectado: userId={user_id}")
            return True, user_id
    
    # Verificar se a mensagem foi enviada por um usuário (não bot)
    if evento == 'message.created':
        is_from_me = data.get('isFromMe', False)
        is_from_bot = data.get('isFromBot', False)
        user_id = data.get('userId')
        
        # Se a mensagem foi enviada por um usuário (não é do cliente, não é do bot)
        if is_from_me and not is_from_bot and user_id and user_id != DIGISAC_USER_ID:
            log(f"👤 Mensagem de atendente humano detectada: userId={user_id}")
            return True, user_id
    
    return False, None

def verificar_encerramento_chamado_no_webhook(dados):
    """
    Verifica se o webhook indica que um chamado foi encerrado ou que o atendente "soltou" o chamado.
    
    Estratégias de detecção:
    1. Eventos de encerramento explícitos (ticket.closed, etc)
    2. Status do ticket (closed, finished, etc)
    3. Flag isClosed = true
    4. userId vazio/null em evento de atualização (atendente "soltou" o chamado)
    
    Retorna: (bool, contact_id ou None)
    """
    evento = dados.get('event', '')
    data = dados.get('data', {})
    contact_id = data.get('contactId')
    
    # Verificar eventos específicos de encerramento
    if evento in ['ticket.closed', 'ticket.finished', 'ticket.resolved', 'ticket.completed']:
        log(f"🔔 Evento de encerramento detectado: {evento}")
        if contact_id:
            return True, contact_id
    
    # Verificar status do ticket
    ticket = data.get('ticket', {})
    if ticket:
        status = ticket.get('status', '').lower()
        is_closed = ticket.get('isClosed', False)
        user_id = ticket.get('userId') or data.get('userId')
        
        if not contact_id:
            contact_id = ticket.get('contactId')
        
        # Verificar se status indica encerramento
        if status in ['closed', 'finished', 'resolved', 'completed', 'finalizado', 'encerrado', 'resolvido']:
            log(f"🔔 Chamado encerrado detectado por status: {status}")
            if contact_id:
                return True, contact_id
        
        # Verificar flag de fechamento
        if is_closed and contact_id:
            log(f"🔔 Chamado encerrado detectado por flag isClosed")
            return True, contact_id
        
        # NOVO: Verificar se atendente "soltou" o chamado (userId vazio/null)
        # Isso acontece quando o chamado é encerrado ou devolvido para a fila
        if evento in ['ticket.updated', 'ticket.transferred'] and contact_id:
            # Se userId é None, vazio ou igual ao bot, significa que não há mais atendente humano
            if user_id is None or user_id == '' or user_id == DIGISAC_USER_ID:
                log(f"🔔 Atendente 'soltou' o chamado - userId vazio/null")
                return True, contact_id
    
    return False, None

# ========== FUNÇÕES MANUS ==========

def carregar_tasks():
    """Carrega tasks por contato do arquivo"""
    global tasks_por_contato
    try:
        with open(ARQUIVO_TASKS, "r") as f:
            tasks_por_contato = json.load(f)
            log(f"✅ {len(tasks_por_contato)} task(s) carregada(s)")
    except:
        tasks_por_contato = {}
        log("ℹ️ Nenhuma task anterior encontrada")

def salvar_tasks():
    """Salva tasks por contato no arquivo"""
    with open(ARQUIVO_TASKS, "w") as f:
        json.dump(tasks_por_contato, f, indent=2)

def obter_task_id_contato(contact_id):
    """Retorna task_id do contato ou None se não existir"""
    return tasks_por_contato.get(contact_id)

def salvar_task_id_contato(contact_id, task_id):
    """Salva task_id para um contato específico"""
    tasks_por_contato[contact_id] = task_id
    salvar_tasks()
    log(f"💾 Task {task_id} vinculada ao contato {contact_id[:8]}...")

def adicionar_ao_historico(contact_id, role, mensagem):
    """Adiciona mensagem ao histórico do contato"""
    from datetime import datetime, timedelta
    
    if contact_id not in historico_conversas:
        historico_conversas[contact_id] = []
    
    timestamp = datetime.now()
    historico_conversas[contact_id].append((timestamp, role, mensagem))
    
    # Limpar mensagens com mais de 24h
    limite = datetime.now() - timedelta(hours=24)
    historico_conversas[contact_id] = [
        (ts, r, msg) for ts, r, msg in historico_conversas[contact_id]
        if ts > limite
    ]
    
    log(f"📚 Histórico de {contact_id}: {len(historico_conversas[contact_id])} mensagens")

def obter_contexto_conversa(contact_id):
    """Retorna histórico formatado das últimas 24h"""
    if contact_id not in historico_conversas or not historico_conversas[contact_id]:
        return ""
    
    contexto = "\n\n=== HISTÓRICO DA CONVERSA (últimas 24h) ===\n"
    for timestamp, role, mensagem in historico_conversas[contact_id]:
        hora = timestamp.strftime("%H:%M")
        if role == "cliente":
            contexto += f"[{hora}] Cliente: {mensagem}\n"
        else:
            contexto += f"[{hora}] Você: {mensagem}\n"
    contexto += "=== FIM DO HISTÓRICO ===\n\n"
    
    return contexto

def carregar_perguntas():
    """Carrega perguntas pendentes do arquivo"""
    global perguntas_pendentes
    try:
        with open(ARQUIVO_PERGUNTAS, "r", encoding="utf-8") as f:
            perguntas_pendentes = json.load(f)
            log(f"✅ {len(perguntas_pendentes)} pergunta(s) pendente(s)")
    except:
        perguntas_pendentes = {}

def salvar_perguntas():
    """Salva perguntas pendentes no arquivo"""
    with open(ARQUIVO_PERGUNTAS, "w", encoding="utf-8") as f:
        json.dump(perguntas_pendentes, f, indent=2, ensure_ascii=False)

def criar_pergunta_pendente(contact_id, nome, pergunta):
    """Cria uma pergunta pendente para o gestor"""
    import uuid
    pergunta_id = str(uuid.uuid4())[:8]
    perguntas_pendentes[pergunta_id] = {
        "contact_id": contact_id,
        "nome": nome,
        "pergunta": pergunta,
        "timestamp": datetime.now().isoformat(),
        "respondida": False,
        "resposta": None
    }
    salvar_perguntas()
    log(f"❓ Pergunta pendente criada: {pergunta_id}")
    return pergunta_id

def perguntar_manus(mensagem, nome, contact_id, imagem_url=None):
    """Envia mensagem para Manus e retorna resposta"""
    if imagem_url:
        log(f"📤 Manus: '{mensagem}' ({nome}) + 🖼️ Imagem")
    else:
        log(f"📤 Manus: '{mensagem}' ({nome})")
    
    # Obter ou criar task_id para este contato
    task_id_contato = obter_task_id_contato(contact_id)
    
    # Obter contexto da conversa
    contexto = obter_contexto_conversa(contact_id)
    
    # Montar prompt com contexto
    prompt_completo = f"{INSTRUCOES_AGENTE}{contexto}\nCliente ({nome}): {mensagem}"
    
    dados = {
        "prompt": prompt_completo,
        "mode": "speed"
    }
    
    # Adicionar imagem se houver
    if imagem_url:
        dados["attachments"] = [
            {
                "type": "image",
                "url": imagem_url
            }
        ]
        log(f"🖼️ Imagem anexada: {imagem_url}")
    
    # Se já existe task para este contato, continuar nela
    if task_id_contato:
        dados["taskId"] = task_id_contato
        log(f"🔄 Continuando task existente: {task_id_contato}")
    
    headers = {
        "API_KEY": MANUS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        log(f"🔗 POST {MANUS_API_URL}")
        resp = requests.post(MANUS_API_URL, json=dados, headers=headers, timeout=30)
        log(f"📊 Status: {resp.status_code}")
        
        if resp.status_code in [200, 201]:
            resultado = resp.json()
            log(f"📝 Resposta: {json.dumps(resultado, indent=2)}")
            task_id = resultado.get('task_id')
            
            if not task_id:
                log("❌ task_id não encontrado na resposta")
                return "Desculpe, tive um problema técnico."
            
            # Salvar task_id para este contato se for nova
            if not task_id_contato:
                salvar_task_id_contato(contact_id, task_id)
            
            log(f"⏳ Aguardando 10s para processar task {task_id}...")
            time.sleep(10)
            
            log(f"🔗 GET {MANUS_API_URL}/{task_id}")
            resp2 = requests.get(f"{MANUS_API_URL}/{task_id}", headers=headers, timeout=30)
            log(f"📊 Status: {resp2.status_code}")
            
            if resp2.status_code == 200:
                dados_resultado = resp2.json()
                log(f"📝 Resultado: {json.dumps(dados_resultado, indent=2)}")
                
                status = dados_resultado.get('status')
                log(f"🔍 Status da task: {status}")
                
                if status == 'completed':
                    # Extrair texto do output
                    output = dados_resultado.get('output', [])
                    if output and len(output) > 0:
                        # Pegar última mensagem do assistant
                        for msg in reversed(output):
                            if msg.get('role') == 'assistant':
                                content = msg.get('content', [])
                                if content and len(content) > 0:
                                    texto = content[0].get('text', '')
                                    if texto:
                                        # Verificar se Manus indicou dúvida
                                        palavras_chave = [
                                            "verificar com minha equipe",
                                            "verificar essa informação",
                                            "consultar minha equipe",
                                            "perguntar para",
                                            "não tenho certeza"
                                        ]
                                        
                                        tem_duvida = any(palavra in texto.lower() for palavra in palavras_chave)
                                        
                                        if tem_duvida:
                                            # Criar pergunta pendente
                                            criar_pergunta_pendente(contact_id, nome, mensagem)
                                            log("❓ Dúvida detectada - pergunta criada para gestor")
                                        
                                        log("✅ Resposta Manus recebida")
                                        return texto
                    
                    log("⚠️ Output vazio")
                    return "Desculpe, não consegui gerar uma resposta."
                elif status in ['pending', 'running']:
                    log(f"⚠️ Task ainda processando: {status}")
                    # Aguardar mais tempo
                    time.sleep(5)
                    return perguntar_manus(mensagem, nome, contact_id, imagem_url)  # Tentar novamente
                else:
                    log(f"❌ Task falhou: {status}")
                    erro = dados_resultado.get('error', 'Erro desconhecido')
                    log(f"❌ Erro: {erro}")
                    return "Desculpe, tive um problema técnico."
            else:
                log(f"❌ Erro ao buscar resultado: {resp2.text}")
        else:
            log(f"❌ Erro na requisição: {resp.text}")
        
        return "Desculpe, tive um problema técnico."
    except Exception as e:
        log(f"❌ Erro Manus: {e}")
        import traceback
        log(f"🐛 Traceback: {traceback.format_exc()}")
        return "Desculpe, estou com dificuldades no momento."

# ========== FUNÇÕES DIGISAC ==========

def enviar_mensagem_digisac(contact_id, texto):
    """Envia mensagem via API Digisac"""
    log(f"📤 Digisac: '{texto}' (contact: {contact_id})")
    
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
    
    # Adicionar userId apenas se configurado
    if DIGISAC_USER_ID:
        payload["userId"] = DIGISAC_USER_ID
    
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

@app.route('/webhook', methods=['POST'])
def webhook():
    """Recebe mensagens do Digisac via webhook"""
    try:
        dados = request.get_json()
        log(f"📥 Webhook recebido: {json.dumps(dados, indent=2)}")
        
        # ========== NOVO: Verificar se chamado foi encerrado ==========
        chamado_encerrado, contact_id_encerrado = verificar_encerramento_chamado_no_webhook(dados)
        
        if chamado_encerrado and contact_id_encerrado:
            # Remover da lista de chamados com atendente humano
            if chamado_tem_atendente_humano(contact_id_encerrado):
                desmarcar_chamado_com_atendente_humano(contact_id_encerrado)
                log(f"✅ Chamado {contact_id_encerrado[:8]}... encerrado - Automação pode reassumir")
            return jsonify({"status": "ticket_closed", "message": "Automação pode reassumir"}), 200
        
        # ========== NOVO: Verificar se atendente humano assumiu o chamado ==========
        tem_atendente_humano, user_id = verificar_atendente_humano_no_webhook(dados)
        
        if tem_atendente_humano:
            data = dados.get('data', {})
            contact_id = data.get('contactId', '')
            
            if contact_id:
                marcar_chamado_com_atendente_humano(contact_id)
                log(f"🛑 Automação PAUSADA para {contact_id[:8]}... - Atendente humano assumiu")
                return jsonify({"status": "human_agent_assigned", "message": "Automação pausada"}), 200
        
        # Verificar tipo de evento
        evento = dados.get('event', '')
        if evento != 'message.created':
            log(f"⏭️ Evento '{evento}' ignorado")
            return jsonify({"status": "ignored"}), 200
        
        # Extrair informações da mensagem
        data = dados.get('data', {})
        mensagem_texto = data.get('text', '')
        contact_id = data.get('contactId', '')
        tipo_mensagem = data.get('type', 'chat')
        
        # ========== NOVO: Verificar se chamado está com atendente humano ==========
        if chamado_tem_atendente_humano(contact_id):
            # VERIFICAÇÃO ADICIONAL: Se o chamado estava pausado, verificar se ainda tem atendente
            # Isso cobre casos onde o encerramento não foi detectado por evento
            ticket = data.get('ticket', {})
            user_id = ticket.get('userId') or data.get('userId')
            
            # Se não há mais userId (atendente "soltou"), reassumir automação
            if user_id is None or user_id == '' or user_id == DIGISAC_USER_ID:
                log(f"✅ Atendente não está mais atribuído - Reassumindo automação")
                desmarcar_chamado_com_atendente_humano(contact_id)
                # Continuar processamento normal abaixo
            else:
                # Ainda tem atendente humano, ignorar mensagem
                log(f"🛑 Mensagem ignorada - Chamado {contact_id[:8]}... está com atendente humano (userId={user_id})")
                return jsonify({"status": "ignored_human_agent"}), 200
        
        # Extrair URL da imagem se houver
        imagem_url = None
        data_extra = data.get('data', {})
        if tipo_mensagem == 'image':
            imagem_url = data_extra.get('fileUrl') or data.get('fileUrl')
            if imagem_url:
                log(f"🖼️ Imagem detectada: {imagem_url}")
                # Se não tem texto, adicionar prompt padrão
                if not mensagem_texto:
                    mensagem_texto = "O cliente enviou esta imagem. Analise e responda de forma útil."
        
        # Verificar se é mensagem recebida (não enviada pelo bot)
        is_from_me = data.get('isFromMe', False)
        is_from_bot = data.get('isFromBot', False)
        
        if is_from_me or is_from_bot:
            log("⏭️ Mensagem do bot/própria, ignorando")
            return jsonify({"status": "ignored"}), 200
        
        # Buscar nome do contato (pode vir em outro webhook)
        contact_name = 'Cliente'
        
        # Verificar se já foi processada
        msg_id = f"{contact_id}_{mensagem_texto}"
        if msg_id in mensagens_processadas:
            log("⏭️ Mensagem já processada")
            return jsonify({"status": "already_processed"}), 200
        
        # Verificar dados mínimos
        if not contact_id:
            log("⚠️ Contact ID ausente")
            return jsonify({"status": "incomplete_data"}), 400
        
        if not mensagem_texto and not imagem_url:
            log("⚠️ Mensagem sem conteúdo")
            return jsonify({"status": "no_content"}), 200
        
        log(f"💬 Mensagem de {contact_name}: '{mensagem_texto}'")
        
        # Adicionar mensagem do cliente ao histórico
        adicionar_ao_historico(contact_id, "cliente", mensagem_texto)
        
        # Processar com Manus (com ou sem imagem)
        resposta = perguntar_manus(mensagem_texto, contact_name, contact_id, imagem_url)
        
        # Adicionar resposta ao histórico
        adicionar_ao_historico(contact_id, "assistente", resposta)
        
        # Enviar resposta via Digisac
        if enviar_mensagem_digisac(contact_id, resposta):
            mensagens_processadas.add(msg_id)
            log(f"✅ Processamento completo: {contact_name}")
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "send_failed"}), 500
            
    except Exception as e:
        log(f"❌ Erro no webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/webhook/digisac', methods=['POST'])
def webhook_digisac():
    """Alias para webhook (compatibilidade)"""
    return webhook()

@app.route('/health', methods=['GET'])
def health():
    """Endpoint de health check"""
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()}), 200

# ========== NOVO: Endpoints para gerenciar atendentes humanos ==========

@app.route('/chamados/humanos', methods=['GET'])
def listar_chamados_humanos():
    """Lista todos os chamados que estão com atendente humano"""
    return jsonify({
        "chamados_com_atendente_humano": list(chamados_com_atendente_humano),
        "total": len(chamados_com_atendente_humano)
    }), 200

@app.route('/chamados/reassumir/<contact_id>', methods=['POST'])
def reassumir_automacao(contact_id):
    """Permite que a automação reassuma um chamado após atendimento humano"""
    desmarcar_chamado_com_atendente_humano(contact_id)
    return jsonify({
        "status": "success",
        "message": f"Automação pode reassumir chamado {contact_id}"
    }), 200

@app.route('/chamados/pausar/<contact_id>', methods=['POST'])
def pausar_automacao(contact_id):
    """Pausa manualmente a automação para um chamado específico"""
    marcar_chamado_com_atendente_humano(contact_id)
    return jsonify({
        "status": "success",
        "message": f"Automação pausada para chamado {contact_id}"
    }), 200

# ========== INTERFACE DO GESTOR ==========

@app.route('/gestor', methods=['GET'])
def gestor_interface():
    """Interface web para o gestor responder perguntas"""
    html = '''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gestão de Perguntas - Difarda</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
        }
        .header {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            margin-bottom: 30px;
            text-align: center;
        }
        .header h1 {
            color: #333;
            font-size: 28px;
            margin-bottom: 10px;
        }
        .header p {
            color: #666;
            font-size: 14px;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.1);
            text-align: center;
        }
        .stat-card h3 {
            font-size: 32px;
            color: #667eea;
            margin-bottom: 5px;
        }
        .stat-card p {
            color: #666;
            font-size: 14px;
        }
        .pergunta-card {
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            margin-bottom: 20px;
            border-left: 5px solid #667eea;
        }
        .pergunta-card.respondida {
            border-left-color: #48bb78;
            opacity: 0.7;
        }
        .pergunta-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .pergunta-id {
            background: #667eea;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }
        .pergunta-timestamp {
            color: #999;
            font-size: 12px;
        }
        .cliente-info {
            background: #f7fafc;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 15px;
        }
        .cliente-nome {
            font-weight: bold;
            color: #333;
            margin-bottom: 5px;
        }
        .cliente-id {
            color: #999;
            font-size: 12px;
        }
        .pergunta-texto {
            background: #edf2f7;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 15px;
            font-size: 16px;
            color: #333;
            line-height: 1.6;
        }
        .resposta-form {
            display: flex;
            gap: 10px;
        }
        .resposta-input {
            flex: 1;
            padding: 12px;
            border: 2px solid #e2e8f0;
            border-radius: 10px;
            font-size: 14px;
            font-family: inherit;
        }
        .resposta-input:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn-enviar {
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 10px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s;
        }
        .btn-enviar:hover {
            background: #5568d3;
            transform: translateY(-2px);
        }
        .badge-respondida {
            background: #48bb78;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }
        .resposta-enviada {
            background: #c6f6d5;
            padding: 15px;
            border-radius: 10px;
            color: #22543d;
            margin-top: 15px;
        }
        .empty-state {
            background: white;
            padding: 60px;
            border-radius: 15px;
            text-align: center;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
        }
        .empty-state h2 {
            color: #333;
            margin-bottom: 10px;
        }
        .empty-state p {
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>👔 Difarda - Gestão de Perguntas</h1>
            <p>Responda às perguntas dos clientes que o assistente não conseguiu resolver</p>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <h3 id="total-perguntas">0</h3>
                <p>Total de Perguntas</p>
            </div>
            <div class="stat-card">
                <h3 id="pendentes">0</h3>
                <p>Pendentes</p>
            </div>
            <div class="stat-card">
                <h3 id="respondidas">0</h3>
                <p>Respondidas</p>
            </div>
        </div>
        
        <div id="perguntas-container"></div>
    </div>
    
    <script>
        function carregarPerguntas() {
            fetch('/gestor/perguntas')
                .then(r => r.json())
                .then(data => {
                    const container = document.getElementById('perguntas-container');
                    const perguntas = data.perguntas;
                    
                    // Atualizar stats
                    const total = perguntas.length;
                    const pendentes = perguntas.filter(p => !p.respondida).length;
                    const respondidas = total - pendentes;
                    
                    document.getElementById('total-perguntas').textContent = total;
                    document.getElementById('pendentes').textContent = pendentes;
                    document.getElementById('respondidas').textContent = respondidas;
                    
                    if (total === 0) {
                        container.innerHTML = `
                            <div class="empty-state">
                                <h2>✅ Tudo em dia!</h2>
                                <p>Não há perguntas pendentes no momento.</p>
                            </div>
                        `;
                        return;
                    }
                    
                    container.innerHTML = perguntas.map(p => `
                        <div class="pergunta-card ${p.respondida ? 'respondida' : ''}">
                            <div class="pergunta-header">
                                <span class="pergunta-id">#${p.id}</span>
                                ${p.respondida ? '<span class="badge-respondida">✅ Respondida</span>' : ''}
                                <span class="pergunta-timestamp">${new Date(p.timestamp).toLocaleString('pt-BR')}</span>
                            </div>
                            <div class="cliente-info">
                                <div class="cliente-nome">👤 ${p.nome}</div>
                                <div class="cliente-id">ID: ${p.contact_id}</div>
                            </div>
                            <div class="pergunta-texto">
                                ❓ ${p.pergunta}
                            </div>
                            ${p.respondida ? `
                                <div class="resposta-enviada">
                                    ✅ Resposta enviada: ${p.resposta}
                                </div>
                            ` : `
                                <div class="resposta-form">
                                    <input 
                                        type="text" 
                                        class="resposta-input" 
                                        id="resposta-${p.id}" 
                                        placeholder="Digite sua resposta aqui..."
                                    />
                                    <button 
                                        class="btn-enviar" 
                                        onclick="enviarResposta('${p.id}')"
                                    >
                                        Enviar
                                    </button>
                                </div>
                            `}
                        </div>
                    `).join('');
                });
        }
        
        function enviarResposta(perguntaId) {
            const input = document.getElementById(`resposta-${perguntaId}`);
            const resposta = input.value.trim();
            
            if (!resposta) {
                alert('Por favor, digite uma resposta!');
                return;
            }
            
            fetch('/gestor/responder', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    pergunta_id: perguntaId,
                    resposta: resposta
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    alert('✅ Resposta enviada com sucesso!');
                    carregarPerguntas();
                } else {
                    alert('❌ Erro: ' + data.message);
                }
            });
        }
        
        // Carregar perguntas ao abrir
        carregarPerguntas();
        
        // Atualizar a cada 10 segundos
        setInterval(carregarPerguntas, 10000);
    </script>
</body>
</html>
    '''
    return html

@app.route('/gestor/perguntas', methods=['GET'])
def gestor_listar_perguntas():
    """API para listar perguntas pendentes"""
    perguntas_lista = []
    for pergunta_id, dados in perguntas_pendentes.items():
        perguntas_lista.append({
            "id": pergunta_id,
            **dados
        })
    # Ordenar por timestamp (mais recentes primeiro)
    perguntas_lista.sort(key=lambda x: x['timestamp'], reverse=True)
    return jsonify({"perguntas": perguntas_lista})

@app.route('/gestor/responder', methods=['POST'])
def gestor_responder():
    """API para responder uma pergunta e enviar ao cliente"""
    try:
        dados = request.get_json()
        pergunta_id = dados.get('pergunta_id')
        resposta = dados.get('resposta')
        
        if not pergunta_id or not resposta:
            return jsonify({"status": "error", "message": "Dados incompletos"}), 400
        
        if pergunta_id not in perguntas_pendentes:
            return jsonify({"status": "error", "message": "Pergunta não encontrada"}), 404
        
        pergunta = perguntas_pendentes[pergunta_id]
        contact_id = pergunta['contact_id']
        
        # Enviar resposta para o cliente via Digisac
        if enviar_mensagem_digisac(contact_id, resposta):
            # Marcar como respondida
            perguntas_pendentes[pergunta_id]['respondida'] = True
            perguntas_pendentes[pergunta_id]['resposta'] = resposta
            salvar_perguntas()
            
            log(f"✅ Gestor respondeu pergunta {pergunta_id}")
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "error", "message": "Erro ao enviar mensagem"}), 500
            
    except Exception as e:
        log(f"❌ Erro ao responder: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== INICIALIZAÇÃO ==========

if __name__ == "__main__":
    print("="*70)
    print("🤖 AGENTE DIGISAC + MANUS AI")
    print("="*70)
    print()
    
    carregar_tasks()
    carregar_perguntas()
    carregar_chamados_humanos()  # NOVO: Carregar chamados com atendente humano
    
    log("="*70)
    log("🚀 SISTEMA INICIADO")
    log("="*70)
    log(f"📡 Webhook: http://localhost:5000/webhook")
    log(f"❤️ Health: http://localhost:5000/health")
    log(f"👨‍💼 Gestor: http://localhost:5000/gestor")
    log(f"👤 Chamados Humanos: http://localhost:5000/chamados/humanos")
    log("="*70)
    
    print("\n⚠️ CONFIGURAÇÃO NECESSÁRIA:")
    print("1. Substitua DIGISAC_TOKEN pelo seu token")
    print("2. Substitua DIGISAC_USER_ID pelo seu user ID")
    print("3. Configure webhook no Digisac apontando para seu servidor")
    print("4. Use ngrok ou similar para expor porta 5000 publicamente")
    print()
    print("Exemplo ngrok: ngrok http 5000")
    print("URL webhook: https://SEU_DOMINIO.ngrok.io/webhook")
    print()
    print("📌 NOVOS RECURSOS:")
    print("- Automação pausa automaticamente quando atendente humano assume")
    print("- Endpoint /chamados/humanos para ver chamados com atendente")
    print("- Endpoint /chamados/reassumir/<contact_id> para reassumir automação")
    print("- Endpoint /chamados/pausar/<contact_id> para pausar manualmente")
    print()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
