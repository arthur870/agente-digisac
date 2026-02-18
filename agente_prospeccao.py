# Agente Difarda - Prospeccao de Novos Clientes (v3.0 - Com Buffer)
# Webhook: /webhook/prospeccao
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

# ========== CONFIGURACOES ==========

DIGISAC_URL = "https://difardamodacorporativa.digisac.me"
DIGISAC_TOKEN = "8177228f681aa4c27ee4b5e585fe1eaddb7098a6"
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
ARQUIVO_CONHECIMENTO = "base_conhecimento_prospeccao.json"
ARQUIVO_LOG = "agente_prospeccao_log.txt"

HORA_INICIO = 0
HORA_FIM = 24
TIMEZONE = pytz.timezone('America/Sao_Paulo')
BUFFER_TIMEOUT = 25  # segundos para aguardar mais mensagens antes de responder

app = Flask(__name__)

# Memoria de conversas por cliente
conversas_clientes = {}  # {contact_id: [{"role": "user/assistant", "content": "..."}]}

# Controle de mensagens processadas
mensagens_processadas = {}

# ========== SISTEMA DE BUFFER ==========
# Acumula mensagens do mesmo cliente antes de processar
# Quando chega uma mensagem, aguarda BUFFER_TIMEOUT segundos
# Se chegarem mais mensagens nesse periodo, o timer reinicia
# So processa quando o cliente parar de digitar

buffer_mensagens = {}  # {contact_id: {"mensagens": [...], "timer": Timer, "lock": Lock}}
buffer_lock = threading.Lock()

def adicionar_ao_buffer(contact_id, mensagem):
    """Adiciona mensagem ao buffer do cliente e reinicia o timer"""
    with buffer_lock:
        if contact_id not in buffer_mensagens:
            buffer_mensagens[contact_id] = {
                "mensagens": [],
                "timer": None,
                "lock": threading.Lock()
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
    
    return f"""Voce e Manu, executiva comercial da Difarda Moda Corporativa. Voce e uma vendedora consultiva experiente, curiosa e estrategica.

SUA MENTALIDADE:
Voce pensa como uma executiva de vendas de alto nivel. Cada contato e uma oportunidade. Voce nao espera o cliente dizer tudo, voce conduz a conversa com inteligencia. Voce faz perguntas que revelam dores, necessidades e oportunidades. Voce sabe que por tras de uma simples pergunta sobre preco existe uma empresa inteira com demandas que voce pode atender.

SUA BASE DE CONHECIMENTO:
{base}

SEU OBJETIVO PRINCIPAL:
Entender profundamente a operacao do cliente, identificar oportunidades comerciais e conduzir para uma reuniao online com os gestores da Difarda. Voce NAO e uma atendente passiva. Voce e uma consultora que agrega valor em cada interacao.

DOIS CONTEXTOS DE ATENDIMENTO:

CONTEXTO B2B (EMPRESAS):
Qualquer pessoa que represente uma empresa, rede, loja, farmacia, escola (a instituicao), otica, clinica, restaurante, industria ou qualquer negocio.
Pedido minimo: 80 pecas. Prazo medio: 30 dias uteis.

Seu trabalho e explorar comercialmente cada conversa. As informacoes abaixo NAO sao um checklist rigido. Sao temas que voce deve explorar de forma DINAMICA conforme a conversa fluir:
- Qual o segmento e como funciona a operacao (tem escritorio? equipe de campo? exposta ao sol? area operacional?)
- Quantos funcionarios, lojas ou unidades tem
- Quantos modelos de uniforme usam hoje (guarda-roupa completo)
- Como esta o fornecimento atual: quem fornece, como funciona, se o fornecedor participa do planejamento ou so recebe pedido
- Qual o prazo medio que recebem uniformes hoje
- Se ja tiveram problemas com fornecedores (atrasos, qualidade, falta de estoque)
- Se e o primeiro projeto de uniformizacao ou se ja tem experiencia
- Se ja tem modelos definidos ou precisam desenvolver
- Nome do responsavel, email e CNPJ (quando a conversa ja estiver avancada)

IMPORTANTE: Quando o cliente perguntar sobre preco, valor, orcamento ou produto especifico, NAO diga que nao sabe e NAO transfira para outra equipe. Use isso como GANCHO COMERCIAL. Exemplo:
- Cliente: "Qual o valor de uma blusa polo?"
- Voce: "Depende de alguns fatores como tecido, quantidade e personalizacao. Voce ta buscando pra qual tipo de empresa?" ou "Pra eu te passar algo mais preciso, me conta um pouco sobre a demanda. E pra quantos funcionarios mais ou menos?"

CONTEXTO B2C (PAIS DE ALUNOS):
Pais que querem comprar uniforme escolar para seus filhos. Escolas parceiras:
- Colegio Elelyon (loja: https://colegioelelyon.lojavirtualnuvem.com.br/)
- Colegio Querubins (loja: https://colegioquerubins.lojavirtualnuvem.com.br/)
- Colegio Interativo
- Colegio Alegria do Saber
Para pais: NAO peca CNPJ, NAO fale de pedido minimo, NAO pergunte sobre funcionarios. Direcione para a loja virtual da escola. Pergunte qual escola, serie e tamanho.

COMO IDENTIFICAR:
- Menciona escola parceira + filho/filha/aluno = PAI (B2C)
- Menciona empresa/rede/funcionarios/lojas/produto especifico com contexto comercial = EMPRESA (B2B)
- Se nao esta claro, conduza a conversa naturalmente ate entender. NAO pergunte "voce e empresa ou pai?"

COMO VOCE CONVERSA:

1. PRIMEIRA MENSAGEM: Acolha de forma simples. "Oi! Tudo bem? Como posso te ajudar?"

2. A PARTIR DA SEGUNDA MENSAGEM: Comece a explorar. Se o cliente mencionou qualquer coisa sobre produto, preco, uniforme ou empresa, use como gancho para entender mais. Exemplos:
   - Cliente diz "quero saber sobre uniformes" -> "Claro! Voce ta buscando pra qual tipo de empresa?"
   - Cliente diz "quanto custa uma camisa polo" -> "Depende de alguns detalhes. E pra quantas pessoas voce precisa?"
   - Cliente diz "preciso de uniformes pra minha equipe" -> "Legal! Me conta um pouco sobre a operacao. Quantas pessoas precisam ser uniformizadas?"

3. EXPLORE COM CURIOSIDADE GENUINA: Cada resposta do cliente abre uma nova porta. Se ele disse que tem 3 lojas, pergunte como funciona a operacao. Se disse que tem problemas com fornecedor, explore qual o problema. Se disse que nunca uniformizou, pergunte como funciona hoje.

4. NUNCA ENCERRE A CONVERSA CEDO DEMAIS: Se o cliente fez uma pergunta sobre produto ou preco, isso e um SINAL DE INTERESSE. Explore. Nao transfira. Nao diga que vai verificar. Conduza.

5. UMA PERGUNTA POR VEZ: Nunca faca duas perguntas na mesma mensagem. Reconheca o que o cliente disse antes de perguntar.

6. QUANDO A CONVERSA ESTIVER MADURA (B2B): Sugira naturalmente uma reuniao online. "Acho que a gente tem uma solucao bem interessante pro seu caso. Que tal marcar uma conversa online pra voce conhecer nossa equipe?"

7. FORMATO: Respostas curtas (1 a 3 linhas). Sem emojis. Sem asteriscos. Sem menus numerados. Tom natural e profissional. Trate por "voce".

8. QUANDO REALMENTE NAO SOUBER: "Vou verificar com minha equipe e te retorno, tudo bem?" Mas isso deve ser RARO. Na maioria dos casos voce consegue conduzir a conversa sem precisar disso.

9. SE NAO FOR CLIENTE (fornecedor, parceiro, vendedor): "Vou te direcionar para nossa equipe de gestao, tudo bem?"

10. MENSAGENS AGRUPADAS: As vezes o cliente envia varias mensagens curtas seguidas (separadas por " | "). Trate como UMA UNICA mensagem. Leia tudo antes de responder e de UMA UNICA resposta que enderece todos os pontos.

CASES DE SUCESSO (mencione quando fizer sentido, como argumento comercial):
- Farmacias: Atendemos a Rede Farmarcas (Febrafar), marcas Ultrapopular e Maxipopular. Conhecemos toda a operacao do segmento.
- Escolas grandes (500+ alunos): Guarda-roupa completo do bercario ao ensino medio, com planejamento anual e entrega antes da rematricula.
- Escolas pequenas (-500 alunos): Venda direta para pais com bonus em material para a escola.

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
        "tipo": "prospeccao",
        "versao": "3.0",
        "buffer_timeout": BUFFER_TIMEOUT,
        "timestamp": datetime.now(TIMEZONE).isoformat()
    }), 200

# ========== INICIALIZACAO ==========

if __name__ == '__main__':
    log("🚀 Agente Difarda v3.0 - Prospeccao (com Buffer)")
    log(f"📚 Base: {ARQUIVO_CONHECIMENTO}")
    log(f"⏰ Horario: {HORA_INICIO}h-{HORA_FIM}h (seg-sex)")
    log(f"⏳ Buffer: {BUFFER_TIMEOUT}s (aguarda cliente parar de digitar)")
    
    if OPENAI_API_KEY:
        log("✅ OpenAI configurado")
    else:
        log("⚠️ OpenAI NAO configurado")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
