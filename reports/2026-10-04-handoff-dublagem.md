# DubSync — relatório de passagem para a etapa de dublagem

**Data do levantamento:** 04/10/2026  
**Projeto:** `C:\Users\hitma\Documents\GitHub\leitor_legendas\Dupsync`  
**Objetivo deste documento:** permitir que um novo chat continue o desenvolvimento sem confundir o catálogo visual já existente com uma solução de dublagem pronta. O exemplo em uso é um gameplay linear de **REPLACED**, mas a solução de dublagem deve servir também a jogos com escolhas, rotas alternativas e falas repetidas.

## 1. Resumo executivo

O DubSync v0.1.0 é um aplicativo local para extrair, organizar e revisar diálogos que aparecem **na imagem** de gameplays. Ele lê arquivos de vídeo locais ou baixa a faixa visual de um vídeo do YouTube, usa ROIs para localizar texto, detecta eventos e aplica PaddleOCR. Cada ocorrência guarda início e fim em segundos da **linha temporal do vídeo de origem**, texto bruto, texto processado, correção humana, imagens de evidência, personagem opcional, emoção e estado de revisão. A interface permite navegar por blocos, revisar na timeline e acompanhar o progresso.

**Ainda não existe pipeline de dublagem:** o aplicativo não extrai áudio do gameplay, não gera ou clona voz, não gerencia takes de áudio, não faz mixagem e não injeta voz em um jogo. O botão **+ Início de áudio** grava apenas um marcador temporal para um uso futuro. O valor de **Personagem (opcional)** dos eventos cria ou vincula automaticamente um perfil na ROI e ganha uma observação de origem; os valores antigos também são sincronizados. Um MP3 de referência pode ser anexado manualmente depois. O arquivo é armazenado como referência e não é processado.

A premissa de trabalho informada pelo usuário é que as falas já têm **tempo estimado**. Isso é um ponto de partida para um roteiro de dublagem, não prova de que toda a obra foi capturada, revisada ou sincronizada em tempo real. É necessário distinguir **tempo no vídeo usado como evidência** de **gatilho de uma fala dentro do jogo**. Essa diferença passa a ser essencial quando o jogo possui ramificações.

## 2. Estado observado no banco local

Estado do banco `Dupsync/.data/dubsync.sqlite3` verificado em 04/10/2026, após sincronizar os nomes dos eventos com personagens da ROI; os números podem mudar com novas análises e revisões. O vídeo registrado tem duração indicada de **7.256 s (02:00:56)**. Há uma única fonte de vídeo e três sessões locais:

| Sessão | Intervalo pedido | Estado | Eventos | Confirmados/corrigidos | Pendentes | Descartados |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `cd570117…` | 02:50–15:00 | Concluída | 13 | 2 | 11 | 0 |
| `5498afec…` | 15:00–35:00 | Interrompida | 0 | 0 | 0 | 0 |
| `fd5aa8ac…` | 02:50–15:00 | Concluída | 67 | 15 | 38 | 14 |

As duas sessões concluídas cobrem **o mesmo primeiro bloco**. Somar seus 80 eventos como 80 falas distintas seria incorreto: uma nova análise pode sobrepor ou substituir ocorrências da anterior. Na sessão concluída mais recente há **53 eventos não descartados**, dos quais **15 estão verdes** (`accepted`/`corrected`) e **38 ainda aguardam revisão**. Os 55 eventos dessa sessão que têm texto em `character` agora estão ligados a **14 perfis** em `roi_characters`; 2 eventos antigos sem ROI gravada foram associados à única ROI definida nessa sessão. Há ainda um marcador temporal e nenhuma execução de pente fino registrada. O banco local, portanto, **não demonstra cobertura completa das falas do vídeo nem do jogo**. Se houver outras falas preparadas fora deste banco, o novo chat deverá inventariá-las antes de planejar a importação.

O campo `timing_quality` indica a procedência técnica da marca temporal (`exact_pts`, `manual` ou `opencv_seek`); não é um selo de aprovação editorial. O estado verde confirma a revisão do evento, mas também não mede sozinho a exatidão do lip-sync.

## 3. Fluxo atual do aplicativo

1. **Fonte e intervalo.** Selecionar vídeo local ou YouTube; definir início e final do primeiro bloco, idioma e duração dos próximos blocos. O YouTube é baixado como **vídeo sem áudio**. O player mostra o vídeo inteiro; a análise lê só o intervalo solicitado.
2. **ROIs.** Criar uma ou mais regiões de diálogo. Cada ROI pode ter tempo de vida absoluto/relativo ao trecho, posições e tamanhos variáveis por keyframes, e uma área vinculada para OCR do nome do falante. Ocultar a visualização da ROI não equivale a desativar sua análise.
3. **Detecção/OCR.** Frames são amostrados, mudanças visuais dentro das ROIs sinalizam candidatos, o PaddleOCR lê o texto e o rastreador forma eventos. O OCR bruto, variantes de processamento, recortes e frames completos de contexto ficam preservados. Há configuração para limpar artefatos de HUD somente no sufixo do texto processado.
4. **Blocos.** `Iniciar primeiro bloco` usa o início e o final informados. `Próximo bloco` começa no fim do anterior e limita a duração ao que resta do vídeo. A continuidade pode ser manual ou automática até o fim. O card **Progresso Geral do Vídeo** mostra a divisão planejada, bloco atual e blocos concluídos; existe retorno ao bloco anterior.
5. **Revisão.** Timeline com zoom, rolagem, seleção automática ao cruzar o ponto de início de um evento e atalhos configuráveis. Um clique seleciona sem mover o playhead; duplo clique busca o início. É possível confirmar, corrigir texto, ajustar tempos, unir, dividir, duplicar, criar diálogo manual, refazer OCR e descartar. A lista permite filtros e ações em lote. O card **Status dos diálogos** resume verdes, amarelos, vermelhos e descartados.
6. **Pente fino.** Após um bloco concluído, uma segunda leitura visual pode marcar divergências e possíveis lacunas; essas marcas pedem revisão humana e não sobrescrevem texto corrigido automaticamente.

A interface mantém os cards **Segmento e Processamento (Processamento)**, **Revisão e Edição (Revisão)** e **Lista de Eventos (Banco de Dados)** dentro da tela principal; a navegação superior mostra **Captura** e **Configurações**. Os cards podem ser recolhidos, movidos, agrupados em abas, destacados e redimensionados. Layout, atalhos e algumas preferências da interface são salvos no `localStorage` do navegador, não no SQLite.

## 4. Dados e pontos de integração existentes

| Estrutura atual | O que guarda | Implicação para dublagem |
| --- | --- | --- |
| `capture_sessions` | fonte, início, duração, idioma, estado, configuração das ROIs e do processamento | Identifica a tomada de vídeo e o intervalo; não identifica uma cena ou rota do jogo. |
| `dialogue_events` | `event_id`, `session_id`, `start_time`, `end_time`, OCR original/processado, `corrected_text`, confiança, ROI, imagens, `character`, `character_profile_id`, emoção, status | Fonte principal do roteiro inicial. `start_time`/`end_time` são segundos no vídeo de origem, não IDs de fala do jogo. |
| `review_history` | ações de revisão por evento | Permite rastrear correções e operações editoriais; preservar em qualquer migração. |
| `roi_characters` | nome, observações, ROI, referência legada MP4 e referência MP3 opcional | Valores preenchidos em `dialogue_events.character` criam ou reutilizam um perfil da ROI, com observação de evento/tempo e vínculo pelo `character_profile_id`. O OCR da área de personagem pode complementar o campo. O MP3 continua sendo anexado manualmente; nenhum áudio é analisado. |
| `timeline_markers` | posição, tipo e rótulo; hoje só `audio_start` pela API | Marcador manual, não gatilho de execução de dublagem. |
| `fine_sweep_runs` | estado/progresso e indícios da releitura | Evidência de QA visual, não aprovação de voz. |

O campo **Personagem (opcional)** da revisão é agora a origem principal do cadastro. Salvar um evento com esse campo preenchido cria ou reutiliza o perfil na mesma ROI e grava `character_profile_id`; abrir a lista de personagens da ROI também sincroniza os valores dos eventos antigos que ainda não tinham vínculo. Se um evento antigo não tiver ROI gravada, ele é associado automaticamente somente quando a sessão tem exatamente uma ROI. O cadastro inclui uma observação com o evento e o tempo de origem, e pede conferência porque o OCR pode confundir elementos do jogo com nomes. O trecho atual foi reconciliado: **55 eventos vinculados a 14 perfis** na ROI `1a3dc532-943b-46f0-a51c-d0bf02d61f06`. A leitura de uma área separada de nome continua opcional e complementar. O MP3 de referência é anexado manualmente; nenhum áudio é analisado nesta fase. A futura entidade de voz deve ter identidade própria e referência explícita ao personagem, sem depender de comparar nomes escritos em cada evento.

Os eventos expõem dados pela API FastAPI, em especial `GET /api/sessions`, `GET /api/events?session_id=...`, `GET /api/events/{event_id}` e `GET /api/events/summary`. Revisões e edições também têm rotas em `app/main.py`. **Não há rota de exportação de roteiro nem esquema de árvore de escolhas ou de assets de voz** na implementação atual.

## 5. Arquitetura e operação

- `capture/`: leitura de vídeo local, download do YouTube, timestamps, sessões e blocos.
- `detector/`: ROI, janelas de vida, movimento e mudança visual.
- `ocr/`: PaddleOCR, pré-processamento, limpeza de texto e calibração visual opcional.
- `events/`: rastreamento de diálogos, deduplicação, assets e pente fino.
- `database/`: modelos SQLAlchemy/SQLite, persistência e histórico de revisão.
- `app/`: servidor FastAPI, configurações e endpoints.
- `review_ui/`: HTML/CSS/JavaScript da interface, timeline, cards e configurações.
- `launcher/`: iniciador Windows com porta configurável, bandeja, início com Windows e controle de instância única. Há proteção de instância também no servidor.

Na pasta `Dupsync`, executar `.\setup.ps1` uma vez no PowerShell e depois abrir `DubSyncLauncher.exe`; a alternativa de terminal é `.\run.ps1`. Requisitos documentados: Python 3.13 x64 no ambiente `.venv`, Node.js 22+ e FFmpeg/ffprobe. A porta padrão é **8766**, mas pode ser alterada; no levantamento, `http://127.0.0.1:8766/api/health` respondeu com `ok: true`. O launcher mostra o endereço local e o da rede, fica na bandeja quando sua janela é fechada e pode iniciar com o Windows. A porta escolhida fica em `%APPDATA%\DubSync\port.txt`.

`Dupsync/.data/` contém o banco, sessões, imagens e configurações e **não deve ser apagado**. `Dupsync/.env.local` guarda a chave opcional para calibração visual com OpenAI e nunca deve ser copiado para relatórios, commits ou exports. `Dupsync/videos/` guarda vídeos de origem. Antes de qualquer migração de esquema, reanálise ou processamento em lote, fazer backup consistente do SQLite e das imagens referenciadas. Reanalisar um intervalo local pode substituir eventos próximos no mesmo vídeo e remover assets antigos; preservar o estado de revisão antes de fazê-lo.

## 6. Diretrizes para a fase de dublagem

### 6.1. Identidades e relógios separados

Criar uma **identidade estável da fala** (`line_id`) independente de `event_id`, texto OCR e timestamp. `event_id` identifica uma ocorrência capturada numa sessão; pode mudar após dividir, duplicar ou reanalisar. O mesmo texto pode ser dito por personagens diferentes, em rotas diferentes ou em momentos diferentes. Uma fala canônica pode também aparecer várias vezes. Modelar explicitamente essa relação por **ocorrências**.

Manter pelo menos três conceitos temporais: (a) tempo observado no vídeo de captura; (b) posição relativa da fala na cena/sequência, se houver; (c) gatilho do jogo em execução. Em um jogo linear, (a) pode ajudar a planejar (b), mas **não substitui (c)**; pausas, menus, escolhas, velocidade do jogador e replays quebram uma timeline global única.

### 6.2. Jogos com ramificações

Representar a narrativa como cenas/nós e transições, com opções e condições identificáveis, em vez de uma lista única ordenada por minuto. Registrar cada percurso capturado como uma **travessia** com fonte de vídeo, build/versão e sequência de escolhas. Associar cada evento OCR à cena, à rota e à ocorrência correspondente, mantendo o tempo do vídeo como evidência dessa travessia. O mesmo `line_id` pode ser reutilizado em vários percursos, sem perder onde e quando apareceu; alternativas de texto ou personagem exigem linhas ou variantes distintas.

O progresso de cobertura deve considerar **nós, escolhas, condições e ocorrências**, não somente percentual do vídeo analisado. A exportação e a reprodução devem escolher a fala pela transição/condição ocorrida no jogo. Caso o jogo não exponha eventos, IDs ou mecanismo de modificação, definir e testar um método de sincronização observável específico para ele; não prometer dublagem interativa apenas com timestamps de um gameplay gravado.

### 6.3. Roteiro e voz

O roteiro de trabalho deve reunir texto original, texto revisado, idioma, personagem estável, emoção/direção, status editorial, tempo estimado e evidências (`session_id`, `event_id`, recorte/frame, tempo no vídeo). Eventos pendentes ou de baixa confiança devem permanecer sinalizados; descartados não entram no roteiro principal; operações de split/merge devem ser rastreáveis. Uma exportação inicial deve ser **somente leitura e reproduzível**, com versão, critérios de seleção e lista de conflitos entre sessões sobrepostas.

Introduzir perfil de voz por personagem e idioma, separado do nome livre em `dialogue_events`. Permitir múltiplos takes por fala, com referência ao arquivo, duração real, revisão, versão e procedência. O MP3 anexado ao personagem é material de referência manual; a decisão sobre gravação humana, síntese ou clonagem e as características técnicas dos arquivos devem ser tomadas quando o destino de execução e as fontes de voz estiverem definidos. Não alterar OCR bruto, imagens ou correções ao testar vozes.

### 6.4. Integração e qualidade

Primeiro montar uma prévia que reproduza cada take com o vídeo e mostre o ajuste entre duração do áudio e janela estimada da fala. Depois validar casos com fala interrompida, sobreposta, repetida, pausada e escolhida por rota. O export para o jogo deve usar IDs/gatilhos de fala e manifestos de áudio por idioma/versão, com estratégia de substituição ou sobreposição definida para o jogo alvo. Guardar o vídeo de origem e a revisão como trilha de auditoria.

## 7. Modelo proposto para evolução (não implementado)

Um modelo mínimo que não prenda o produto ao exemplo linear:

- `projects` / `game_builds`: jogo, versão e idiomas; evitar assumir que a origem atual é o único projeto.
- `scenes` e `scene_edges`: nós narrativos, escolhas, destino e condições de ativação.
- `capture_traversals`: gameplay usado como evidência, versão do jogo, rota percorrida e fonte de vídeo.
- `dialogue_lines`: `line_id` estável, texto canônico por idioma e direção de fala.
- `dialogue_occurrences`: `occurrence_id`, `line_id`, cena, condição/rota, ordem local e, quando existir, gatilho do jogo.
- `capture_links`: ocorrência ↔ `session_id`/`event_id`, intervalo do vídeo e confiança da associação; muitos-para-muitos quando necessário.
- `characters` / `voice_profiles`: personagem estável, aliases do OCR, idioma e configuração de voz; importar os cadastros de ROI sem perder os dados originais.
- `audio_takes`: ocorrência ou linha, idioma, arquivo, duração, versão, estado de revisão e metadados de produção.

Exemplo conceitual: no vídeo da rota A, uma resposta aparece aos 12:10; no vídeo da rota B, a mesma resposta aparece aos 19:40. Ambos os eventos podem apontar para a mesma linha canônica, mas mantêm **dois vínculos de captura**. Se a escolha B muda a resposta, cria-se outra linha e uma ocorrência condicionada a B. Na execução, a escolha feita seleciona a ocorrência; o minuto do vídeo não decide qual áudio tocar.

Antes de alterar o SQLite, verificar as tabelas reais, criar uma migração **aditiva** e registrar versão de esquema. Não remover colunas ou reescrever eventos antigos para acomodar a nova estrutura. O primeiro export pode existir antes da migração, desde que explicite os campos ainda desconhecidos como `null` e preserve a procedência.

## 8. Próximos passos recomendados ao novo chat

1. **Inventariar as falas disponíveis.** Confirmar se as falas mencionadas pelo usuário estão todas neste SQLite ou também em outros blocos/arquivos. Selecionar a sessão canônica de cada intervalo sobreposto, listar lacunas, pendências e personagens sem perfil. Fazer backup antes de qualquer escrita.
2. **Entregar um roteiro exportável de referência.** Criar export estruturado e legível (por exemplo JSON/CSV de revisão) com `event_id`, sessão, tempos de origem, texto bruto/revisado, personagem, emoção, status e evidência; não incluir descartados como falas finais. Resolver ou sinalizar conflitos de análises repetidas, sem deduplicar só por texto.
3. **Modelar caminhos.** Definir cena, rota, escolha, condição, linha canônica e ocorrência. Implementar um exemplo linear e um exemplo ramificado com reconvergência para provar que a estrutura funciona nos dois casos.
4. **Associar personagens e takes.** Criar cadastro estável de personagem/voz, mapear aliases do OCR, importar/gravar áudio, pré-visualizar no tempo estimado e revisar take por take.
5. **Preparar integração com o jogo alvo.** Levantar como o jogo sinaliza diálogos ou permite modificação; só então escolher formato final, gatilhos e estratégia de sincronização. Validar as escolhas A/B, volta ao menu, replay e falas repetidas.

**Critérios mínimos de aceitação da primeira entrega de dublagem:** um roteiro exportado sem perda dos originais; todas as falas selecionadas para o roteiro com estado editorial e origem rastreável; conflitos de sessão visíveis; personagens não vinculados identificados; um take de teste ligado a `line_id`/ocorrência e pré-visualizável; um pequeno cenário de duas escolhas produzindo o áudio correto em cada ramo. “100% do bloco analisado” ou “100% verde” não devem ser usados como substitutos de cobertura narrativa de todas as rotas.

## 9. Questões que precisam de decisão antes da integração final

- Qual é o jogo alvo, sua versão e a forma permitida de acionar/substituir áudio durante a execução (mod, API/evento, arquivos do jogo ou outra via)?
- As falas com tempo estimado existem apenas no banco atual ou há outros blocos, planilhas, roteiros ou vídeos a importar?
- Quais percursos e escolhas precisam ser cobertos? Há um mapa de cenas/IDs do próprio jogo ou será necessário catalogá-los manualmente a partir das gravações?
- A produção de voz será com gravações humanas, importação de áudios prontos, síntese ou uma combinação? Qual referência pertence a cada personagem?
- Qual idioma é origem e qual idioma será dublado? O `language` da sessão atual descreve o OCR, não define por si só uma estratégia de localização.

## 10. Arquivos para abrir primeiro no novo chat

- `Dupsync/README.md` — fluxo atual e limites; conferir contra o código quando a redação estiver desatualizada.
- `Dupsync/database/models.py` e `Dupsync/database/store.py` — esquema, status, cadastro automático de personagem e operações que podem alterar eventos.
- `Dupsync/app/main.py` — API disponível; verificar inexistência de export e voz antes de criar rotas.
- `Dupsync/capture/session_manager.py` — sessões, reanálise e sequência de blocos.
- `Dupsync/detector/visual.py`, `Dupsync/events/` e `Dupsync/ocr/` — origem dos tempos, ROIs e OCR.
- `Dupsync/review_ui/templates/index.html` e `Dupsync/review_ui/static/` — interface e persistência de preferências no navegador.
- `Dupsync/.data/dubsync.sqlite3` — **somente leitura para inventário inicial**; não enviar nem versionar o banco sem pedido explícito.

### Texto para iniciar o próximo chat

> Continue o DubSync a partir de `Dupsync/reports/2026-10-04-handoff-dublagem.md`. Vamos desenvolver a dublagem usando as falas e seus tempos estimados, preservando o OCR, as revisões e as evidências existentes. O design deve servir a um jogo linear e a jogos com escolhas: tempo de vídeo é evidência, enquanto a reprodução no jogo precisa de IDs/condições de ocorrência. Comece conferindo o banco e identificando blocos/sessões sobrepostas, pendências e personagens sem perfil; depois proponha e implemente a primeira entrega concreta do roteiro exportável e de um modelo de falas/ocorrências preparado para ramificações. Não trate os vídeos e o banco atuais como cobertura completa sem conferir os dados reais.
