# Como contribuir

Obrigado pelo interesse no Ditado Local.

## Preparação

1. Crie um fork do repositório.
2. Crie uma branch curta para a alteração.
3. Use Python 3.11 ou 3.12.
4. Instale as dependências verificadas de `requirements.lock`.

## Verificação

Execute antes de abrir um pull request:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m py_compile ditado_ai.py ditado_audio.py ditado_local.pyw ditado_storage.py
```

Teste mudanças de interface no Windows. Não inclua `config.json`, histórico,
capturas pessoais, modelos, bibliotecas de GPU ou ambientes virtuais.

## Pull requests

Explique:

- o comportamento alterado;
- por que a mudança é necessária;
- como ela foi verificada;
- qualquer impacto em privacidade, licença ou compatibilidade.

Não envie dados pessoais, tokens, áudio real ou conteúdo da área de transferência.
