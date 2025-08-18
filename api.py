import os
import re
import json
import requests
import asyncio
import logging
import google.generativeai as genai
from google.cloud import vision
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# --- Configuração de Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURAÇÃO DAS CHAVES E APIs ---
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credenciais.json'
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
WORDPRESS_URL = os.environ.get('WORDPRESS_URL')
WORDPRESS_API_SECRET = os.environ.get('WORDPRESS_API_SECRET')

# Inicialização dos clientes de IA
genai.configure(api_key=GEMINI_API_KEY)
vision_client = vision.ImageAnnotatorClient()
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

# --- FUNÇÃO DE ANÁLISE DE VAGA (A mesma de antes) ---
def extrair_dados_vaga_com_ia(texto_completo):
    prompt = f"""
    Você é um assistente de RH especialista em analisar anúncios de vagas.
    Analise o texto de anúncio de vaga de emprego a seguir e extraia as seguintes informações em um formato JSON.
    As chaves do JSON devem ser exatamente: "nome_empresa", "whatsapp", "email", "beneficios", "requisitos", "nome_cargo", "horario_trabalho", "atividades", "carga_horaria".

    Regras de Extração Específicas:
    - "whatsapp": Encontre QUALQUER número de telefone no texto e classifique-o como WhatsApp.
    - "email": Encontre QUALQUER texto que se pareça com um endereço de e-mail (ex: nome@exemplo.com).
    - "beneficios", "requisitos", "atividades": Retorne uma lista (array) de strings. Se não encontrar, retorne uma lista vazia [].
    - Para todos os outros campos de string: Se a informação não for encontrada, o valor deve ser "não informado".

    Texto da Vaga:
    ---
    {texto_completo}
    ---
    """
    try:
        response = gemini_model.generate_content(prompt)
        cleaned_text = re.sub(r'```json\n|\n```', '', response.text).strip()
        dados = json.loads(cleaned_text)
        dados['texto_completo'] = texto_completo
        return dados
    except Exception as e:
        return {'erro': f"Erro ao processar com a IA: {str(e)}", 'texto_completo': texto_completo}

# --- FUNÇÕES DO BOT DO TELEGRAM ---

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Olá! Sou seu assistente de vagas. Envie-me uma imagem de uma vaga de emprego para que eu possa analisá-la e salvá-la no WordPress. Use /validar para ver as vagas pendentes.')

# Função para receber e processar a imagem
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Recebi sua imagem. Analisando com a IA, por favor aguarde...')
    
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    
    # 1. Análise com Google Vision
    imagem = vision.Image(content=bytes(photo_bytes))
    response = vision_client.text_detection(image=imagem)
    if not response.text_annotations:
        await update.message.reply_text('Não consegui encontrar nenhum texto na imagem.')
        return
    
    texto_completo = response.text_annotations[0].description
    
    # 2. Extração com Gemini
    dados_extraidos = extrair_dados_vaga_com_ia(texto_completo)
    if 'erro' in dados_extraidos:
        await update.message.reply_text(f"Ocorreu um erro na análise:\n{dados_extraidos['erro']}")
        return

    # 3. Envio para o WordPress
    wp_endpoint = f"{WORDPRESS_URL}/wp-json/vagasbot/v1/submit_vaga"
    headers = {
        'Authorization': f'Bearer {WORDPRESS_API_SECRET}',
        'Content-Type': 'application/json'
    }
    
    try:
        wp_response = requests.post(wp_endpoint, headers=headers, json=dados_extraidos, timeout=30)
        wp_response.raise_for_status() # Lança um erro se o status for 4xx ou 5xx
        
        response_data = wp_response.json()
        vaga_id = response_data.get('vaga_id')
        await update.message.reply_text(f"✅ Sucesso! A vaga '{dados_extraidos.get('nome_cargo')}' foi salva no WordPress com o ID {vaga_id} e está aguardando sua validação.")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao enviar para o WordPress: {e}")
        await update.message.reply_text(f"Não consegui salvar no WordPress. Erro: {e}\nResposta do servidor: {e.response.text if e.response else 'N/A'}")

# Comando /validar
async def validate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wp_endpoint = f"{WORDPRESS_URL}/wp-json/vagasbot/v1/pending_vagas"
    headers = {'Authorization': f'Bearer {WORDPRESS_API_SECRET}'}
    
    try:
        response = requests.get(wp_endpoint, headers=headers, timeout=20)
        response.raise_for_status()
        vagas = response.json()
        
        if not vagas:
            await update.message.reply_text("Nenhuma vaga pendente para validação no momento.")
            return

        await update.message.reply_text(f"Encontrei {len(vagas)} vaga(s) para validar:")

        for vaga in vagas:
            dados = vaga['dados']
            # Formata a mensagem
            texto_vaga = f"**ID da Vaga:** {vaga['id']}\n"
            texto_vaga += f"**Cargo:** {dados.get('nome_cargo', 'N/A')}\n"
            texto_vaga += f"**Local:** {dados.get('local', 'N/A')}\n"
            texto_vaga += f"**Salário:** {dados.get('salario', 'N/A')}\n\n"
            texto_vaga += "**Requisitos:**\n" + "\n".join(f"- {r}" for r in dados.get('requisitos', []))

            # Cria os botões Sim/Não
            keyboard = [
                [
                    InlineKeyboardButton("✅ Aprovar", callback_data=f"approve_{vaga['id']}"),
                    InlineKeyboardButton("❌ Reprovar", callback_data=f"reject_{vaga['id']}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(texto_vaga, reply_markup=reply_markup, parse_mode='Markdown')
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao buscar vagas pendentes: {e}")
        await update.message.reply_text(f"Não consegui buscar as vagas no WordPress. Erro: {e}")

# Função para lidar com os cliques nos botões
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer() # Responde ao clique para o Telegram saber que foi recebido

    action, vaga_id = query.data.split('_')
    aprovado = True if action == 'approve' else False
    
    wp_endpoint = f"{WORDPRESS_URL}/wp-json/vagasbot/v1/validate_vaga"
    headers = {'Authorization': f'Bearer {WORDPRESS_API_SECRET}', 'Content-Type': 'application/json'}
    payload = {'vaga_id': int(vaga_id), 'aprovado': aprovado}
    
    try:
        response = requests.post(wp_endpoint, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        
        message = response.json().get('message', 'Ação concluída.')
        await query.edit_message_text(text=f"{query.message.text}\n\n--- **AÇÃO REALIZADA:** {message} ---")
        
    except requests.exceptions.RequestException as e:
        await query.edit_message_text(text=f"Ocorreu um erro ao validar a vaga: {e}")

# --- FUNÇÃO PRINCIPAL QUE RODA O BOT ---
def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("validar", validate_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_handler))

    # Inicia o bot
    # No Render, ele vai rodar continuamente. Para testes locais, você pode parar com Ctrl+C
    print("Bot iniciado e aguardando mensagens...")
    application.run_polling()

if __name__ == '__main__':
    main()
