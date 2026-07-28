# Ditado Local

Ditado e transformação de texto por voz no Windows, com processamento local usando
Faster Whisper e Ollama.

![Tela de regras permanentes do Ditado Local](assets/ditado-local.png)

## O que o aplicativo faz

- Segure `Ctrl + Espaço` para ditar em qualquer aplicativo.
- Selecione um texto e segure `Ctrl esquerdo + Alt esquerdo` para transformá-lo por voz.
- Escolha detecção automática ou fixe o idioma da transcrição.
- Cadastre correções de grafia para nomes, marcas e termos técnicos.
- Crie regras permanentes para preferências que devem valer em todas as ações.
- Crie skills acionadas por nome ou frase para fluxos específicos.
- Use CPU automaticamente quando a aceleração por GPU não estiver disponível.
- Mantenha um histórico local protegido pela conta atual do Windows.

Novas instalações não recebem regras, termos ou preferências pessoais do autor.

## Instalação rápida

### 1. Requisitos

- Windows 10 ou Windows 11 de 64 bits.
- Python 3.11 ou 3.12 disponível pelo comando `py` ou `python`.
- Microfone.
- Conexão com a internet durante a instalação e no primeiro download dos modelos.

Ollama é opcional para o ditado básico e necessário para revisão gramatical, regras,
skills e ações sobre texto selecionado. Instale o
[Ollama](https://ollama.com/download/windows) e prepare o modelo padrão:

```powershell
ollama pull qwen3:4b-instruct
```

### 2. Baixe e instale

Baixe o arquivo `DitadoLocal-<versão>.zip` na página de Releases, extraia e execute:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Para iniciar automaticamente com o Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -StartWithWindows
```

O instalador cria um ambiente Python isolado em
`%LOCALAPPDATA%\faster-whisper`, preserva configurações existentes e adiciona
`Ditado Local` ao menu Iniciar.

## Como usar

### Ditado

1. Coloque o cursor no campo em que deseja escrever.
2. Segure `Ctrl + Espaço`.
3. Fale e solte `Espaço`.
4. O texto é copiado e, quando habilitado, colado no aplicativo em foco.

### Agente local

1. Selecione um texto.
2. Segure apenas `Ctrl esquerdo + Alt esquerdo`.
3. Diga algo como `deixe mais curto` ou `transforme em uma lista`.
4. Solte uma das teclas.

### Regras permanentes

Regras são preferências sempre ativas. Cada resultado passa por uma segunda revisão
local quando existe pelo menos uma regra habilitada.

Exemplos:

- `Preserve o idioma do texto, exceto quando eu pedir uma tradução.`
- `Não altere números, URLs ou nomes próprios.`
- `Mantenha um tom direto e evite linguagem promocional.`

As regras ficam somente no `config.json` do usuário e não fazem parte do código.

### Skills

Skills são comportamentos ativados por nome ou frase. Use quando uma regra não deve
ser aplicada o tempo todo, como formatação de resumo semanal ou resposta profissional.

## Idiomas

O aplicativo oferece detecção automática e seleção explícita de português, inglês,
espanhol, francês, alemão, italiano e árabe. O Faster Whisper aceita outros códigos de
idioma por configuração manual.

## Privacidade

- O áudio é transcrito localmente.
- O texto enviado ao agente usa o endpoint local do Ollama por padrão.
- O histórico é criptografado com a proteção de dados da conta atual do Windows.
- A captura de textos copiados por outros aplicativos começa desativada.
- Não existe telemetria no aplicativo.
- PyPI e Hugging Face são acessados para baixar dependências e modelos.

Se `DITADO_OLLAMA_URL` apontar para outra máquina ou serviço, o texto será enviado para
esse destino. O usuário é responsável pela privacidade desse endpoint.

## Configuração

O arquivo é criado em:

```text
%LOCALAPPDATA%\faster-whisper\config.json
```

Consulte [`config.example.json`](config.example.json) para ver a estrutura sem dados
pessoais.

Variáveis opcionais:

| Variável | Padrão | Finalidade |
| --- | --- | --- |
| `DITADO_OLLAMA_URL` | `http://127.0.0.1:11434/api/chat` | Endpoint compatível com a API de chat do Ollama |
| `DITADO_OLLAMA_MODEL` | `qwen3:4b-instruct` | Modelo local usado para revisão e ações |

## GPU

O projeto fixa `ctranslate2==4.4.0` para manter compatibilidade com CUDA 12 e cuDNN 8.
Sem as bibliotecas NVIDIA compatíveis, o aplicativo utiliza automaticamente o modelo
Whisper Small em CPU com quantização INT8.

Os binários de CUDA, cuDNN e os pesos dos modelos não são incluídos no repositório.

## Desenvolvimento

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\pythonw.exe .\ditado_local.pyw
```

Para montar o arquivo de uma Release:

```powershell
powershell -File .\scripts\package-release.ps1 -Version 0.1.0
```

## Licença

O código do Ditado Local usa a [licença MIT](LICENSE). Bibliotecas e modelos mantêm
suas próprias licenças, relacionadas em
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Contribuições são bem-vindas. Leia [`CONTRIBUTING.md`](CONTRIBUTING.md).
