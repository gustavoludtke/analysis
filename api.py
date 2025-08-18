import os
import re # Usaremos re para uma limpeza final
import google.generativeai as genai
from flask import Flask, request, jsonify
from google.cloud import vision

# --- CONFIGURAÇÃO ---
# Chave da Vision API (via Secret File)
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credenciais.json'

# Chave da Gemini API (via Environment Variable)
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)

app = Flask(__name__)
vision_client = vision.ImageAnnotatorClient()
gemini_model = genai.GenerativeModel('gemini-1.5-flash') # Usando um modelo rápido e eficiente

# --- NOVA FUNÇÃO DE EXTRAÇÃO COM GEMINI AI ---
def extrair_dados_vaga_com_ia(texto_completo):
    # Instrução (Prompt) para a IA
    prompt = f"""
    Analise o texto de anúncio de vaga de emprego a seguir e extraia as seguintes informações em formato JSON:
    - cargo: O título ou nome da vaga.
    - requisitos: Uma lista de todos os requisitos mencionados.
    - beneficios: Uma lista de todos os benefícios, se mencionados.
    - local: A cidade, estado ou se a vaga é remota/híbrida.
    - contato: O email ou telefone para contato.

    Se uma informação não for encontrada, retorne "Não encontrado" para strings ou uma lista vazia para listas.

    Texto da Vaga:
    ---
    {texto_completo}
    ---
    """
    
    try:
        # Chama a API do Gemini
        response = gemini_model.generate_content(prompt)
        
        # O Gemini pode retornar o JSON dentro de um bloco de código Markdown. Vamos limpar isso.
        cleaned_text = re.sub(r'```json\n|\n```', '', response.text).strip()

        # Tenta decodificar o JSON retornado pela IA
        import json
        dados = json.loads(cleaned_text)
        
        # Adiciona o texto completo original para referência
        dados['texto_completo'] = texto_completo
        
        return dados
    except Exception as e:
        # Se a IA falhar ou retornar um formato inesperado, retorna o texto bruto
        return {
            'cargo': 'IA não conseguiu extrair',
            'requisitos': [],
            'beneficios': [],
            'local': 'IA não conseguiu extrair',
            'contato': 'IA não conseguiu extrair',
            'texto_completo': f"Erro ao processar com a IA: {str(e)}\n--- Texto Original ---\n{texto_completo}"
        }

@app.route('/analisar', methods=['POST'])
def analisar_imagem():
    conteudo = request.get_data()
    if not conteudo:
        return jsonify({"erro": "Corpo da requisição está vazio."}), 400
    
    try:
        imagem = vision.Image(content=conteudo)
        response = vision_client.text_detection(image=imagem)
        textos = response.text_annotations

        if textos:
            texto_completo = textos[0].description
            # Chama a nova função com IA
            dados_extraidos = extrair_dados_vaga_com_ia(texto_completo)
            return jsonify(dados_extraidos)
        else:
            return jsonify({"erro": "Nenhum texto encontrado na imagem."})

    except Exception as e:
        return jsonify({"erro": f"Erro na API do Google Vision: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
