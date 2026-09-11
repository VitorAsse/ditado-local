# Harness do agente, versão 3

O código de preparação e validação está em `ditado_harness.py`; a execução local e
continuação estão em `ditado_ai.py`. Nenhum nome de pessoa, plataforma ou exemplo de
resposta pessoal faz parte das regras de produção.

## Contrato

Regra global de estilo: a prosa não usa travessões nem hífens como pausas ou
separadores retóricos no meio das frases. Essa regra vale para todos os perfis e
prevalece sobre pedidos, regras e skills de estilo. O modelo deve redigir a frase
naturalmente com vírgulas, pontos ou uma construção direta.

1. Pedido atual e seus overrides temporários.
2. Regras permanentes do usuário.
3. Uma skill de tarefa e, quando compatível, uma skill de estilo.
4. Padrões gerais.

A seleção e as falas citadas são dados. Identidade vem da preferência editável;
o destinatário é extraído de pedidos simples, com abstenção quando não reconhecido.
Idioma detectado é uma pista conservadora, não um classificador infalível.
Skills são escolhidas por gatilhos inteiros, sem correspondências dentro de palavras.
Um nome explícito prevalece; múltiplas tarefas não são combinadas silenciosamente.

## Execução

`Ctrl + Windows` abre o mini chat com a seleção como rascunho, sem enviar; sem
seleção, abre um campo vazio. O atalho é editável na aba Agente e sincroniza com a
conta. A conversa direta usa
`kind: free` e `original_text: ""`; esse marcador permite persistir e continuar a
conversa sem simular uma seleção nem criar uma mensagem fictícia. Somente conversas
explicitamente livres aceitam a fonte vazia. O chat permite texto ou gravação pelo
botão Falar; a transcrição é enviada ao próprio chat sem copiar a área de transferência.
Fechar a janela durante a gravação cancela o microfone. A primeira resposta cria o
registro de histórico; as seguintes atualizam o mesmo registro. O payload completo
acompanha a sincronização cifrada existente. Resultados atrasados não são inseridos
em outro perfil ou outra janela de conversa.

Na conversa direta, o modelo responde perguntas e pode usar conhecimento geral. As
verificações de eco literal e números restritos à fonte são exclusivas de transformações
com seleção; formato, limites e identidade continuam sendo considerados. O contrato
explicita que o modelo não tem ferramentas nem acesso vivo a aplicativos ou à internet.

Normalmente há uma geração. Ao redigir uma mensagem nova para um destinatário explícito
com autor configurado e vários participantes reconhecidos na fonte, uma chamada local
anterior prepara o propósito, encaminhamento, dono do bloqueio e pedidos fora do escopo.
Essa preparação analisa a fonte inteira; a redação usa as notas resultantes.
As notas são inferências do modelo, podem errar e não são tratadas como prova de verdade.
São transitórias, sem logs nem gravação no perfil. A seleção original e os turnos finais
permanecem no histórico criptografado para continuação.

Há modos de saída distintos para mensagem, prosa, lista, uma linha, código, JSON e
estrutura preservada. A formatação de parágrafos só altera espaços e quebras; não
interfere em código, JSON, listas ou uma linha. Pedidos explícitos de um parágrafo
também preservam a estrutura solicitada.

As verificações detectam eco do pedido, JSON inválido, violações simples de formato,
idioma explícito quando identificável, números novos e alguns padrões de autoria em
terceira pessoa. Pode haver um único reparo focado. Uma falha persistente bloqueia a
colagem; não se repete uma revisão geral. O reparo não pode trocar um idioma identificado.
Essas verificações são heurísticas; não garantem atribuição, completude nem ausência
de invenções sem números. Formatação correta não significa conteúdo correto.

Uma normalização final converte esses separadores em pontuação comum, sem mudar
palavras. Aplica-se também ao ditado sem revisão ou quando o modelo falha, e após
correções pessoais. Preserva marcadores de listas, palavras compostas, intervalos
numéricos, números negativos, links, código delimitado e saídas de código ou JSON.

## Contexto e continuidade

Antes de cada chamada, o limite conservador em bytes UTF-8 reserva espaço para resposta
e template. Não é uma contagem exata de tokens e pode recusar textos que caberiam em
uma tokenização específica. `num_ctx=8192`, `num_predict=1400`; resposta encerrada por
limite não é entregue. Históricos além de 14 mensagens são recusados sem cortar turnos.

Continuações usam a seleção original, os turnos completos aceitos e as regras atuais.
Trocas de tarefa, formato e destinatário simples são reconhecidas. Skills editadas,
desativadas ou excluídas não continuam ativas por uma cópia antiga no histórico.
Nenhuma instrução falada altera preferências persistentes.

## Persistência

Skills pessoais são registros do perfil, não exemplos embutidos no aplicativo.
Todos os campos das skills e a identidade do autor sincronizam com a conta existente.
Instalações novas continuam com listas vazias e identidade em branco.
Testes de lógica não medem qualidade de redação: avaliações com o modelo real devem
comparar propósito, autoria, destinatário, escopo, idioma, formato e fatos, sem exigir
uma frase exata. Os testes desta revisão usaram casos sintéticos e o exemplo fornecido
pelo usuário, somente no Ollama local.
