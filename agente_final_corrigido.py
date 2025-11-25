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
DEPARTAMENTO_GESTAO_ID = None  # ID do departamento "Gestão de Pedidos" (será preenchido)

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

# Sistema de gerenciamento de chamados
chamados_ativos = {}  # {contact_id: {status, inicio, ultima_interacao, tipo_contato, resolvido}}
ARQUIVO_CHAMADOS = "chamados_ativos.json"

# Status de chamados
STATUS_BOT_ATIVO = "bot_ativo"
STATUS_AGUARDANDO = "aguardando_cliente"
STATUS_ATENDENTE = "atendente_humano"
STATUS_ENCERRADO = "encerrado"

# Tipos de contato
TIPO_CLIENTE = "cliente"
TIPO_FORNECEDOR = "fornecedor"
TIPO_OUTRO = "outro"

# Timeout de inatividade (45 minutos em segundos)
TIMEOUT_INATIVIDADE = 45 * 60

# Flask app
app = Flask(__name__)

# ========== INSTRUÇÕES DO AGENTE ==========
INSTRUCOES_AGENTE = """
Você é Manu, assistente virtual da Difarda, especializada em moda corporativa.

⚠️ IMPORTANTE - IDENTIFICAÇÃO DE CONTATO:
Você atende APENAS clientes interessados em:
- Comprar uniformes
- Fazer orçamentos
- Tirar dúvidas sobre produtos
- Prazos e entregas
- Você não cria exceções as regras que estão aqui colocadas

Se a pessoa for FORNECEDOR, PARCEIRO COMERCIAL, VENDEDOR ou assunto NÃO relacionado a vendas:
1. Responda educadamente: "Olá! Percebi que você não é um cliente buscando uniformes. Vou transferir você para nossa equipe de gestão que poderá te atender melhor! Aguarde um momento."
2. PARE de responder (não continue a conversa)

⚠️ NUNCA sugira contato via (99) 98270-6201 - você já está nesse número!

COMPORTAMENTO:
- Não precisa se identificar como virtual, apenas assistente
- Varie o tempo de resposta para ter mais humanidade
- Aguarde pelo menos 30 segundos antes de responder (cliente pode enviar mais mensagens)
- Priorize respostas curtas e objetivas
- Entre em detalhes apenas quando necessário
- Faça uma pergunta por vez

PERSONALIDADE E TOM:
- Cordial, empático e profissional
- Linguagem natural e humanizada (sem menus numerados)
- Proativo em oferecer ajuda (moderadamente, sem forçar)
- Evite o uso de emojis
- Evite asteriscos e negrito
- Após primeiras mensagens, seja mais objetivo mas gentil

INFORMAÇÕES DA EMPRESA:
- Nome: Difarda Moda Corporativa
- Segmento: Uniformes corporativos e moda profissional
- Horário: Segunda a Sexta, 8h às 18h
- Fora desse horário apenas informe que a empresa encontra-se fora do horário de atendimento

PRODUTOS E SERVIÇOS:
- Uniformes corporativos (polo, camisetas, jalecos, aventais)
- Bordados e personalizações
- Uniformes escolares
- EPIs (Equipamentos de Proteção Individual)

REGRAS COMERCIAIS:
- Pedido mínimo: 80 peças
- Prazo médio: 30 dias úteis
- Para orçamento: modelo + quantidade + CNPJ
- Contratos anuais: melhores condições
- Setor educação: as escolas podem ser atenditas tanto com a venda direta quando com a Difarda atendendo diretamente aos pais

QUANDO NÃO SOUBER:
- NUNCA invente preços, prazos ou disponibilidade
- Responda: "Ótima pergunta! Deixa eu verificar com minha equipe e já te retorno, ok?"
"""

# ========== FUNÇÕES DE LOG ==========

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")
    with open(ARQUIVO_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")

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

# ========== FUNÇÕES DE GERENCIAMENTO DE CHAMADOS ==========

def carregar_chamados():
    """Carrega chamados ativos do arquivo"""
    global chamados_ativos
    try:
        with open(ARQUIVO_CHAMADOS, "r", encoding="utf-8") as f:
            chamados_ativos = json.load(f)
            ativos = sum(1 for c in chamados_ativos.values() if c["status"] != STATUS_ENCERRADO)
            log(f"✅ {ativos} chamado(s) ativo(s)")
    except:
        chamados_ativos = {}

def salvar_chamados():
    """Salva chamados ativos no arquivo"""
    with open(ARQUIVO_CHAMADOS, "w", encoding="utf-8") as f:
        json.dump(chamados_ativos, f, indent=2, ensure_ascii=False)

def criar_chamado(contact_id, nome):
    """Cria um novo chamado"""
    agora = datetime.now().isoformat()
    chamados_ativos[contact_id] = {
        "status": STATUS_BOT_ATIVO,
        "inicio": agora,
        "ultima_interacao": agora,
        "tipo_contato": None,  # Será classificado na primeira mensagem
        "nome": nome,
        "resolvido": False,
        "mensagens_count": 0
    }
    salvar_chamados()
    log(f"🆕 Novo chamado criado: {nome} ({contact_id[:8]}...)")

def atualizar_ultima_interacao(contact_id):
    """Atualiza timestamp da última interação"""
    if contact_id in chamados_ativos:
        chamados_ativos[contact_id]["ultima_interacao"] = datetime.now().isoformat()
        chamados_ativos[contact_id]["mensagens_count"] += 1
        salvar_chamados()

def marcar_como_resolvido(contact_id):
    """Marca chamado como resolvido"""
    if contact_id in chamados_ativos:
        chamados_ativos[contact_id]["resolvido"] = True
        chamados_ativos[contact_id]["status"] = STATUS_AGUARDANDO
        salvar_chamados()
        log(f"✅ Chamado resolvido: {contact_id[:8]}... (aguardando timeout)")

def pausar_bot(contact_id):
    """Pausa bot quando atendente humano assume"""
    if contact_id in chamados_ativos:
        chamados_ativos[contact_id]["status"] = STATUS_ATENDENTE
        salvar_chamados()
        log(f"⏸️ Bot pausado: Atendente assumiu {contact_id[:8]}...")

def encerrar_chamado(contact_id):
    """Encerra um chamado"""
    if contact_id in chamados_ativos:
        chamados_ativos[contact_id]["status"] = STATUS_ENCERRADO
        salvar_chamados()
        log(f"🔒 Chamado encerrado: {contact_id[:8]}...")

def verificar_atendente_humano(webhook_data):
    """Verifica se um atendente humano assumiu o chamado"""
    data = webhook_data.get('data', {})
    user_id = data.get('userId')
    is_from_bot = data.get('isFromBot', False)
    
    # Se tem userId e não é do bot = atendente humano
    if user_id and not is_from_bot:
        log(f"👤 Atendente humano detectado: {user_id}")
        return True
    return False


def transferir_para_gestao_pedidos(contact_id, nome):
    """Transfere contato para departamento de Gestão de Pedidos via API Digisac"""
    log(f"🔄 Transferindo {nome} para Gestão de Pedidos...")
    
    # Se não tiver ID do departamento, apenas pausar bot
    if not DEPARTAMENTO_GESTAO_ID:
        log(f"⚠️ DEPARTAMENTO_GESTAO_ID não configurado! Bot pausado manualmente.")
        if contact_id in chamados_ativos:
            chamados_ativos[contact_id]["status"] = STATUS_ATENDENTE
            salvar_chamados()
        return False
    
    try:
        url = f"{DIGISAC_URL}/api/v1/contacts/{contact_id}/ticket/transfer"
        
        headers = {
            "Authorization": f"Bearer {DIGISAC_TOKEN}",
            "Content-Type": "application/json"
        }
        
        body = {
            "departmentId": DEPARTAMENTO_GESTAO_ID,
            "userId": None  # Sem usuário específico
        }
        
        log(f"🔗 POST {url}")
        resp = requests.post(url, json=body, headers=headers, timeout=15)
        log(f"📊 Status: {resp.status_code}")
        
        if resp.status_code == 200:
            log(f"✅ Transferência realizada com sucesso!")
            # Pausar bot
            if contact_id in chamados_ativos:
                chamados_ativos[contact_id]["status"] = STATUS_ATENDENTE
                salvar_chamados()
            return True
        else:
            log(f"❌ Erro na transferência: {resp.text}")
            # Pausar bot mesmo assim
            if contact_id in chamados_ativos:
                chamados_ativos[contact_id]["status"] = STATUS_ATENDENTE
                salvar_chamados()
            return False
            
    except Exception as e:
        log(f"❌ Erro ao transferir: {e}")
        # Pausar bot mesmo com erro
        if contact_id in chamados_ativos:
            chamados_ativos[contact_id]["status"] = STATUS_ATENDENTE
            salvar_chamados()
        return False


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
        
        # ✅ VERIFICAR ÁREA PRIMEIRO (antes de filtrar evento)
        data = dados.get('data', {})
        ticket_user_id = data.get('ticketUserId')
        
        if ticket_user_id:
            # Tem atendente = Chat = Bot NÃO atua
            contact_id = data.get('contactId', '')
            log(f"⏸️ Chamado no Chat (atendente: {ticket_user_id}) - Bot não atua")
            return jsonify({"status": "chat_area"}), 200
        
        log(f"✅ Chamado na Fila/Contatos - Bot atua")
        
        # Verificar tipo de evento
        evento = dados.get('event', '')
        if evento != 'message.created':
            log(f"⏭️ Evento '{evento}' ignorado")
            return jsonify({"status": "ignored"}), 200
        
        # Extrair informações da mensagem
        mensagem_texto = data.get('text', '')
        contact_id = data.get('contactId', '')
        tipo_mensagem = data.get('type', 'chat')
        
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
        
        # ===== SISTEMA DE GERENCIAMENTO DE CHAMADOS =====
        
        # 1. Verificar se atendente humano assumiu
        if verificar_atendente_humano(dados):
            pausar_bot(contact_id)
            log("⏸️ Bot pausado - Atendente assumiu")
            return jsonify({"status": "atendente_assumiu"}), 200
        
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
        
        # 2. Verificar se chamado existe
        if contact_id not in chamados_ativos:
            # Criar novo chamado
            criar_chamado(contact_id, contact_name)
            
            # Tipo de contato será identificado pelo agente na conversa
            chamados_ativos[contact_id]["tipo_contato"] = TIPO_CLIENTE  # Padrão
            salvar_chamados()
        
        # 5. Verificar se bot está pausado (atendente humano)
        chamado = chamados_ativos.get(contact_id, {})
        if chamado.get("status") == STATUS_ATENDENTE:
            # Verificar se ainda tem atendente ativo
            if verificar_atendente_humano(dados):
                log("⏸️ Bot pausado - Atendente está atendendo")
                return jsonify({"status": "atendente_ativo"}), 200
            else:
                # Não tem atendente, reativar bot
                log("🔄 Nenhum atendente ativo - Reativando bot")
                chamados_ativos[contact_id]["status"] = STATUS_BOT_ATIVO
                salvar_chamados()
        
        # 6. Se chamado foi encerrado, criar novo
        if chamado.get("status") == STATUS_ENCERRADO:
            log("🔄 Chamado encerrado - Criando novo")
            criar_chamado(contact_id, contact_name)
            
            # Tipo de contato será identificado pelo agente na conversa
            chamados_ativos[contact_id]["tipo_contato"] = TIPO_CLIENTE  # Padrão
            salvar_chamados()
        
        # Adicionar mensagem do cliente ao histórico
        adicionar_ao_historico(contact_id, "cliente", mensagem_texto)
        
        # 7. Processar com Manus (com ou sem imagem)
        resposta = perguntar_manus(mensagem_texto, contact_name, contact_id, imagem_url)
        
        # Adicionar resposta ao histórico
        adicionar_ao_historico(contact_id, "assistente", resposta)
        
        # Verificar se resposta indica transferência
        palavras_transferencia = [
            "transferir você para",
            "equipe de gestão",
            "nossa equipe",
            "não é um cliente"
        ]
        
        resposta_lower = resposta.lower()
        deve_transferir = any(palavra in resposta_lower for palavra in palavras_transferencia)
        
        if deve_transferir:
            log(f"🔄 Transferência detectada na resposta")
            # Enviar mensagem de transferência
            if enviar_mensagem_digisac(contact_id, resposta):
                log(f"✅ Mensagem de transferência enviada: {contact_name}")
            # Transferir via API Digisac
            transferir_para_gestao_pedidos(contact_id, contact_name)
            return jsonify({"status": "transferido"}), 200
        
        # 8. Atualizar última interação
        atualizar_ultima_interacao(contact_id)
        
        # Enviar resposta via Digisac
        if enviar_mensagem_digisac(contact_id, resposta):
            log(f"✅ Resposta enviada: {contact_name}")
        else:
            return jsonify({"status": "send_failed"}), 500
        
        # Marcar como processada
        mensagens_processadas.add(msg_id)
        
        log(f"✅ Processamento completo: {contact_name}")
        return jsonify({"status": "success"}), 200
            
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

# ========== THREAD DE TIMEOUT ==========

import threading

def verificar_timeouts_background():
    """Thread que verifica timeouts de chamados a cada minuto"""
    while True:
        try:
            time.sleep(60)  # Verifica a cada 1 minuto
            
            agora = datetime.now()
            chamados_para_encerrar = []
            
            for contact_id, chamado in chamados_ativos.items():
                # Ignorar chamados já encerrados
                if chamado["status"] == STATUS_ENCERRADO:
                    continue
                
                # Ignorar se atendente está atendendo
                if chamado["status"] == STATUS_ATENDENTE:
                    continue
                
                # Calcular tempo de inatividade
                ultima_interacao = datetime.fromisoformat(chamado["ultima_interacao"])
                tempo_inativo = (agora - ultima_interacao).total_seconds()
                
                # Se passou 45 minutos sem interação
                if tempo_inativo > TIMEOUT_INATIVIDADE:
                    chamados_para_encerrar.append(contact_id)
            
            # Encerrar chamados inativos
            for contact_id in chamados_para_encerrar:
                log(f"⏰ Timeout: Encerrando chamado {contact_id[:8]}... (45min sem interação)")
                encerrar_chamado(contact_id)
        
        except Exception as e:
            log(f"❌ Erro na thread de timeout: {e}")

# ========== INICIALIZAÇÃO ==========

if __name__ == "__main__":
    print("="*70)
    print("🤖 AGENTE DIGISAC + MANUS AI")
    print("="*70)
    print()
    
    carregar_tasks()
    carregar_perguntas()
    carregar_chamados()
    
    # Iniciar thread de timeout
    thread_timeout = threading.Thread(target=verificar_timeouts_background, daemon=True)
    thread_timeout.start()
    log("⏰ Thread de timeout iniciada (verificação a cada 1 minuto)")
    
    log("="*70)
    log("🚀 SISTEMA INICIADO")
    log("="*70)
    log(f"📡 Webhook: http://localhost:5000/webhook")
    log(f"❤️ Health: http://localhost:5000/health")
    log(f"👨‍💼 Gestor: http://localhost:5000/gestor")
    log(f"⏰ Timeout: 45 minutos de inatividade")
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
    
    app.run(host='0.0.0.0', port=5000, debug=False)
