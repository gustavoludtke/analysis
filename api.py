import os
from flask import Flask, request, jsonify
from google.cloud import vision

# --- Configuração ---
# O Render vai configurar esta variável de ambiente para nós.
# Ela aponta para o arquivo de credenciais.
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credenciais.json'

app = Flask(__name__)
client = vision.ImageAnnotatorClient()

# --- Função de Extração (similar à que fizemos em PHP) ---
def extrair_dados_vaga(texto):
    import re
    dados = {
        'cargo': 'Não encontrado',
        'salario': 'Não encontrado',
        'local': 'Não encontrado',
        'texto_completo': texto
    }
    
    # Regex para cargo
    match_cargo = re.search(r'^(?:cargo|vaga|posição)[\s:]*(.*)', texto, re.IGNORECASE | re.MULTILINE)
    if match_cargo:
        dados['cargo'] = match_cargo.group(1).strip()

    # Regex para salário
    match_salario = re.search(r'(R\$\s*[\d\.,]+)', texto, re.IGNORECASE)
    if match_salario:
        dados['salario'] = match_salario.group(1).strip()
    
    # Regex para local
    match_local = re.search(r'^(?:local|localização|cidade)[\s:]*(.*)', texto, re.IGNORECASE | re.MULTILINE)
    if match_local:
        dados['local'] = match_local.group(1).strip()
    elif re.search(r'\b(remoto|híbrido)\b', texto, re.IGNORECASE):
        dados['local'] = re.search(r'\b(remoto|híbrido)\b', texto, re.IGNORECASE).group(0).capitalize()

    return dados

# --- Endpoint da API ---
@app.route('/analisar', methods=['POST'])
def analisar_imagem():
    if 'imagem' not in request.files:
        return jsonify({"erro": "Nenhum arquivo de imagem enviado"}), 400

    file = request.files['imagem']
    
    try:
        conteudo = file.read()
        imagem = vision.Image(content=conteudo)

        response = client.text_detection(image=imagem)
        textos = response.text_annotations

        if textos:
            texto_completo = textos[0].description
            dados_extraidos = extrair_dados_vaga(texto_completo)
            return jsonify(dados_extraidos)
        else:
            return jsonify({"erro": "Nenhum texto encontrado"})

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))