# Microsoft Copilot — levantamento técnico de arquitetura, funções e documentação

**Fonte de partida:** https://support.microsoft.com/pt-br/microsoft-copilot/  
**Data da coleta:** 24 de outubro de 2026  
**Idioma:** Português do Brasil (artigos em inglês quando o destino oficial assim redirecionou)  
**Tipo de entrega:** inventário documentado, síntese técnica autoral e índice de URLs verificadas.

> **Escopo e precisão.** Este levantamento percorre as **24 entradas de documentação do hub** que apontam para artigos, portais ou áreas de suporte, e amplia a parte de arquitetura com fontes técnicas oficiais do Microsoft Learn. Não é uma cópia integral do texto dos artigos, nem um espelho de todo o domínio support.microsoft.com, que contém milhares de páginas e conteúdo dinâmico. Links podem redirecionar, a disponibilidade muda por conta, licença, país e data. A documentação pública explica a arquitetura **lógica** e os fluxos de produto; não revela código-fonte, topologia física privada, prompts internos, credenciais, modelos exatos de cada requisição ou implementação proprietária completa.

## 1. Visão geral e taxonomia

O hub reúne ajuda para o assistente de IA Microsoft Copilot e aponta, em diversos itens, para páginas atualizadas da experiência **Microsoft 365 Copilot**. As experiências **pessoais** (conta Microsoft e planos de consumo) e **de trabalho/estudante** (Microsoft Entra ID, tenant Microsoft 365) não são intercambiáveis em licenciamento, dados autorizados e controles administrativos.

### 1.1 Famílias da experiência

| Família | Características observadas | Interfaces citadas |
|---|---|---|
| Chat e modelos | Perguntas, redação, análise, prompts, modos de conversa e fontes externas | Web, Windows, macOS, iOS/Android e aplicativos Microsoft 365 |
| Conteúdo | Upload de arquivos, geração/edição de imagens, Páginas colaborativas, relatórios | Chat, Biblioteca, Páginas |
| Multimodal | Ditado, leitura de respostas, diálogo por voz, compartilhamento de tela/câmera | Voice, Vision, aplicativos e Web |
| Navegação | Resumos/contexto do Edge, automação de cliques e formulários em Navegar com o Copilot, Jornadas | Microsoft Edge |
| Agentes e tarefas | Planejamento, execução de múltiplas etapas, arquivos, e-mails, agenda, tarefas agendadas | Tasks/Cowork, agentes e conectores |
| Integração de dados | OneDrive, Outlook.com, Google Drive/Gmail/Calendar; dados do Microsoft 365/Graph no contexto corporativo | Conectores pessoais, Graph, Copilot Studio |
| Confiança | Identidade, autorizações, privacidade, permissões, controles de retenção e proteção corporativa | Configurações da conta, Microsoft Entra, Purview, políticas empresariais |
| Experiências setoriais | Shopping e Copilot Health | Copilot e serviços associados |

**Fontes:** [hub geral](https://support.microsoft.com/pt-br/microsoft-copilot/), [introdução ao Copilot](https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-microsoft-365-copilot-chat), [visão geral da aplicação Microsoft 365 Copilot](https://support.microsoft.com/pt-br/microsoft-365-copilot/what-is-the-microsoft-365-copilot-app).

## 2. Arquitetura documentada — serviço e fluxo de dados

### 2.1 Diagrama lógico autoral

```mermaid
flowchart TD
    U[Usuário] --> C[Superfícies: Web / Windows / macOS / Edge / mobile / Microsoft 365]
    C --> A[Autenticação, identidade e políticas]
    A --> O[Serviço de orquestração Copilot]
    O --> G[Preparação da consulta e grounding]
    G --> P[Contexto autorizado do usuário]
    P --> MG[Microsoft Graph / índice semântico / arquivos / mensagens]
    P --> WEB[Índice da Web, quando habilitado]
    P --> EXT[Conectores e APIs autorizadas]
    G --> L[Modelo(s) de linguagem e capacidades multimodais]
    O --> ACT[Seleção de ações / ferramentas / agentes]
    ACT --> SAFE[Verificações de segurança, identidade, consentimento e limites]
    SAFE --> EXEC[Execução no serviço / navegador / sistema conectado]
    EXEC --> O
    L --> O
    O --> R[Resposta, citações, arquivos ou resultado de tarefa]
    R --> C
    O --> AUD[Histórico, auditoria, controles e conformidade conforme o produto]
```

**Importante:** o diagrama acima é uma **reconstrução lógica** baseada na documentação, não um desenho oficial de serviços físicos nem uma alegação de que todos os produtos seguem exatamente o mesmo pipeline. *Vision* (percepção) e *Navigate/Browse with Copilot* (ação no navegador) são recursos distintos.

### 2.2 Pipeline de uma resposta com contexto corporativo

1. A pessoa fornece a solicitação em um aplicativo ou interface Copilot.
2. O serviço considera autenticação, contexto e privilégios da identidade autenticada.
3. O prompt é fundamentado (*grounded*) com dados disponíveis e autorizados no tenant, incluindo acesso via Microsoft Graph, quando a experiência/conta oferece esse recurso.
4. O orquestrador usa os dados relevantes e solicita processamento ao modelo.
5. O resultado é devolvido à experiência, podendo passar por nova recuperação/contextualização e verificações de segurança.
6. A interação pode aparecer no histórico e nos mecanismos de conformidade apropriados ao tipo de conta.

**Documentado:** dados da organização ficam sujeitos à fronteira de serviço do Microsoft 365; o Copilot não recebe permissão universal de ler todo o tenant; respeita permissões existentes do usuário, Acesso Condicional e MFA. Referências: [arquitetura Microsoft Learn](https://learn.microsoft.com/pt-br/microsoft-365/copilot/microsoft-365-copilot-architecture), [Zero Trust](https://learn.microsoft.com/pt-br/security/zero-trust/copilots/zero-trust-microsoft-365-copilot).

### 2.3 Orquestrador de ações, habilidades e agentes

O documento técnico oficial descreve a seguinte sequência funcional:

- Verificação inicial e controles de segurança/IA responsável.
- Composição do contexto conversacional e, quando pertinente, dados Microsoft Graph.
- Planejamento das próximas etapas pelo orquestrador.
- Seleção das ferramentas/ações habilitadas, com correspondência por nomes e descrições.
- Geração dos parâmetros necessários à chamada.
- Acionamento de uma ferramenta/API externa ao núcleo do Copilot.
- Processamento dos retornos e eventual repetição do ciclo de raciocínio até formar a resposta.
- Entrega ao usuário e atualização do estado da conversa.

A documentação menciona que a descoberta de habilidades pode considerar **até cinco candidatas**, em ordem de correspondência lexical e semântica por nome/descrição. Alerta ainda que agentes declarativos podem apresentar problemas de resposta em um turno que aciona **três ou mais ações de API distintas**: um efeito pode ocorrer no backend mesmo sem resposta final visível. Isso deve ser tratado como um risco de observabilidade e idempotência, **não** como garantia universal para outros runtimes.

**Fonte:** [orquestrador — Microsoft Learn](https://learn.microsoft.com/pt-br/microsoft-365/copilot/extensibility/orchestrator).

### 2.4 Grounding, recuperação e índice semântico

- **Grounding:** contextualiza o pedido com documentos, e-mails, reuniões e outros dados que a pessoa pode acessar.
- **Microsoft Graph:** camada de acesso a sinais e dados organizacionais usados para contextualização.
- **Índice semântico:** aprimora busca/recuperação por relevância lexical e semântica sobre dados disponibilizados para indexação.
- **Web grounding:** possibilidade de usar informações da Web quando o recurso está ativo.
- **Pós-processamento:** a documentação técnica do índice descreve acesso adicional ao Graph/índice após a resposta do modelo para compor a saída.

Esses mecanismos descrevem principalmente o **Copilot corporativo**, não implicam que toda conversa pessoal consulta o Microsoft Graph do tenant de uma organização.

**Fontes:** [arquitetura do Copilot](https://learn.microsoft.com/pt-br/microsoft-365/copilot/microsoft-365-copilot-architecture), [índice semântico](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot).

### 2.5 Dois padrões de integração diferentes

| Padrão | Mecanismo técnico documentado | Uso predominante | Consideração |
|---|---|---|---|
| **Copilot connectors (Microsoft Graph)** | Ingestão/indexação de conteúdo externo no ecossistema Microsoft 365 | Busca, conhecimento, recuperação semântica e citações | Conteúdo replicado/indexado com escopo de ACL da origem |
| **Power Platform connectors** | Acesso de API em tempo real, com conexões e identidade do serviço | Dados atualizados, leitura, escrita e ações transacionais | Pode executar efeitos reais; depende de governança de ferramentas/conexões |

Essa distinção se refere ao **Copilot Studio/ambiente corporativo**. Os conectores pessoais do Copilot para Gmail/Drive/Outlook são um produto relacionado, mas não equivalem automaticamente à ingestão Microsoft Graph ou a uma ferramenta Power Platform.

**Fonte:** [Copilot connectors versus Power Platform connectors](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-graph-vs-power-platform-connectors).

## 3. Catálogo de funções — artigos da central

### 3.1 Introdução e operação geral

O Copilot oferece perguntas e respostas, rascunhos e resumos. A documentação atual de introdução diferencia **Chat** (conversa), **Cowork** (tarefas prolongadas) e **Código** (trabalho de programação). Em contas corporativas, a opção **Work IQ/QI de trabalho** controla o uso de contexto organizacional; a fonte afirma que as respostas só usam material a que o usuário já tem acesso. A versão pessoal utiliza conteúdo pessoal conectado, e a disponibilidade das opções muda conforme a conta.

**Fonte:** [introdução](https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-microsoft-365-copilot-chat).

### 3.2 Modos de conversa

- **Resposta rápida:** favorece baixa latência.
- **Pensar mais a fundo/Think Deeper:** resposta mais ponderada para análise complexa.
- **Estude e aprenda:** interação guiada para compreensão e exercícios.
- **Inteligente/Smart:** seleção de resposta conforme a complexidade; o artigo consultado nomeia GPT-5, informação que pode mudar com novas versões.
- **Pesquisar:** prioriza conteúdo atual da Web com citações.

O artigo associa especificamente esses modos à experiência de **contas pessoais**. Não se deve impor esses nomes ao seletor de modelos do tenant empresarial sem confirmação da interface.

**Fonte:** [modos de conversa](https://support.microsoft.com/pt-br/microsoft-365-copilot/conversation-modes-in-microsoft-copilot).

### 3.3 Histórico das conversas e memória

- Usuários conectados podem reabrir conversas anteriores.
- Podem excluir conversas individuais e, conforme conta, o histórico completo.
- Podem compartilhar uma conversa/resposta por link com pré-visualização do conteúdo.
- A página do histórico pessoal descreve retenção padrão de **18 meses**; normas empresariais e políticas de conformidade podem ser diferentes.
- Um link compartilhado representa a cópia no momento da criação; não é uma janela viva para os turnos futuros da conversa.
- Memória/personalização e histórico são conceitos diferentes e possuem controles próprios.

**Fontes:** [histórico](https://support.microsoft.com/pt-br/microsoft-copilot/conversation-history-in-microsoft-copilot), [memória no Copilot corporativo](https://support.microsoft.com/pt-br/microsoft-365-copilot/manage-copilot-memory-in-microsoft-365-copilot).

### 3.4 Criação e edição de imagens

- Geração de imagem a partir de instruções textuais.
- Edição de imagem fornecida pela pessoa, com acompanhamento conversacional.
- Possibilidade de especificar estilo, composição e elementos a editar.
- Direitos de uso do material carregado são responsabilidade do usuário.
- A página descreve armazenamento temporário de imagens e controles associados às conversas; **não** apresenta especificações do pipeline interno de difusão/geração.

**Fonte:** [geração de imagem](https://support.microsoft.com/pt-br/microsoft-copilot/using-image-generation-in-microsoft-copilot).

### 3.5 Upload, análise de arquivos e limites

**Formatos listados:** PDF, DOCX, XLSX, PPTX, PNG, JPEG, PJP, JFIF, TXT, JSON, CSV, MD e tipos relacionados.

**Limites documentados no artigo consultado:** **até 20 arquivos por conversa** e **até 50 MB por arquivo**; limites podem evoluir conforme a experiência e o plano. O arquivo entra no contexto para explicações, resumos, perguntas e análises. A página declara que conteúdo de arquivos carregados não é utilizado para treinamento dos modelos pela Microsoft; conferir sempre os termos aplicáveis à conta/experiência específica.

**Fonte:** [upload de arquivos](https://support.microsoft.com/pt-br/microsoft-365-copilot/file-upload-in-microsoft-copilot).

### 3.6 Deep Research — pesquisa aprofundada

- Produz relatórios de pesquisa mais extensos, com análise e referências.
- Pode solicitar esclarecimentos para definir o escopo.
- Disponibilidade depende de login, região e condições do produto.
- O artigo informa limites mais altos para determinadas assinaturas Microsoft 365 de consumo.
- Na documentação consultada, o aplicativo **nativo macOS** não oferece Deep Research, mas o recurso pode ser acessado pelo navegador.
- A fonte não detalha o algoritmo interno de decomposição de tarefas ou a lista completa de motores de busca utilizados.

**Fonte:** [Deep Research](https://support.microsoft.com/pt-br/microsoft-copilot/deep-research-in-microsoft-copilot).

### 3.7 Páginas do Copilot

- Transforma uma resposta de chat em artefato persistente e editável.
- Permite edição humana e atualizações solicitadas ao assistente, lado a lado.
- Suporta compartilhamento e colaboração quando disponível.
- Pode oferecer recursos visuais, painéis e evolução do conteúdo conforme a edição.
- Algumas experiências permitem produzir arquivos Word ou PowerPoint a partir de uma página.
- Na documentação consultada, acesso corporativo requer recursos de armazenamento SharePoint/OneDrive; condições pessoais dependem da assinatura.

**Fontes:** [introdução às Páginas](https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-microsoft-365-copilot-pages), [como Páginas funciona](https://support.microsoft.com/pt-br/microsoft-365-copilot/how-microsoft-365-copilot-pages-works).

### 3.8 Voz: três recursos distintos

| Recurso | Entrada/saída | Observações |
|---|---|---|
| Ditado | voz → texto no compositor | Pode exigir permissões de microfone; usuário revisa transcrição |
| Ler em voz alta | texto da resposta → áudio | Artigo descreve TTS do lado do cliente e sem gravação desse áudio, no contexto corporativo |
| Copilot Voice | diálogo falado em tempo real | Início/pausa/fim de sessão, perguntas subsequentes, transcrição posterior |

Para **Copilot Voice corporativo**, o documento consultado informa retenção temporária do áudio em cenários de feedback e exclusão em **48 horas**. Os fluxos de áudio/privacidade pessoal seguem documentação própria; não extrapolar a mesma retenção a todos os produtos.

**Fonte:** [recursos de voz](https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-voice-features-in-microsoft-365-copilot).

### 3.9 Visão do Copilot — percepção visual sem automação de ações

- Durante uma conversa de voz, usuário seleciona a tela desktop ou câmera mobile a compartilhar.
- Copilot analisa interfaces, gráficos, documentos e imagens visíveis para explicar ou orientar.
- Pode combinar contexto visual e informações de trabalho autorizadas nas experiências corporativas.
- Funciona somente com conteúdo compartilhado na sessão ativa; a documentação destaca que não mantém memória visual de longo prazo entre sessões.
- A visão, isoladamente, **não clica, digita nem rola**; esse comportamento pertence a recursos de ação no navegador.
- O material sobre funcionamento descreve a entrada visual como uma sequência de imagens e limitações envolvendo vídeos/GIFs e troca rápida de janelas.

**Fontes:** [início da Visão](https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-vision-microsoft-365-copilot), [como a Visão funciona](https://support.microsoft.com/pt-br/microsoft-365-copilot/how-vision-microsoft-365-works).

### 3.10 Conectores pessoais

Serviços listados no artigo: **OneDrive, Outlook.com** (e-mail, calendário, contatos), **Google Drive, Gmail, Google Calendar e contatos Google**. O usuário precisa ativar os conectores e conceder permissões. Permitem busca de arquivos/e-mails, consultas de agenda e recuperação de dados dos serviços. A Microsoft afirma que não amplia privilégios que a conta já possui. A disponibilidade indicada é Web Copilot e aplicativo móvel, conforme tipo de conta e região.

**Fonte:** [conectores pessoais](https://support.microsoft.com/pt-br/microsoft-copilot/connecting-microsoft-copilot-to-other-services).

### 3.11 Copilot no Windows

Além da experiência Web, o aplicativo menciona tecla dedicada Copilot ou **Win+C**, ativação por voz (“Ei Copilot”), visão, pesquisa e leitura de arquivos locais, capturas de tela, exibição de conteúdo da Web e auxílio em configurações do Windows. A leitura de arquivos local cita tipos como DOCX, XLSX, PPTX, TXT, PDF e JSON e permissão específica. São capacidades de produto, não evidência de API pública de controle irrestrito do sistema operacional.

**Fonte:** [Copilot no Windows](https://support.microsoft.com/pt-br/microsoft-copilot/getting-started-with-copilot-on-windows).

### 3.12 Copilot no Edge

- Ajuda a resumir páginas, PDFs, vídeos e conteúdo das guias.
- Dependendo do pedido e das configurações, pode usar título/URL, guia atual, contexto de navegação e histórico recente.
- Possui controles de contexto e privacidade no navegador.
- Não significa automaticamente que o Copilot pode executar qualquer ação: resumo/análise e automação de navegação são funcionalidades separadas.

**Fonte:** [Copilot no Edge](https://support.microsoft.com/pt-br/edge/copilot/getting-started-with-copilot-in-microsoft-edge).

### 3.13 Copilot no macOS

O aplicativo inclui atalho de teclado, ajuste de aparência claro/escuro e respostas contextualizadas ao macOS. Na versão do artigo consultada, Páginas e Deep Research não são oferecidos no aplicativo nativo, mas determinados recursos podem funcionar pela Web. **Há uma possível redação incorreta no artigo** ao descrever o atalho do Mac como sendo usado “no Windows”; não usar essa frase como especificação técnica.

**Fonte:** [Copilot no macOS](https://support.microsoft.com/pt-br/microsoft-copilot/getting-started-with-copilot-on-macos).

### 3.14 Navegar com o Copilot — automação no Edge

- Pode executar etapas de navegação: selecionar, clicar, digitar, rolar, abrir e trocar guias.
- A página afirma que as ações ocorrem **localmente no navegador**, com visibilidade em tempo real e possibilidade de interrupção/intervenção.
- O artigo descreve implantação inicial para **Microsoft 365 Premium nos EUA**; não presumir que já esteja liberado no Brasil.
- O sistema usa controle de consentimento, restrições de sites e proteções do Edge; não deve acessar senhas salvas, carteira ou dados de preenchimento automático conforme o artigo.
- A Microsoft alerta expressamente para riscos de **prompt injection**, sites maliciosos, compras, dados pessoais e ações imprevistas.

**Fonte:** [Navegar com o Copilot](https://support.microsoft.com/pt-br/microsoft-copilot/browse-with-copilot).

### 3.15 Jornadas/Percursos do Copilot

Recurso do Edge que reúne trajetórias temáticas de navegação em cartões, com resumos e sugestões para retomar uma exploração. Usa informações de páginas visitadas, títulos/URLs, pesquisas e chats, respeitando controles específicos; está desativado por padrão. Documentação consultada indica remoção de jornadas antigas e seus dados relacionados após **14 dias**. Sincronização entre dispositivos depende da sincronização de histórico do Edge.

**Fonte:** [Jornadas](https://support.microsoft.com/pt-br/microsoft-copilot/copilot-journeys).

### 3.16 Copilot Shopping

Busca de produtos, comparações, detalhes fornecidos por comerciantes, recomendações e consulta de preços/histórico quando disponível. Disponibilidade de experiências varia conforme país, licença e plataforma. A Microsoft ressalta que preços e condições devem ser confirmados junto ao vendedor. O artigo também distingue resultados/sugestões e itens patrocinados identificados.

**Fonte:** [Shopping](https://support.microsoft.com/pt-br/edge/copilot/shopping-with-microsoft-copilot).

### 3.17 Podcasts — recurso DESCONTINUADO

**Atenção a desatualização do hub:** a entrada “Podcasts do Copilot” permanece no índice, mas o próprio artigo de destino afirma que o recurso foi **desativado em 18 de agosto de 2026**, incluindo impossibilidade de acessar/exportar podcasts antigos. Não classificar como função ativa disponível.

**Fonte:** [desativação de Podcasts](https://support.microsoft.com/pt-br/microsoft-copilot/podcasts-in-microsoft-copilot).

### 3.18 Copilot Tasks e a migração para Cowork

- Tasks permite delegar tarefas de uma execução ou recorrentes/agendadas.
- Envolve geração/edição de arquivos, uso de serviços conectados e automação de navegador para tarefas autorizadas.
- A pessoa pode acompanhar, interromper e revisar resultados; ações sensíveis devem solicitar consentimento/controle humano.
- O artigo informa que **Tasks em contas pessoais está sendo migrado para Cowork**; portanto o inventário histórico de Tasks não deve ser confundido com a interface futura/atual de cada conta.
- **Cowork** (contas pessoais em prévia no artigo consultado): execução de trabalho longo, documentos Office/PDF, correio, calendário, OneDrive, pesquisa e agendamentos; cenário empresarial acrescenta dados organizacionais/Teams/SharePoint, mediante licenças e políticas.

**Fontes:** [Tasks](https://support.microsoft.com/pt-br/microsoft-365-copilot/using-copilot-tasks), [introdução ao Cowork](https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-cowork).

### 3.19 Copilot Health — saúde

Experiência específica para perguntas de saúde, explicações de exames/sintomas, recuperação de fontes e, opcionalmente, contexto de wearables e registros. Inclui perfil e memória próprios configuráveis, não equivale a atendimento clínico. No artigo consultado, estava em **prévia nos EUA para maiores de 18 anos com assinaturas pessoais elegíveis** e não disponível em todos os aplicativos/regiões; **não considerar disponível automaticamente no Brasil**.

**Fonte:** [Copilot Health](https://support.microsoft.com/pt-br/microsoft-copilot/copilot-health).

### 3.20 Idiomas e região

A Microsoft descreve disponibilidade em **mais de 170 mercados**, com exceções geográficas e implantação gradual por recurso. **Português (Brasil)** aparece nas listas de prompts/respostas e voz da documentação consultada. Idioma da interface, qualidade de respostas e suporte a cada modalidade não são necessariamente idênticos. O artigo de idiomas consultado continha até um comentário editorial não removido na seção de voz; nesse trecho, recomenda-se checagem adicional antes de utilizar a página como especificação contratual.

**Fonte:** [idiomas suportados](https://support.microsoft.com/pt-br/microsoft-365-copilot/supported-languages-for-microsoft-365-copilot).

## 4. Privacidade, identidade e segurança

### 4.1 Separação por conta e geração da aplicação

A central original aponta páginas de controles de privacidade e FAQ que declaram expressamente aplicar-se à **versão antiga do Copilot**, anterior à atualização de **18/08/2026**. O documento correto para a versão pessoal recente é a nova área de privacidade, e a proteção para contas empresariais deve ser lida na documentação Microsoft Entra/Microsoft 365.

- [Privacidade da nova aplicação pessoal](https://support.microsoft.com/en-us/privacy/microsoft-copilot/overview)
- [Controles de privacidade — artigo antigo](https://support.microsoft.com/pt-br/microsoft-copilot/microsoft-copilot-privacy-controls)
- [FAQ antiga de privacidade](https://support.microsoft.com/pt-br/microsoft-copilot/privacy-faq-for-microsoft-copilot)
- [Menores — artigo antigo](https://support.microsoft.com/pt-br/microsoft-copilot/microsoft-copilot-young-people)
- [Proteção Zero Trust empresarial](https://learn.microsoft.com/pt-br/security/zero-trust/copilots/zero-trust-microsoft-365-copilot)

**Não transportar automaticamente** a regra antiga de retenção de 18 meses, treino de modelos ou anúncios para todos os recursos da experiência nova; conferir o documento específico e o plano.

### 4.2 Camadas empresariais descritas pela Microsoft

O guia Zero Trust organiza a implementação em sete temas: dados; identidade/acesso; proteção de aplicativos; gerenciamento e segurança do dispositivo; ameaça; colaboração pelo Teams; autorização para dados. Isso inclui os princípios de verificação explícita, privilégio mínimo e presunção de comprometimento. A documentação de arquitetura destaca MFA, Conditional Access, Graph e permissões do usuário sobre os dados organizacionais.

### 4.3 Ações de risco e limitações

- **Ações externas não são apenas texto.** Navegação e Cowork podem alterar arquivos, enviar mensagens e operar interfaces conforme permissões e aprovações.
- **Prompt injection** é um risco explicitamente reconhecido no navegador e em tarefas longas. Conteúdo de sites não deve ser tratado como instrução privilegiada.
- **Revisão humana** é requerida/recomendada para decisões sensíveis; o suporte descreve pausas e consentimento em determinados fluxos.
- **Visão visual não é autorização de ação:** reconhecer uma tela não implica poder operá-la.
- **Efeito sem resposta:** a nota do orquestrador empresarial informa casos nos quais uma ação pode ter concluído e a resposta não chegar; o cliente deve validar resultados e evitar reexecução automática cega.

## 5. Matriz de superfícies e disponibilidade — leitura prudente

| Recurso | Web Copilot | Windows | macOS | Mobile | Edge | M365 empresarial |
|---|---|---|---|---|---|---|
| Chat geral | Sim | Sim | Sim | Sim | Sim | Sim |
| Modos de chat pessoais | Conforme plano | Conforme plano | Conforme plano | Conforme plano | Conforme interface | Experiência diferente |
| Voz | Conforme assinatura/região | Conforme assinatura/região | Conforme assinatura/região | Conforme assinatura/região | Contextual | Sim, conforme licença |
| Visão | Conforme licença, durante voz | Durante voz | Verificar app/conta | Câmera durante voz | Contextual | Sim, conforme licença |
| Upload | Sim, sujeito a limites | Sim, sujeito a limites | Verificar fluxo | Sim, sujeito a limites | Dependendo do produto | Sim, sujeito a limites |
| Pesquisa profunda | Sim | Conforme versão | Pela Web; não nativo no artigo | Verificar disponibilidade | Conforme interface | Ferramentas de pesquisa próprias |
| Páginas | Sim, conforme conta | Conforme produto | Não nativo no artigo | Verificar | Não é recurso de guia Edge | Sim, conforme armazenamento/licença |
| Navegar com o Copilot | Não equivalente a navegação genérica | Via Edge onde habilitado | Via Edge onde habilitado | Verificar | Implantação limitada | Solução dependente de licenciamento |
| Cowork/Tasks | Conforme migração/conta | Conforme produto | Conforme produto | Conforme produto | Automação pode usar navegador | Conforme TI/licença |
| Podcasts | **Descontinuado** | **Descontinuado** | **Descontinuado** | **Descontinuado** | **Descontinuado** | Não presumir disponível |
| Health | Prévia EUA no artigo | Prévia EUA | Não presumir | iOS em implantação; Android não no artigo | Não informado | Produto pessoal específico |

**Nota:** “Sim” significa presença geral documentada da família; não garante acesso na conta. “Verificar” é proposital para não inventar disponibilidade não demonstrada pela fonte consultada.

## 6. Interfaces, fronteiras e contratos — extração técnica

| Fronteira | Entrada típica | Saída típica | Garantia descrita | Detalhes não divulgados |
|---|---|---|---|---|
| UI → Copilot | prompt texto/voz/arquivo/visão | streaming, texto, áudio, objetos | Contexto e experiência por conta | Protocolo wire completo / SDK interno |
| Copilot → dados corporativos | identidade + consulta | documentos, mensagens e metadados | Graph com ACLs do usuário | Ranking e features proprietárias completos |
| Copilot → Web | solicitação de pesquisa | conteúdo e links/citações | Uso condicionado à tarefa/configuração | Política interna de cache de todas as fontes |
| Copilot → conectores pessoais | autenticação consentida | e-mails, agenda, arquivos e contatos | Permissões da conta conectada | Escopos OAuth exatos para toda combinação |
| Orquestrador → ação | habilidade, parâmetros | resultado da ferramenta/API | Checagens preliminares e governança de agentes | Tokens internos, locks/filas e semântica exata de retry |
| Navegação → Edge | objetivo de navegação | cliques/digitação/navegação | Ações visíveis, pausa e restrições de sites | Código interno de automação e isolamento total |
| Voz/Visão → sessão | áudio e tela/câmera consentidos | fala, explicações | Sessão ativa, controles e limitações próprias | Implementação exata de VAD, ASR, TTS e codecs |
| Conversas → histórico | turnos e artefatos | acesso/revisão/exclusão | Controles por tipo de conta | Estrutura do banco de dados interno |

## 7. Inferências técnicas e recomendações próprias (NÃO atribuídas à Microsoft)

Esta seção é **análise arquitetural autoral**, útil para desenhar outro assistente, mas **não** uma afirmação sobre o código-fonte Microsoft.

1. **Separar produtores de intenção de executores.** Texto, voz, navegador e agentes devem passar pela mesma fronteira de governança antes de provocar efeito.
2. **Identidade real validada no servidor.** Permissões de conectores e arquivos devem derivar de principal autenticado e escopos de recursos, nunca somente de `session_id` fornecido pelo cliente.
3. **Capabilities com escopo e expiração.** Aprovações para enviar e-mail, alterar arquivos ou navegar não devem servir indistintamente a múltiplas operações.
4. **Executor próprio para ações sensíveis.** Evitar entregar a agentes um callable arbitrário ou capacidade irrestrita de shell.
5. **Idempotência, journal e reconciliação.** Uma ação concluída com falha de resposta deve poder ser investigada sem duplicar pagamento, mensagem ou alteração.
6. **RAG com rastreabilidade.** Manter IDs de fontes, ACLs, datas, versões e confiança da recuperação; não promover texto externo a instrução privilegiada.
7. **Multimodal independente da mutação.** Visão analisa; automação de navegador executa somente com nova avaliação de autoridade.
8. **Observabilidade por fronteira.** Distinguir erro de modelo, falha de conexão, erro da ferramenta, recusa política e ação que ocorreu mas não foi confirmada ao usuário.
9. **Capacidade progressiva.** Um catálogo de ações/Skills/Plugins deve informar risco, necessidade de consentimento, origem e compatibilidade.
10. **Testes adversariais.** Concorrência de confirmações, repetição, sessão expirada, permissão revogada, ferramenta desconhecida, prompt injection e efeito concluído sem resposta.

### 7.1 Modelo de capacidades recomendado para estudo

```yaml
# Exemplo conceitual — NÃO é formato oficial do Microsoft Copilot.
capability:
  id: unique-action-id
  principal: authenticated-user
  session_id: issued-and-verified-session
  action: connector.email.send
  resource: mailbox-or-provider-account
  args_digest: sha256-of-canonical-arguments
  expires_at: monotonic-deadline
  max_consumptions: 1
  approval_status: approved
  origin: user-confirmed
```

## 8. Descobertas importantes e possíveis armadilhas documentais

1. **A central não é um inventário de funções ativas.** Podcasts está listado, mas foi desativado em agosto/2026.
2. **A nomenclatura mudou.** Algumas páginas antigas falam de Copilot Tasks; artigos atuais descrevem migração para Cowork.
3. **Há redirecionamentos.** Links `/pt-br/microsoft-copilot/` passam para `/pt-br/microsoft-365-copilot/` ou `/pt-br/edge/copilot/`.
4. **Contas pessoais e empresariais divergem.** Work IQ, Graph tenant, Entra e governança de TI não são recursos implícitos de toda conta pessoal.
5. **Artigos antigos de privacidade são expressamente históricos.** A atualização do app em 18/08/2026 exige consulta aos documentos novos.
6. **A documentação tem inconsistências editoriais pontuais.** Idiomas de voz apresenta nota editorial no corpo; macOS contém frase de atalho com referência a Windows.
7. **Informações de disponibilidade são voláteis.** A documentação destaca países e assinaturas diferentes para Edge Navigate, Vision, Health e recursos de voz.
8. **Não há fonte pública suficiente para “extrair a arquitetura interna completa”.** O que se pode extrair fielmente é a arquitetura lógica, processamento de prompts, política de dados, ações e extensibilidade declarada.

## 9. Índice completo de destinos da central consultada

### Acesso e primeira utilização

1. **Introdução:** https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-microsoft-365-copilot-chat
2. **Regiões e idiomas:** https://support.microsoft.com/pt-br/microsoft-365-copilot/supported-languages-for-microsoft-365-copilot
3. **Experimentar Copilot:** https://copilot.com/
4. **Copilot Microsoft 365 Apps / área de ajuda:** https://support.microsoft.com/pt-br/microsoft-365-copilot/

### Privacidade

5. **Controles de privacidade (legado):** https://support.microsoft.com/pt-br/microsoft-copilot/microsoft-copilot-privacy-controls
6. **Limites etários/jovens (legado):** https://support.microsoft.com/pt-br/microsoft-copilot/microsoft-copilot-young-people
7. **FAQ de privacidade (legado):** https://support.microsoft.com/pt-br/microsoft-copilot/privacy-faq-for-microsoft-copilot

### Plataformas

8. **Windows:** https://support.microsoft.com/pt-br/microsoft-copilot/getting-started-with-copilot-on-windows
9. **Edge:** https://support.microsoft.com/pt-br/edge/copilot/getting-started-with-copilot-in-microsoft-edge
10. **macOS:** https://support.microsoft.com/pt-br/microsoft-copilot/getting-started-with-copilot-on-macos

### Funcionalidades listadas no hub

11. **Modos:** https://support.microsoft.com/pt-br/microsoft-365-copilot/conversation-modes-in-microsoft-copilot
12. **Histórico:** https://support.microsoft.com/pt-br/microsoft-copilot/conversation-history-in-microsoft-copilot
13. **Geração/edição de imagens:** https://support.microsoft.com/pt-br/microsoft-copilot/using-image-generation-in-microsoft-copilot
14. **Upload:** https://support.microsoft.com/pt-br/microsoft-365-copilot/file-upload-in-microsoft-copilot
15. **Deep Research:** https://support.microsoft.com/pt-br/microsoft-copilot/deep-research-in-microsoft-copilot
16. **Páginas:** https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-microsoft-365-copilot-pages
17. **Voz:** https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-voice-features-in-microsoft-365-copilot
18. **Visão:** https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-vision-microsoft-365-copilot
19. **Conectores pessoais:** https://support.microsoft.com/pt-br/microsoft-copilot/connecting-microsoft-copilot-to-other-services
20. **Navegar com Copilot:** https://support.microsoft.com/pt-br/microsoft-copilot/browse-with-copilot
21. **Jornadas:** https://support.microsoft.com/pt-br/microsoft-copilot/copilot-journeys
22. **Shopping:** https://support.microsoft.com/pt-br/edge/copilot/shopping-with-microsoft-copilot
23. **Podcasts (desativado):** https://support.microsoft.com/pt-br/microsoft-copilot/podcasts-in-microsoft-copilot
24. **Tasks (transição Cowork):** https://support.microsoft.com/pt-br/microsoft-365-copilot/using-copilot-tasks
25. **Health:** https://support.microsoft.com/pt-br/microsoft-copilot/copilot-health

**Nota de contagem:** o hub apresenta **25 destinos numerados neste índice** quando se conta o link externo de experimentar o Copilot e a área Microsoft 365; **24 deles são entradas de navegação informativa/documental do portal** no escopo considerado (o link de experimentar é acesso ao produto, não um artigo técnico).

## 10. Documentação técnica oficial complementar

- [Arquitetura e fluxo do Microsoft Copilot](https://learn.microsoft.com/pt-br/microsoft-365/copilot/microsoft-365-copilot-architecture)
- [Orquestrador: descoberta e escolha de ações](https://learn.microsoft.com/pt-br/microsoft-365/copilot/extensibility/orchestrator)
- [Índice semântico no Microsoft Graph](https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot)
- [Copilot connectors vs. Power Platform connectors](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-graph-vs-power-platform-connectors)
- [Zero Trust para Copilot](https://learn.microsoft.com/pt-br/security/zero-trust/copilots/zero-trust-microsoft-365-copilot)
- [Segurança de componentes e fluxo de dados](https://learn.microsoft.com/en-us/compliance/anz/blueprint-copilot-servicecom)
- [Introdução ao Cowork](https://support.microsoft.com/pt-br/microsoft-365-copilot/get-started-with-cowork)
- [Como funciona a Vision](https://support.microsoft.com/pt-br/microsoft-365-copilot/how-vision-microsoft-365-works)
- [Como funcionam as Páginas](https://support.microsoft.com/pt-br/microsoft-365-copilot/how-microsoft-365-copilot-pages-works)
- [Privacidade da nova versão para pessoas físicas](https://support.microsoft.com/en-us/privacy/microsoft-copilot/overview)
- [Memória no Copilot do Microsoft 365](https://support.microsoft.com/pt-br/microsoft-365-copilot/manage-copilot-memory-in-microsoft-365-copilot)

## 11. Checklist de validação de atualização

- [x] Ler página-raiz e percorrer todos os temas principais apresentados.
- [x] Identificar redirecionamentos para Microsoft 365 Copilot e Microsoft Edge.
- [x] Inventariar funções ativas, limitações e itens descontinuados.
- [x] Verificar documentação de arquitetura Microsoft Graph/orquestrador/grounding.
- [x] Separar consumo, trabalho/estudante e extensão via Copilot Studio.
- [x] Distinguir fatos documentados de recomendações arquiteturais autorais.
- [x] Preservar URL por função e data da coleta.
- [ ] Espelhamento textual integral de todos os artigos: **fora do escopo**; substituído por síntese com referências.
- [ ] Código-fonte e infraestrutura interna do serviço: **não publicados nas fontes consultadas**.
- [ ] Varredura exaustiva de todo `support.microsoft.com`: **fora do escopo** (foi delimitada à central fornecida e páginas técnicas relevantes).
- [ ] Garantia de disponibilidade no Brasil para cada recurso: **não verificável apenas por essas páginas**, pois depende de rollout e conta.

---

**Conclusão:** a arquitetura pública do Copilot é uma composição de interfaces multimodais, gerenciamento de identidade, grounding em dados autorizados, orquestração com ferramentas/agentes, modelos generativos e execução via serviços conectados ou navegador, sob controles próprios de privacidade e permissão. Este documento é uma base de referência técnica, não especificação de implementação proprietária.
