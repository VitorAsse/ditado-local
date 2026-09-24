# Ativar skills no Ditado Local

Na aba Skills, use **Adicionar skills padrão** para importar as 18 skills genéricas.
Você pode editar, pausar ou excluir suas cópias. O botão preserva versões existentes,
inclusive pausadas. O perfil aceita até 30 skills; adicionar a biblioteca é opcional.

## Sobre um texto selecionado

1. Selecione o texto, código, fórmula ou dados.
2. Segure **Ctrl esquerdo + Alt esquerdo** e fale a instrução do agente.
3. Diga uma frase de ativação junto do pedido e solte as teclas.

**Ctrl + Espaço** é ditado simples; não ativa as skills do agente.
Os mesmos pedidos podem ser digitados ou falados na conversa direta do agente.

## Duas maneiras de ativar

- Por frase: “Corrija a fórmula da planilha mantendo os intervalos.”
- Por nome explícito: “Use a skill Fórmulas do Google Sheets e corrija esta fórmula.”

Para combinar tarefas, cite suas frases distintas ou nomeie cada uma:
“Use a skill Expressões do n8n e a skill JavaScript; corrija esta expressão.”
Quando uma tarefa é nomeada explicitamente, ela tem preferência sobre tarefas
detectadas apenas por gatilho. Nomeie todas as tarefas que pretende combinar.

Até quatro skills são carregadas por pedido, com no máximo 8.000 caracteres de
instruções combinadas. Estilo e contexto também contam. Mais skills não significam
melhor resultado; use apenas as necessárias.

## Frases rápidas

| Skill | Frase de ativação |
|---|---|
| Mensagens profissionais | mensagem profissional |
| Criar e revisar copy | revisar copy |
| Inglês natural | inglês natural |
| Fórmulas do Google Sheets | fórmula da planilha |
| Expressões do n8n | expressão do n8n |
| Revisar fluxos do n8n | fluxo do n8n |
| Expressões cron | expressão cron |
| JavaScript | JavaScript |
| Python | Python |
| HTML e CSS | HTML ou CSS |
| PowerShell | PowerShell |
| SQL | SQL |
| JSON | corrija o JSON |
| Expressões regulares | regex |
| Requisições HTTP e cURL | cURL |
| Comandos Git | comando Git |
| Criar e melhorar prompts | melhore este prompt |
| Transformar dados e listas | remova duplicatas |

## Limites da ativação

Os gatilhos são procurados no pedido falado ou digitado, sem distinção de acentos
e maiúsculas. Não são procurados no texto selecionado, e sinônimos não cadastrados
não são reconhecidos automaticamente. Uma frase negada ainda pode conter um gatilho:
prefira dizer positivamente o que deseja em vez de listar skills que não quer.

Na continuação, “deixe mais curto” mantém as skills anteriores. Um pedido só de
complemento, como “inglês natural”, acrescenta esse complemento sem apagar os anteriores.
Para trocar a combinação, solicite a nova tarefa com os complementos desejados ou
inicie outra conversa. Skills pausadas, removidas ou editadas são respeitadas.

Os contextos personalizados pertencem ao perfil e não são distribuídos na biblioteca.
As skills importadas e suas edições sincronizam criptografadas quando a conta está
conectada; a recuperação em outro computador requer login e código de recuperação.
