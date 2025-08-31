import os
import re
import json
import requests
import asyncio
import logging
import time
from io import BytesIO
from PIL import Image

# --- Bibliotecas de Automação Web ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# --- Bibliotecas de IA (as mesmas que você já usa) ---
import google.generativeai as genai
from google.cloud import vision

# --- Configuração de Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURAÇÃO DAS CHAVES E APIs ---
# É uma boa prática carregar as variáveis de ambiente no início
# from dotenv import load_dotenv
# load_dotenv() # Se você usar um arquivo .env

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credenciais.json'
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN') # Não é mais usado, mas mantido por consistência
WORDPRESS_URL = os.environ.get('WORDPRESS_URL')
WORDPRESS_API_SECRET = os.environ.get('WORDPRESS_API_SECRET')

# --- CONFIGURAÇÕES DO WHATSAPP ---
# Coloque o nome EXATO do grupo que o bot irá monitorar
NOME_DO_GRUPO_ALVO = "NOME DO SEU GRUPO AQUI" 
# Diretório para salvar os dados da sessão do Chrome e evitar escanear o QR Code toda vez
CHROME_PROFILE_PATH = "user-data-dir=./chrome_profile"

# --- Inicialização dos clientes de IA ---
genai.configure(api_key=GEMINI_API_KEY)
vision_client = vision.ImageAnnotatorClient()
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

# --- FUNÇÃO DE ANÁLISE DE VAGA (Inalterada) ---
def extrair_dados_vaga_com_ia(texto_completo):
    """
    Analisa o texto de uma vaga usando o modelo Gemini e extrai informações estruturadas.
    """
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
        logger.error(f"Erro ao processar com a IA Gemini: {e}")
        return {'erro': f"Erro ao processar com a IA: {str(e)}", 'texto_completo': texto_completo}

# --- NOVAS FUNÇÕES DO BOT DO WHATSAPP ---

class WhatsAppBot:
    """
    Classe que encapsula toda a lógica de automação do WhatsApp.
    """
    def __init__(self, group_name):
        self.group_name = group_name
        
        # Configura as opções do Chrome para manter a sessão
        chrome_options = Options()
        chrome_options.add_argument(CHROME_PROFILE_PATH)
        
        # Inicializa o driver do Selenium
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        self.wait = WebDriverWait(self.driver, 20)
        self.latest_message_id = None # Para controlar a última mensagem lida

    def connect_to_whatsapp(self):
        """
        Abre o WhatsApp Web e aguarda o login do usuário (escaneamento do QR Code).
        """
        self.driver.get("https://web.whatsapp.com")
        logger.info("Por favor, escaneie o QR Code para conectar ao WhatsApp.")
        # Espera até que o painel lateral de conversas esteja visível, indicando que o login foi feito.
        self.wait.until(EC.presence_of_element_located((By.ID, "side")))
        logger.info("Conectado ao WhatsApp com sucesso!")

    def select_group(self):
        """
        Encontra e clica no grupo alvo na lista de conversas.
        """
        try:
            group_xpath = f"//span[@title='{self.group_name}']"
            group_element = self.wait.until(EC.presence_of_element_located((By.XPATH, group_xpath)))
            group_element.click()
            logger.info(f"Grupo '{self.group_name}' selecionado.")
            time.sleep(2) # Pequena pausa para a conversa carregar
        except Exception as e:
            logger.error(f"Não foi possível encontrar ou selecionar o grupo '{self.group_name}'. Verifique o nome. Erro: {e}")
            self.driver.quit()
            exit()

    def send_message_to_group(self, message):
        """
        Envia uma mensagem de texto para o grupo atualmente aberto.
        """
        try:
            # Localiza a caixa de entrada de texto do WhatsApp
            input_box_xpath = '//*[@id="main"]/footer/div[1]/div/span[2]/div/div[2]/div[1]/div/div[1]/p'
            input_box = self.wait.until(EC.presence_of_element_located((By.XPATH, input_box_xpath)))
            
            # Digita a mensagem e envia
            input_box.send_keys(message)
            self.driver.find_element(By.XPATH, '//*[@id="main"]/footer/div[1]/div/span[2]/div/div[2]/div[2]/button/span').click()
            logger.info(f"Mensagem enviada para o grupo: {message}")
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem: {e}")

    def process_new_image(self, image_element):
        """
        Processa uma nova imagem encontrada no grupo.
        """
        self.send_message_to_group("Recebi uma nova imagem. Analisando com a IA, por favor aguarde...")
        
        try:
            # O Selenium captura a imagem como um screenshot do elemento
            image_bytes = image_element.screenshot_as_png
            
            # 1. Análise com Google Vision
            imagem_vision = vision.Image(content=image_bytes)
            response_vision = vision_client.text_detection(image=imagem_vision)
            
            if not response_vision.text_annotations:
                self.send_message_to_group("Não consegui encontrar nenhum texto na imagem.")
                return

            texto_completo = response_vision.text_annotations[0].description
            
            # 2. Extração com Gemini
            dados_extraidos = extrair_dados_vaga_com_ia(texto_completo)
            if 'erro' in dados_extraidos:
                self.send_message_to_group(f"Ocorreu um erro na análise:\n{dados_extraidos['erro']}")
                return

            # 3. Envio para o WordPress
            wp_endpoint = f"{WORDPRESS_URL}/wp-json/vagasbot/v1/submit_vaga"
            headers = {
                'Authorization': f'Bearer {WORDPRESS_API_SECRET}',
                'Content-Type': 'application/json'
            }
            
            wp_response = requests.post(wp_endpoint, headers=headers, json=dados_extraidos, timeout=30)
            wp_response.raise_for_status() # Lança um erro se o status for 4xx ou 5xx
            
            response_data = wp_response.json()
            vaga_id = response_data.get('vaga_id')
            
            mensagem_sucesso = f"✅ Sucesso! A vaga '{dados_extraidos.get('nome_cargo', 'N/A')}' foi salva no WordPress com o ID {vaga_id} e está aguardando validação. Use `!validar` para ver as vagas pendentes."
            self.send_message_to_group(mensagem_sucesso)

        except requests.exceptions.RequestException as e:
            logger.error(f"Erro ao enviar para o WordPress: {e}")
            self.send_message_to_group(f"Não consegui salvar no WordPress. Erro: {e}\nResposta do servidor: {e.response.text if e.response else 'N/A'}")
        except Exception as e:
            logger.error(f"Erro inesperado ao processar imagem: {e}")
            self.send_message_to_group(f"Ocorreu um erro inesperado ao processar a imagem. Detalhes: {str(e)}")

    def listen_for_messages(self):
        """
        Loop principal que monitora o grupo por novas imagens e comandos.
        """
        logger.info("Iniciando monitoramento do grupo...")
        last_processed_message_element = None
        
        while True:
            try:
                # Encontra todas as mensagens na conversa
                all_messages = self.driver.find_elements(By.CSS_SELECTOR, ".message-in, .message-out")
                if not all_messages:
                    time.sleep(5)
                    continue

                latest_message = all_messages[-1]
                
                # Se a última mensagem já foi processada, espera um pouco
                if latest_message == last_processed_message_element:
                    time.sleep(3)
                    continue
                
                last_processed_message_element = latest_message

                # Tenta encontrar uma imagem dentro da última mensagem
                try:
                    # O seletor busca por elementos de imagem que não sejam figurinhas (stickers)
                    image_element = latest_message.find_element(By.CSS_SELECTOR, "img[src^='blob:']")
                    self.process_new_image(image_element)
                except:
                    # Se não for uma imagem, verifica se é um comando
                    try:
                        message_text = latest_message.find_element(By.CSS_SELECTOR, ".copyable-text").text.strip()
                        self.handle_command(message_text)
                    except:
                        # Não é nem imagem nem texto de comando, ignora
                        pass

            except Exception as e:
                logger.error(f"Erro no loop de monitoramento: {e}")
                time.sleep(10) # Espera um pouco antes de tentar novamente

    def handle_command(self, text):
        """
        Processa comandos de texto recebidos no grupo.
        """
        if text == "!validar":
            self.validate_command()
        elif text.startswith("!aprovar"):
            try:
                vaga_id = int(text.split(" ")[1])
                self.handle_validation(vaga_id, True)
            except (IndexError, ValueError):
                self.send_message_to_group("Comando inválido. Use: `!aprovar ID_DA_VAGA` (ex: !aprovar 123)")
        elif text.startswith("!reprovar"):
            try:
                vaga_id = int(text.split(" ")[1])
                self.handle_validation(vaga_id, False)
            except (IndexError, ValueError):
                self.send_message_to_group("Comando inválido. Use: `!reprovar ID_DA_VAGA` (ex: !reprovar 123)")

    def validate_command(self):
        """
        Busca vagas pendentes no WordPress e as envia para o grupo.
        """
        wp_endpoint = f"{WORDPRESS_URL}/wp-json/vagasbot/v1/pending_vagas"
        headers = {'Authorization': f'Bearer {WORDPRESS_API_SECRET}'}
        
        try:
            response = requests.get(wp_endpoint, headers=headers, timeout=20)
            response.raise_for_status()
            vagas = response.json()
            
            if not vagas:
                self.send_message_to_group("Nenhuma vaga pendente para validação no momento.")
                return

            self.send_message_to_group(f"Encontrei {len(vagas)} vaga(s) para validar:")
            
            for vaga in vagas:
                dados = vaga['dados']
                texto_vaga = (
                    f"--- VAGA PENDENTE ---\n"
                    f"*ID da Vaga:* {vaga['id']}\n"
                    f"*Cargo:* {dados.get('nome_cargo', 'N/A')}\n"
                    f"*Empresa:* {dados.get('nome_empresa', 'N/A')}\n\n"
                    f"Para aprovar, digite: `!aprovar {vaga['id']}`\n"
                    f"Para reprovar, digite: `!reprovar {vaga['id']}`"
                )
                self.send_message_to_group(texto_vaga)

        except requests.exceptions.RequestException as e:
            logger.error(f"Erro ao buscar vagas pendentes: {e}")
            self.send_message_to_group(f"Não consegui buscar as vagas no WordPress. Erro: {e}")

    def handle_validation(self, vaga_id, is_approved):
        """
        Envia a decisão de validação (aprovar/reprovar) para o WordPress.
        """
        wp_endpoint = f"{WORDPRESS_URL}/wp-json/vagasbot/v1/validate_vaga"
        headers = {'Authorization': f'Bearer {WORDPRESS_API_SECRET}', 'Content-Type': 'application/json'}
        payload = {'vaga_id': int(vaga_id), 'aprovado': is_approved}
        
        try:
            response = requests.post(wp_endpoint, headers=headers, json=payload, timeout=20)
            response.raise_for_status()
            
            message = response.json().get('message', 'Ação concluída.')
            self.send_message_to_group(f"Ação realizada para a vaga ID {vaga_id}: {message}")
            
        except requests.exceptions.RequestException as e:
            self.send_message_to_group(f"Ocorreu um erro ao validar a vaga ID {vaga_id}: {e}")

# --- FUNÇÃO PRINCIPAL QUE RODA O BOT ---
def main():
    """
    Função principal para inicializar e rodar o bot.
    """
    bot = WhatsAppBot(NOME_DO_GRUPO_ALVO)
    bot.connect_to_whatsapp()
    bot.select_group()
    bot.listen_for_messages()

if __name__ == '__main__':
    main()
