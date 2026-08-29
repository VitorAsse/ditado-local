# Avisos de terceiros

O Ditado Local é distribuído sob licença MIT, mas depende de projetos mantidos por
terceiros. Cada projeto continua sujeito à sua própria licença.

O pacote de Release deste repositório contém o código do Ditado Local e o instalador.
As dependências Python e os modelos são baixados de suas fontes oficiais durante a
instalação ou primeira execução.

## Motor de transcrição e modelos

| Projeto | Uso | Licença |
| --- | --- | --- |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Transcrição local | MIT |
| [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) | Inferência otimizada | MIT |
| [OpenAI Whisper](https://github.com/openai/whisper) | Arquitetura e pesos originais | MIT |
| [dropbox-dash/faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) | Modelo convertido usado no perfil equilibrado | MIT |

## Dependências diretas

| Pacote | Versão fixada | Licença declarada |
| --- | --- | --- |
| customtkinter | 6.0.0 | CC0-1.0 |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| ctranslate2 | 4.4.0 | MIT |
| faster-whisper | 1.2.1 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| numpy | 2.4.6 | BSD-3-Clause e componentes compatíveis |
| Pillow | 12.3.0 | MIT-CMU |
| pycaw | 20251023 | MIT |
| pynput | 1.8.2 | LGPL-3.0 |
| pyperclip | 1.11.0 | BSD |
| pystray | 0.19.5 | LGPL-3.0 |
| setuptools | 80.9.0 | MIT |
| sounddevice | 0.5.5 | MIT |
| soxr | 1.1.0 | LGPL-2.1-or-later |

## Fonte incorporada

| Projeto | Uso | Licença |
| --- | --- | --- |
| [DM Sans](https://github.com/google/fonts/tree/main/ofl/dmsans) | Tipografia da interface | SIL Open Font License 1.1 |

O texto integral da licença acompanha a fonte em `assets/fonts/OFL.txt`.

Dependências transitivas são instaladas pelo `pip` e também mantêm suas próprias
licenças. Use o metadata do ambiente instalado para obter a lista exata correspondente
à plataforma.

## Distribuição de binários

Uma futura distribuição que incorpore dependências, CUDA, cuDNN ou pesos de modelos
deve incluir os respectivos textos de licença e atender às condições de redistribuição.
O arquivo de Release atual não incorpora esses componentes.
