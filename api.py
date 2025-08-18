import os
import re
import json
import google.generativeai as genai
from flask import Flask, request, jsonify
from google.cloud import vision

# --- CONFIGURAÇÃO ---
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credenciais.json'
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)

app = Flask(__name__)
vision_client = vision.ImageAnnotatorClient()
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

# --- FUNÇÃO DE EXTRAÇÃO COM IA (REFINADA) ---
def extrair_dados_vaga_com_ia(texto_completo):
    prompt = f"""
    Você é um assistente de RH especialista em analisar anúncios de vagas.
    Analise o texto de anúncio de vaga de emprego a seguir e extraia as seguintes informações em um formato JSON.
    As chaves do JSON devem ser exatamente: "nome_empresa", "whatsapp", "email", "beneficios", "requisitos", "nome_cargo", "horario_trabalho", "atividades", "carga_horaria".

    Regras de Extração Específicas:
    - "whatsapp": Encontre QUALQUER número de telefone no texto e classifique-o como WhatsApp. Formate-o como você o encontrou.
    - "email": Encontre QUALQUER texto que se pareça com um endereço de e-mail (ex: nome@exemplo.com).
    - "beneficios", "requisitos", "atividades": Retorne uma lista (array) de strings. Se não encontrar, retorne uma lista vazia [].
    - Para todos os outros campos de string: Se a informação não for encontrada no texto, o valor correspondente no JSON deve ser a string "não informado".

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
        return {
            'erro': f"Erro ao processar com a IA: {str(e)}",
            'texto_completo': texto_completo
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
            dados_extraidos = extrair_dados_vaga_com_ia(texto_completo)
            return jsonify(dados_extraidos)
        else:
            return jsonify({"erro": "Nenhum texto encontrado na imagem."})
    except Exception as e:
        return jsonify({"erro": f"Erro na API do Google Vision: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
