**DubSync**

**Documento Mestre de Contexto e Handoff**

Arquivo vivo para desenvolvedores, Codex e novas conversas

Objetivo: concentrar o que realmente existe no projeto, o que foi
decidido e o que ainda precisa ser verificado.

| **Arquivo**        | DubSync_Documento_Mestre_Contexto_Handoff.md                                   |
|--------------------|----------------------------------------------------------------------------------|
| **Versao inicial** | 1.0                                                                              |
| **Versao atual** | 1.5 |
| **Ultima verificacao do codigo** | 07/10/2026 |
| **Data inicial**   | 06/10/2026                                                                       |
| **Projeto**        | C:\Users\hitma\Documents\GitHub\leitor_legendas\Dupsync                          |
| **Git no checkout inspecionado** | `.git` ausente na pasta Dupsync e na raiz do workspace em 07/10/2026 |
| **Regra**          | Cada desenvolvedor atualiza ESTE MESMO arquivo e o devolve ao final da sua fase. |

**NAO guardar senhas, tokens, chaves de API ou credenciais neste
arquivo.**

# 0. Como usar este documento

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>REGRA PRINCIPAL</strong></p>
<p>Este documento nao substitui o codigo. Ele existe para permitir que
uma nova conversa ou um novo desenvolvedor entenda rapidamente o projeto
sem depender do historico do chat. Cada fase deve atualizar o mesmo
arquivo depois de inspecionar o codigo real.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 0.1 Fluxo de passagem entre desenvolvedores

1.  O desenvolvedor recebe este arquivo e o checkout/pasta de codigo do DubSync.

2.  Antes de alterar qualquer coisa, le o documento inteiro e inspeciona
    o codigo real da sua fase. Verifica se o checkout tem metadados Git; nao
    presume que a pasta seja um repositorio.

3.  Corrige neste arquivo qualquer informacao que esteja desatualizada
    ou que tenha mudado no codigo.

4.  Preenche a secao da sua fase com arquivos, classes, endpoints,
    tabelas, comandos, testes, limitacoes e decisoes realmente
    implementadas.

5.  Atualiza as secoes compartilhadas caso sua fase tenha alterado banco
    de dados, contratos entre modulos, status, caminhos ou dependencias.

6.  Registra a mudanca no Historico de Handoff no fim do arquivo.

7.  Devolve ESTE MESMO arquivo junto com a fase concluida.

## 0.2 Hierarquia de fonte de verdade

| **Prioridade** | **Fonte**                                | **Como usar**                                                    |
|----------------|------------------------------------------|------------------------------------------------------------------|
| 1              | Codigo e migrations atuais               | Fonte de verdade sobre o que esta implementado agora.            |
| 2              | Banco/schema e APIs em execucao          | Confirma contratos reais, nomes de campos e estados persistidos. |
| 3              | Este documento mestre                    | Resume arquitetura, decisoes e handoff entre fases.              |
| 4              | Especificacoes de fase e telas aprovadas | Definem comportamento esperado e UX da fase.                     |
| 5              | Conversas antigas                        | Somente contexto historico. Nao presumir que continuam atuais.   |

## 0.3 Marcadores usados

| **VERIFICADO** | Foi confirmado no codigo, banco ou execucao pelo desenvolvedor responsavel. |
|----------------|-----------------------------------------------------------------------------|

| **PLANEJADO** | Foi decidido para a arquitetura, mas ainda pode nao estar implementado. |
|---------------|-------------------------------------------------------------------------|

| **VERIFICAR** | Informacao conhecida por planejamento/handoff, mas precisa ser confirmada no codigo atual. |
|---------------|--------------------------------------------------------------------------------------------|

| **LEGADO** | Comportamento ou codigo anterior que pode servir de referencia, sem criar acoplamento novo. |
|------------|---------------------------------------------------------------------------------------------|

# 1. Identidade do projeto e caminhos importantes

| **Item**                      | **Caminho / valor**                                     | **Status inicial**                             |
|-------------------------------|---------------------------------------------------------|------------------------------------------------|
| Pasta do codigo DubSync | C:\Users\hitma\Documents\GitHub\leitor_legendas\Dupsync | Pasta fonte; confirme o checkout recebido |
| Projeto vizinho legado | C:\Users\hitma\Documents\GitHub\leitor_legendas\LeitorLegendas | Separado; nao e dependencia importada pelo DubSync |
| OmniVoice local | Dupsync\docker\omnivoice | Provider local incluido; endereco padrao 127.0.0.1:8015 |
| REPLACED | Caminho `I:\SteamLibrary\steamapps\common\REPLACED` citado no plano | Alvo de piloto planejado; confirmar caminho e acesso antes do teste runtime |

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>PRIMEIRA ACAO EM UMA NOVA CONVERSA</strong></p>
<p>Abrir a pasta do codigo, verificar se existe `.git` e ler README/pontos de entrada. Se for repositorio Git, executar `git status`; se nao, registrar que branch/commit nao podem ser confirmados. Localizar models/migrations e comparar este documento com o codigo antes de propor alteracoes.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 1.1 Objetivo geral e estado atual do DubSync

DubSync e uma aplicacao local para catalogar dialogos visualmente, revisar as ocorrencias e produzir takes de dublagem antes de uma futura integracao com jogos. O codigo atual implementa a base do aplicativo, a Fase 1 de captura/revisao visual e a Fase 2 de producao de takes. A integracao runtime da Fase 3 e a execucao durante a gameplay da Fase 4 continuam planejadas; nao devem ser descritas como funcionalidades prontas.

| **Fase** | **Papel** | **Saida principal** | **Estado confirmado no codigo** |
|----------|-----------|---------------------|---------------------------------|
| 0 | Base do aplicativo | Aplicacao web local, launcher Windows, configuracoes e persistencia | Implementada; o prototipo vizinho nao e dependencia do app atual |
| 1 | Captura visual | Falas/ocorrencias revisaveis com texto, tempo e contexto visual | Implementada para arquivos e fluxos YouTube; sem analise de audio |
| 2 | Dublagem | Perfis, fila de TTS, takes, revisao e aprovacao | Implementada com provider OmniVoice local configuravel |
| 3 | Integracao | Adaptador do jogo, mapeamento de gatilhos e pacote runtime | Planejada; campos e endpoint preliminares nao equivalem a integracao funcional |
| 4 | Hora da Jogatina | Runtime leve para executar a dublagem durante o jogo | Visao planejada; executavel/runtime bridge nao implementados |

## 1.2 Principios arquiteturais que devem permanecer

- Para uma futura integracao runtime, preferir gatilhos internos/deterministicos do jogo. Na Fase 1 atual, pixels e OCR sao o metodo principal; a captura nao analisa audio.

- Manter separados fala canonica (`line_id`), ocorrencia (`occurrence_id`) e vinculo ao evento de captura (`event_id` via `capture_links`). Esses conceitos ja existem no schema atual.

- O provider da Fase 2 gera masters WAV. Conversoes para formatos de runtime e empacotamento pertencem a uma futura Fase 3 e nao devem sobrescrever o master.

- Plugins/adaptadores sao infraestrutura planejada para a integracao; o jogo e a entidade que o usuario configura.

- Nao quebrar Fases 1 e 2 para implementar Fase 3 ou 4. O schema usa migrations incrementais em `database/store.py`; preservar os dados existentes.

# 2. Fase 0 - Base do aplicativo e legado

**STATUS DESTA SECAO: REVISADO NO CODIGO EM 07/10/2026; NAO FOI FEITO TESTE DE EXECUCAO NESTA REVISAO.** A Fase 0 e a fundacao de aplicacao, interface, inicializacao, isolamento por projeto e persistencia. O antigo LeitorLegendas fica em uma pasta/projeto vizinho (`..\LeitorLegendas`); o DubSync nao importa sua interface PyQt, overlay, leitor ou TTS. A semelhanca e conceitual, nao um acoplamento executavel.

| **Aspecto** | **Implementacao atual** | **Status/limite** |
|-------------|-------------------------|-------------------|
| UI e navegacao | FastAPI + Uvicorn servem paginas Jinja2 e arquivos HTML/CSS/JavaScript sem framework frontend de build. `/` abre o hub de projetos; `/projects/{project_id}/{area}` abre a area do projeto. | VERIFICADO |
| Launcher Windows | `launcher/Program.cs` implementa o launcher WinForms com bandeja, inicio/parada do backend, porta configuravel e opcao de inicio com Windows. `launcher/build.ps1` gera `Dupsync/DubSyncLauncher.exe`, presente no checkout inspecionado. | VERIFICADO |
| OCR e ROI | O OCR atual e PaddleOCR chamado na analise de quadros de video. ROIs proporcionais ao frame sao implementadas na UI web e aplicadas pelo detector/processador. | VERIFICADO; nao e OCR de gameplay em tempo real |
| Captura de tela | `mss` e usado por `desktop_preview()` como pre-visualizacao do monitor. A analise de falas usa fontes de video, nao um capturador de gameplay ativo. | VERIFICADO |
| TTS legado | Nenhum TTS em tempo real do LeitorLegendas e chamado pelo DubSync. A geracao de takes atual e a Fase 2, via provider OmniVoice local. | VERIFICADO |
| Persistencia | Um SQLite (`Dupsync/.data/dubsync.sqlite3`) via SQLAlchemy atende os projetos; sessoes, eventos e personagens ROI sao escopados por projeto. `database/store.py` cria tabelas ausentes e aplica migracoes incrementais inline, com `PRAGMA user_version` ate v6 e `schema_migrations`. Configuracoes ficam em `.data/settings.json`; dados locais em `.data/` sao ignorados pelo Git. | VERIFICADO NO CODIGO; versao do banco real nao consultada |
| Contexto de projeto | Hub em `/`; area do projeto em `/projects/{project_id}/{area}`. Middleware verifica o projeto e exige contexto (`X-DubSync-Project` ou `project_id`) nas APIs do projeto; o armazenamento continua no banco SQLite compartilhado. | VERIFICADO |
| Retomada de captura | Analise de arquivo local salva ponto de pausa, totais e identidade do video na sessao. Depois de reiniciar o servidor, bloco pausado pode continuar do checkpoint ou ser apagado e reiniciado; somente eventos daquele bloco sao removidos na segunda opcao. | VERIFICADO NO CODIGO; nao confundir com pausa do YouTube |
| Estado Git deste checkout | Nao foi encontrado `.git` em `Dupsync` nem na raiz do workspace examinado; `git status`/branch/commit nao estao disponiveis neste checkout. | VERIFICADO EM 07/10/2026; confirmar ao receber outro checkout |

## 2.1 Mapa do codigo, execucao e dependencias

- **Entry point recomendado:** em `Dupsync`, `.\run.ps1` verifica dependencias externas e inicia o backend. O script usa o Python de `Dupsync/.venv` e executa `python -m Dupsync.app.server --host 0.0.0.0 --port <porta>`. O modulo chama Uvicorn com `Dupsync.app.main:app` e impede instancia duplicada no Windows. O launcher chama o mesmo backend.
- **UI e API:** `app/main.py`; templates `review_ui/templates/projects.html` e `review_ui/templates/index.html`; recursos e interacoes em `review_ui/static/`. A UI envia requisicoes HTTP/JSON para as rotas da API e guarda preferencias de interface no navegador. Os estados de encaixe/minimizacao dos cards e os layouts nomeados tambem ficam no `localStorage`, separados por tela; nao sao dados de dominio nem alteram sessoes, falas ou takes.
- **Versao e contexto:** a aplicacao FastAPI declara `0.2.0`. `require_project_context` valida projetos e define o contexto usado por sessoes, eventos, assets e demais operacoes; chamadas de projeto precisam carregar o ID do projeto, salvo rotas globais explicitamente liberadas.
- **Captura e ROIs:** `capture/models.py` (`FramePacket`, protocolo `FrameSource`); `capture/file_source.py` (`FileVideoSource`); `capture/youtube_source.py` (`YoutubeFrameSource`); `capture/session_manager.py` (sessoes, download, analise, preview); `detector/visual.py` (`Region`, geometria/vida/movimento da ROI e detector visual); `review_ui/static/roi.js` (editor web de ROI).
- **OCR e eventos:** `ocr/engine.py` (`OcrEngine` PaddleOCR lazy); `ocr/preprocess.py`; `events/processor.py`, `events/tracker.py` e `events/dedup.py`.
- **Banco:** `database/models.py` e `database/store.py`; a funcao `_migrate_schema()` aplica migracoes incrementais inline, sem uma pasta Alembic. O codigo aceita schema ate v6 e recusa abrir schema mais novo. A inicializacao chama `Base.metadata.create_all()` antes das migracoes. Tabelas definidas atualmente: `capture_sessions`, `dialogue_events`, `review_history`, `fine_sweep_runs`, `timeline_markers`, `roi_characters`, `schema_migrations`, `projects`, `project_text_replacement_settings`, `game_builds`, `characters`, `voice_profiles`, `voice_reference_assets`, `scenes`, `routes`, `scene_edges`, `capture_traversals`, `dialogue_lines`, `dialogue_occurrences`, `capture_links`, `audio_takes`, `tts_jobs`, `audio_take_review_history` e `interpretation_presets`. Isto descreve o schema definido pelo codigo, nao confirma quais tabelas/migracoes existem no banco local.
- **Versoes de schema presentes no codigo:** v1 `fase1_legacy_baseline` registra a base de captura preexistente; v2 `fase2_dubbing_additive` registra as tabelas aditivas da dublagem; v3 `omnivoice_local_profiles` adiciona metadados de perfil/configuracao de voz; v4 `fase0_project_hub` adiciona contexto de projeto e associa sessoes/ROIs a projetos, cria o projeto legado `replaced` quando necessario; v5 `dubbing_batch_tracking` adiciona rastreio de lotes de TTS; v6 `voice_profile_preview_text` adiciona texto de previa ao perfil de voz. O codigo verifica colunas antes de `ALTER TABLE` e registra versoes em `schema_migrations`; isso e evidencia da logica de migracao, nao da versao do arquivo `.data/dubsync.sqlite3` desta maquina.
- **Arquivos de dados e configuracao:** `.data/dubsync.sqlite3` e `.data/settings.json` sao locais; `.env.local` e usado para `OPENAI_API_KEY`. O `.gitignore` exclui `.data/`, `.env.local`, `.venv/` e arquivos de banco. Nao apagar nem recriar `.data/` para corrigir problema de schema; primeiro inspecionar o banco existente e fazer backup antes de qualquer alteracao manual.
- **Pausa persistente de arquivo:** `POST /api/file/{session_id}/pause`, `/resume` e `/restart` controlam a analise de arquivo local. O checkpoint e guardado no `config_json` da sessao, validando tamanho e `mtime` do video antes de retomar. A recuperacao de sessoes preserva o estado pausado ao iniciar o servidor. A opcao reiniciar limpa os eventos somente da sessao/bloco selecionado. A interface usa `persistent_file_pause` em `/api/health` para detectar se o backend carregado oferece esse fluxo. Nao inferir que a pausa persistente de arquivo tambem se aplica ao fluxo YouTube.
- **Fase 2:** `dubbing/service.py`, `dubbing/api.py`, `dubbing/jobs.py`, `providers/omnivoice.py`, `providers/docker.py` e `docker/omnivoice/`.
- **Dependencias Python fixadas:** `requirements.txt`: FastAPI 0.142.2, Uvicorn 0.54.0, Jinja2 3.1.6, SQLAlchemy 2.0.54, OpenCV Contrib 4.10.0.84, RapidFuzz 3.14.6, MSS 10.2.0, yt-dlp[default] 2026.8.19 e PaddleOCR 3.7.0. `setup.ps1` exige Python 3.13 x64 e instala PaddlePaddle CPU 3.2.2. Node.js 22+ e FFmpeg/ffprobe sao ferramentas externas.
- **Lacuna de instalacao a resolver:** os fluxos YouTube e varios scripts de smoke importam Playwright, e ele esta instalado no `.venv` inspecionado, mas nao consta em `requirements.txt`/`setup.ps1`; o setup tambem nao declara a instalacao do Chromium do Playwright. Uma instalacao limpa pode nao reproduzir esses fluxos.
- **Binario do launcher:** `Dupsync/DubSyncLauncher.exe` esta presente neste checkout; o fonte C# e `launcher/build.ps1` ficam em `launcher/`. O script de build grava o EXE na raiz de `Dupsync`, que e o caminho usado pelo README.
- **Documentacao divergente:** o primeiro paragrafo do `README.md` ainda descreve um milestone sem geracao de voz, embora o modulo Fase 2 atual exista; o tutorial do README tambem conserva alguns nomes/fluxos de UI anteriores. `dubbing/README.md` afirma `PRAGMA user_version=2`, mas o codigo atual migra ate v6. Para comportamento e schema, prevalecem o codigo e o banco realmente inspecionado; os READMEs precisam ser alinhados em uma revisao propria.
- **Testes existentes:** `Dupsync/tests/test_*.py` sao testes `unittest`; os arquivos `*_smoke.py` sao smoke tests de UI/Playwright. A partir da raiz do workspace, o comando de descoberta e `.\Dupsync\.venv\Scripts\python.exe -m unittest discover -s Dupsync\tests -t .`. Esse comando nao foi executado nesta atualizacao do handoff.

**Comando de setup:** em `Dupsync`, `.\setup.ps1` cria `.venv`, instala PaddlePaddle CPU e `requirements.txt`. O app e local, mas o launcher pode escutar em `0.0.0.0` para acesso na rede local; o firewall descrito no README e restrito a rede/perfil privados. Nao exponha essa porta em rede publica.

## 2.2 Hub de projetos, ciclo de vida e arquivos persistentes

O hub em `/` cadastra e organiza projetos independentes. Os dados de cada projeto estao no mesmo arquivo SQLite, relacionados por `project_id`; os arquivos ficam separados pelo `slug` em `.data/projects/<slug>/`. O middleware define o contexto antes das rotas de captura/dublagem, bloqueia API sem projeto (`428`), responde `404` para projeto inexistente e bloqueia escrita em projeto arquivado (`409`). A tela do projeto aceita as areas `capture`, `review`, `dubbing` e `settings`; abrir `/projects/{project_id}` redireciona para `capture`.

| **Parte do hub** | **Implementacao conferida** |
|------------------|-----------------------------|
| Cadastro | Nome do projeto, slug, titulo/versao do jogo, idioma original, idioma da captura, idioma da dublagem, plataforma, narrativa (`linear`, `branching` ou `unknown`), notas e capa PNG/JPG/WebP ate 8 MB. Slug duplicado retorna conflito; cria tambem registro de build quando versao ou plataforma sao informadas. |
| Status/lista | Estados `active`, `paused`, `completed` e `archived`; lista pode filtrar status/palavra e ordenar por atividade, nome, criacao ou progresso. Arquivar e bloqueado enquanto ha captura ativa/pausada; restaurar retorna a `active`. |
| Resumo do card | Resumo consulta duracao coberta, dialogos, personagens e takes a partir do banco. A porcentagem/etapa e uma leitura derivada de dados atuais, nao um campo manual universal. |
| Manifesto e diretorios | Ao criar/abrir o projeto, `ProjectStorage.ensure_project()` cria `sources/`, `capture/`, `frames/`, `crops/`, `characters/`, `dubbing/voices/`, `dubbing/audio/`, `dubbing/temp/`, `dubbing/exports/`, `dubbing/logs/` e `runtime/`; grava `project.json`. `project_id` identifica o registro; `slug` forma o diretorio. |
| Seguranca de arquivos | Resolvedor confina caminhos ao diretorio do projeto; capa e demais assets nao podem apontar para fora dele. Arquivar oculta/bloqueia uso normal, nao apaga os dados. |

Rotas principais do hub: `GET/POST /api/projects`, `GET/PATCH /api/projects/{id}`, `GET /api/projects/{id}/summary`, `GET /api/projects/{id}/cover`, `POST /api/projects/{id}/open`, `/archive`, `/restore` e `/status`. O endpoint de capa entrega o arquivo se ele ainda existir no armazenamento.

## 2.3 Launcher, servidor, configuracoes e fronteiras da Fase 0

- **Inicializacao:** `launcher/Program.cs` e um WinForms pequeno. O `DubSyncLauncher.exe` fica na raiz de `Dupsync`; `launcher/build.ps1` o recompila. Launcher e backend possuem mutexes distintos para uma unica janela/servidor por usuario/processo. O launcher inicia o backend compartilhado com `run.ps1`, recebe a porta, atualiza status/URLs e mantem o processo enquanto minimizado na bandeja.
- **Porta e Windows:** padrao `8766`; valor em `%APPDATA%\DubSync\port.txt`. O checkbox de inicializacao grava/remove o valor em `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Fechar a janela esconde na bandeja; menu de contexto oferece as acoes do launcher. Aplicar porta pede elevacao para ajustar regra de firewall local/restrita a rede privada conforme o codigo do launcher. O servidor usa `0.0.0.0`, portanto a exposicao depende tambem da regra do firewall/rede do Windows.
- **Comandos:** a partir de `Dupsync`, `.\setup.ps1` configura Python 3.13 x64, `.venv`, PaddlePaddle CPU e requirements; `.\run.ps1` inicia o servidor, le a porta salva, usa `python -m Dupsync.app.server --host 0.0.0.0 --port <porta>`; `.\launcher\build.ps1` recompila o launcher. `Ctrl+C` encerra execucao pelo terminal. FFmpeg/ffprobe e Node.js 22+ precisam existir no PATH.
- **Health:** `/api/health` informa `ok`, disponibilidade de FFmpeg/ffprobe/Node/yt-dlp, FPS alvo/analise e suporte a pausa persistente de arquivo. `ok` nao significa que o video ou OCR foram processados com sucesso.
- **Configuracoes persistentes:** `app/settings.py` le e grava `.data/settings.json` atomicamente e limita/normaliza valores recebidos; os grupos atuais sao `detection`, `preprocess`, `ocr`, `calibration`, `review`, `dubbing` e `appearance`. `OPENAI_API_KEY` pode vir de `.env.local`; a API/UI nao retorna o segredo.
- **Limites das configuracoes:** `analysis_fps` 2–15; limiar medio de mudanca 0,2–20; fracao de pixels alterados 0,001–0,2; OCR periodico 0,25–8 s; observacoes estaveis/ausentes 1–8. Preprocessamento aceita `fast`, `balanced` ou `thorough`; contraste 0,5–2,5, brilho -80–80, limiar 40–245 e morfologia 0–5. OCR aceita confianca 0,05–0,95 e lado maximo de deteccao 640–2560 px. Revisao controla preview da divisao e descarte permanente. Dublagem restringe o endpoint OmniVoice a loopback (`127.0.0.1`/`localhost`/`[::1]` na porta 8015), timeout 30–1800 s e formato master WAV 24 kHz mono. Estes limites sao aplicados pelo sanitizador mesmo se o JSON local tiver valores invalidos.
- **Cuidado com dados:** `.data/` e ignorado pelo Git. Guarda SQLite, ajustes, cache PaddleOCR, sessoes, frames/crops, videos de origem e arquivos de projeto. O codigo de migracao e ate v6, mas o `PRAGMA user_version` do banco ativo nao foi consultado em 07/10/2026. Antes de qualquer alteracao manual, copiar o banco e verificar schema/tabelas existentes. Nenhuma limpeza ou migracao manual foi feita nesta revisao.
- **Fora da Fase 0:** `mss.desktop_preview()` fornece uma imagem do monitor para ajuste/preview; a captura de dialogos usa arquivo/video e ROIs, nao captura gameplay ao vivo. A analise visual nao extrai audio. Dublagem via OmniVoice e Fase 2; runtime integrado ao jogo ainda nao esta implementado.
- **Documentacao a alinhar depois:** o inicio de `README.md` ainda diz que o primeiro milestone nao gera voz, apesar da Fase 2 atual; partes do tutorial tambem usam rotulos antigos. `dubbing/README.md` ainda informa schema v2, embora `database/store.py` aplique ate v6. Estes READMEs nao foram alterados neste pedido.

### 2.4 Ciclo de inicializacao e diagnostico

1. O launcher ou `run.ps1` le a porta configurada e chama o entry point `Dupsync.app.server`; se a porta nao for definida, o padrao e 8766. A validacao exige porta entre 1 e 65535.
2. No Windows, o servidor adquire mutex nomeado por pasta de instalacao antes de iniciar Uvicorn. Se ja existir instancia, encerra com codigo 3 e orienta abrir o icone da bandeja. O launcher tambem evita duplicar a propria janela; sao protecoes diferentes: uma para UI/controle e outra para backend.
3. Uvicorn carrega `Dupsync.app.main:app`; a aplicacao configura banco/storage e exposicao das rotas. O host padrao do servidor e `0.0.0.0`, necessario para conexao na rede local. O middleware exige contexto do projeto, mas isso nao e autenticacao de usuario; o codigo inspecionado nao apresenta login para proteger a API. A regra de firewall/rede privada e a barreira operacional, nao exponha a porta na internet.
4. `GET /api/health` e um diagnostico superficial de processo/dependencias. Um retorno `ok` confirma que o endpoint respondeu, nao que OCR, fonte de video, permissao de arquivo, Chromium, OmniVoice ou uma analise completa funcionaram.
5. Ao fechar a janela do launcher, ele oculta para a bandeja. A opcao **Finalizar** envia o evento nomeado de parada ao backend. Reiniciar o PC encerra o processo, mas os dados persistidos ficam em `.data`; o estado de retomada depende do fluxo (checkpoint de arquivo local e pente fino interrompido nao sao equivalentes).

**Fluxo de erro e recuperacao:** falha de porta/servidor deve ser investigada em `.data/launcher.log` e na saida do `run.ps1`; erro de schema exige verificar `PRAGMA user_version`, `schema_migrations` e colunas antes de qualquer reparo. Nunca apagar `.data` como procedimento de diagnostico, porque ali ficam banco, projeto, videos baixados, imagens, sessoes, estado de captura e cache do OCR. Se a chave OpenAI estiver ausente, a calibracao assistida externa nao fica configurada, mas a captura visual local continua independente.

# 3. Fase 1 - Captura e catalogacao de dialogos

**STATUS: IMPLEMENTADA NO CODIGO; REVISAO ESTATICA DOS MODULOS EM 07/10/2026.** O produto desta fase e um evento visual com texto, intervalo temporal, confianca, ROI e evidencias de imagem. A fase nao transcreve audio nem identifica falas por voz. O runtime completo nao foi iniciado e nenhum banco ao vivo foi consultado durante esta atualizacao.

## 3.1 Fontes de video e relogio temporal

| **Fonte** | **Caminho atual** | **O que entrega / limite** |
|-----------|-------------------|----------------------------|
| Arquivo local | `FileVideoSource` em `capture/file_source.py`; duracao e streams consultados com `ffprobe`, quadros lidos pelo OpenCV. | Analisa intervalo em segundos, no maximo quatro horas por bloco, limitado ao fim do arquivo. Nao abre nem decodifica faixa de audio. A indexacao consulta PTS de video; para VP9/WebM ha caminho de timestamp de pacotes para evitar decodificar todos os quadros anteriores ao trecho. |
| YouTube recomendado | `/api/youtube/prepare` cria uma sessao; `YoutubeDownloadJob` chama `download_best_video()` em `capture/youtube_download.py` via yt-dlp. | Seleciona `bestvideo`, desativa playlist/config externa, continua download parcial e valida com ffprobe que existe video e zero streams de audio. O arquivo recebe nome amigavel com titulo e ID; o app passa a usar a fonte local baixada. Reuso da mesma URL/intervalo concluido e do video previamente baixado evita repetir trabalho quando possivel. |
| YouTube direto | `YoutubeFrameSource` em `capture/youtube_source.py` controla Chromium/Playwright e captura canvas do player. | Codigo ainda presente como fonte alternativa/legada; nao e o fluxo recomendado da interface atual. Playwright e Chromium nao estao garantidos pelo `setup.ps1`, entao uma instalacao limpa pode nao suportar esse caminho. |
| Gameplay | Aba Jogo da tela de captura. | Desabilitada; nao ha captura de processo/janela de jogo nesta fase. |

`FrameSource` (`capture/models.py`) e o contrato comum: `open()`, `iter_frames()`, `frame_near(source_time)` e `close()`. Cada `FramePacket` carrega imagem, IDs da fonte/segmento/quadro, `source_time`, `captured_at`, relogio monotonic e qualidade/erro de timing. `source_time` e o eixo persistido da timeline; `captured_at` e instante real de aquisicao e nao substitui o tempo do video. A fonte local prefere timestamps de apresentacao (`best_effort_timestamp_time`); consultas isoladas para preview/revisao podem informar `opencv_seek` ou `preview_seek` e erro estimado. Verificar `timing_quality` ao exigir sincronismo preciso.

Blocos sao sessoes independentes no mesmo arquivo/video e projeto. O proximo bloco parte do fim absoluto do anterior; ao passar do fim do video a duracao e limitada. Reanalisar arquivo no mesmo intervalo ativa substituicao seletiva: busca correspondencia por arquivo, tempo proximo e ROI/caixa, remove duplicatas pendentes antigas, mas preserva eventos `accepted`, `corrected` ou com texto corrigido e mantem os outros intervalos. Nao e um comando para limpar o projeto.

## 3.2 Fluxo de deteccao, ROI e OCR

O fluxo comum a arquivo e fontes `FrameSource` esta em `events/processor.py` (`DialogueProcessor`):

1. Carrega OCR de forma lazy, define `analysis_fps` e percorre o intervalo. Padrao atual: 8 quadros amostrados por segundo. A leitura/indexacao pode percorrer todos os timestamps, mas o OCR nao roda em todos os frames.
2. Para cada ROI ativa, recorta a imagem em coordenadas da imagem original e passa um gate barato `VisualChangeDetector`: reduz a 128 x 64 em cinza e compara diferenca media e fracao de pixels alterados. Padroes atuais: limiar medio 2.4 e fracao 0.006. Mesmo sem mudanca, a regiao volta ao OCR depois de `periodic_ocr_seconds` (padrao 1.25 s).
3. `OcrEngine` usa PaddleOCR CPU, sem classificadores de orientacao/documento. `min_confidence` padrao 0.30; textos curtos com confianca fraca sao rejeitados pelo tracker. Erros ficam nos contadores e mensagens da sessao; nao se tornam dialogos validos.
4. `DialogueTracker` por ROI implementa `EMPTY -> CANDIDATE -> ACTIVE -> END`. Padrao: tres observacoes estaveis para iniciar e duas ausencias para encerrar. Similaridade/continuidade tolera texto que cresce progressivamente durante uma animacao; a melhor leitura/confianca e preservada como candidata final. Mudanca de texto pode encerrar uma fala e iniciar outra.
5. O texto bruto (`original_text`) e observacoes OCR ficam separados do texto processado/corrigido. Normalizacao serve para comparar/deduplicar e nao deve substituir a frase exibida. Regras de substituicao do projeto sao aplicadas a `processed_text` durante analise/refazer OCR; tambem ha preview e substituicao manual em lote com historico.
6. Evento e assets sao gravados no SQLite/area de sessao. Progresso inclui quadros lidos/amostrados, OCR, falhas, falas, duplicatas, tempo da fonte, estagio e atividade recente.

As configuracoes editaveis vivem em **Configuracoes** e sao armazenadas em `.data/settings.json`. Valores base atuais (`app/settings.py`):

| **Grupo** | **Padroes principais** |
|-----------|------------------------|
| detection | `analysis_fps=8`, `change_threshold=2.4`, `changed_pixel_fraction=0.006`, OCR periodico `1.25 s`, estabilidade `3`, ausencia `2`. Faixas sao limitadas pelo sanitizador. |
| preprocess | modo `balanced`, contraste `1.15`, brilho `0`, CLAHE ativo, threshold Otsu, valor manual `180`, morfologia `1`, upscale inteligente ativo, sharpen desligado, fallback de confianca `0.78`. |
| ocr | confianca minima `0.30`, lado maximo de deteccao `1280`, limpeza de artefato HUD final desligada. |
| calibration | `gpt-5.4-mini`, amostra de `10 s`, `6` frames; limites de 3 a 30 s e 3 a 10 frames. Requer chave e consentimento para enviar as imagens escolhidas a OpenAI; sugestao nao e aplicada automaticamente. |
| review | pre-visualizacao de divisao ligada; exclusao permanente desligada. Descartar normalmente e logico/reversivel por filtro; exclusao fisica so e exposta quando habilitada. |

`image_variants()` (`ocr/preprocess.py`) mantem a imagem original e constroi vistas OCR: original, cinza/CLAHE, upscale em recortes pequenos, limiarizacao e variantes de nitidez/inversao conforme o modo. `fast` usa apenas o original; `balanced` pode parar apos resultado com confianca media suficiente; `thorough` avalia variantes adicionais, limitado a seis. A imagem de origem e salva sem as transformacoes OCR.

### Configuracao das regioes de dialogo

`validate_regions()` limita uma sessao a oito ROIs; sem configuracao, gera uma ROI ampla inferior padrao. Cada caixa usa `x`, `y`, `width`, `height` proporcionais ao frame, entre 0 e 1. Uma ROI pode ter:

- vida util por janelas temporais absolutas ou relativas ao inicio da analise (ate 64 janelas);
- movimento com keyframes temporais absolutos/relativos (ate 64 pontos); a geometria e interpolada entre posicoes;
- `speaker_region` opcional e acoplada a ROI, para ler nome do falante. A janela acompanha o deslocamento da ROI;
- `roi_id` estavel para associar eventos, personagens detectados e cor.

Se o OCR da area do personagem estiver ligado, o pipeline compara a leitura com perfis da ROI; correspondencia aproximada forte reutiliza perfil, nome novo pode criar perfil automaticamente e anotar origem/tempo para confirmacao humana. O campo manual Personagem tambem cria/reutiliza perfil da ROI. O app pode guardar foto e referencia MP3/WAV, mas a captura visual nao usa a referencia de voz.

### Regra de formacao de evento e deduplicacao

O tracker mantem estado por ROI. Uma leitura abaixo da confianca minima (ou texto muito curto sem confianca alta) e tratada como ausencia; uma observacao estavel amadurece de `CANDIDATE` para `ACTIVE`. Enquanto o texto segue semelhante, acumula observacoes, atualiza ultimo instante visto e conserva a melhor evidencia/confianca; quando o texto muda, a fala anterior termina e uma candidata nova comeca. Duas ausencias encerram a fala no primeiro instante de ausencia, nao necessariamente no frame da segunda ausencia. Desativar uma ROI ou uma lacuna/descontinuidade de timestamps tambem descarrega o tracker para nao unir falas atravessando intervalos sem evidencia.

Depois da deteccao, `events/dedup.py` compara observacoes/eventos: intervalo entre o inicio novo e fim anterior de 0 a 0,8 s; similaridade RapidFuzz do texto normalizado pelo menos 91%; origem espacial igual quando X difere no maximo `max(28 px, 12% da largura)` e Y `max(18 px, 20% da altura)`; e hash perceptual do recorte igual ou similaridade textual de pelo menos 97%. `store.add_event()` limita a busca aos 16 eventos recentes da mesma sessao. Esses criterios evitam repeticoes de amostragem, mas nao garantem detectar toda repeticao real; reanalise usa tambem escopo de substituicao mais amplo e preserva revisoes humanas conforme descrito em 3.1.

### Custo e interpretacao do progresso

`analysis_fps` controla quadros amostrados para deteccao, nao FPS de reproducao do player nem FPS efetivo de OCR. Primeiro, a fonte prepara/indexa timestamps; depois a leitura amostrada executa o gate visual e chama OCR so em mudanca ou quando vence o periodo maximo sem leitura. Portanto, uma sessao pode exibir atividade de indexacao sem ainda contar OCR. `frames_read`, `frames_analyzed`, `ocr_calls`, `ocr_failures`, `events_created` e `events_deduplicated` medem coisas distintas. O painel de progresso e informativo; nao deve ser interpretado como garantia de que a legenda foi reconhecida.

## 3.3 Evento persistido, OCR e contexto visual

As tabelas de captura estao em `database/models.py`; a Fase 1 usa principalmente:

| **Tabela** | **Conteudo usado pela captura** |
|------------|---------------------------------|
| `capture_sessions` | projeto, fonte/URI, inicio/duracao solicitada, idioma, estado, `config_json`, erro e datas. `config_json` guarda caminho, ROI, bloco, parametros congelados da analise, downloads e checkpoint quando pausado. |
| `dialogue_events` | ID/ordem, `session_id`, `segment_id`, tempos inicial/final, OCR bruto, processado, normalizado e corrigido, observacoes, confianca, caixa, paths/assets, hashes, personagem/emocao, `review_status`, qualidade/erro de timing, frame e merge. |
| `review_history` | cada acao e dados alterados, inclusive acao em lote; preserva trilha editorial. |
| `roi_characters` | nome/alias/cor/notas, ROI e projeto, ativo, foto e metadados de referencia de voz. |
| `timeline_markers` | marcadores manuais como `audio_start`; e anotacao futura, nao analise nem sincronizacao automatica de audio. |
| `fine_sweep_runs` | execucao de auditoria visual do bloco, andamento, quadros/OCR, achados e erro/inconclusivos. |

Assets de captura de uma sessao nova: `.data/projects/<slug>/capture/sessions/<session_id>/`; `capture/session_manager.py` grava `asset_root` na configuracao e `store.session_asset_dir()` resolve o diretorio. O download YouTube fica organizado em biblioteca de video do projeto e a sessao referencia o caminho/manifesto. Nao presumir que remover um arquivo da biblioteca seja seguro enquanto sessoes o referenciam.

Para cada evento, o processor salva crop original da legenda e, quando os frames estao disponiveis, quadros completos com roles `pre_1000`, `pre_500`, `pre_250`, `start`, `best`, `end` e `post_250`. Busca pre-evento usa margem de proximidade; `post_250` so entra se o quadro estiver suficientemente proximo. Assets ausentes nao sao inventados. `assets_json` guarda tipo, path relativo a sessao, `source_time`, offset, frame/segmento, timing e SHA-256. O recorte e os frames sao PNG; preview pode ser JPEG/base64. Observacoes guardam cada leitura OCR, tempo, texto bruto/processado, confianca e bbox.

O `event_id` identifica uma deteccao daquela sessao e nao e o ID canonico de uma fala. O modelo separado da Fase 2 (`dialogue_lines`, `dialogue_occurrences`, `capture_links`) conecta evento e roteiro; uma mesma fala canonica pode ter ocorrencias diferentes. Esta separacao permite preservar repeticoes, caminhos e origens do jogo.

### Campos e significado temporal

- `start_time` e `end_time` sao segundos no eixo do video de origem, nao tempo de parede. `duration` e derivada desses extremos; eventos manuais e cortes podem ter `timing_quality` diferente de eventos com timestamps confiaveis.
- `original_text` representa leitura OCR original; `processed_text` aplica normalizacao/regras configuradas; `corrected_text` e o texto editorial quando o revisor corrige. A interface/consumidores escolhem o texto efetivo seguindo essa separacao; nao se deve apagar o bruto ao substituir/corrigir.
- `confidence` vem da leitura OCR observada e pode nao representar a certeza humana sobre personagem, emocao ou texto corrigido. `bbox` e em coordenadas do frame completo, mesmo quando a ROI foi recortada.
- `assets_json` e `ocr_observations_json` guardam evidencias e historico tecnico da leitura. Paths sao relativos ao diretorio da sessao; o endpoint de assets revalida sessao/projeto e caminho para impedir escapar da raiz.
- `review_history` explica alteracoes humanas e em lote; status atual sozinho nao substitui essa trilha. `discarded` e `merged` continuam sendo registros, salvo uso deliberado da exclusao definitiva habilitada.

## 3.4 Estados e acoes de revisao implementadas

`review_status` persistido nao e a mesma coisa que estagio de processamento da sessao nem uma divergencia do pente fino:

| **Valor gravado** | **Uso na Captura** |
|-------------------|--------------------|
| `pending` | Novo evento aguardando decisao humana. Continua `pending` mesmo quando a confianca baixa apenas muda seu filtro/cor. |
| `accepted` | Confirmado sem alteracao textual. |
| `corrected` | Texto revisado/corrigido ou evento resultante de uniao; `corrected_text` preserva a forma editorial separada do OCR original. |
| `discarded` | Descartado logicamente e mantido no banco/historico, normalmente acessivel pelo filtro. |
| `merged` | Evento de origem incorporado a outro; escondido da lista normal, mas conservado com referencia `merged_into`. |

A UI colore pendentes de baixa confianca (limiar atual `0.68`) em vermelho e demais pendentes em amarelo. Confirmados/corrigidos contam como verdes no resumo de captura; descartados sao cinza e fora da meta de dialogos ativos. Achados de pente fino sao sinalizacao adicional, nao valor de `review_status`. A regra de cor/contagem e implementacao da Captura, nao um estado universal de todo o app.

| **Acao** | **Efeito principal e limite** |
|----------|------------------------------|
| Confirmar / Corrigir texto | Atualiza estado/campos e acrescenta `review_history`. OCR original nao e apagado. Salvar personagem/emocao sincroniza o perfil/ligacao do evento. |
| Ajustar tempos | Persiste inicio/fim; atualiza vinculos de captura da Dublagem para manter source times alinhados. |
| Descartar | Marca `discarded`; nao remove registro nem imagens. Exclusao definitiva depende da opcao `review.permanent_discard` e confirmacao na UI; pode remover dados de dublagem ligados se nao houver outra ligacao e nao houver job em geracao. |
| Unir | Eventos da mesma sessao e proximos no tempo; o primeiro torna-se principal, recebe o texto combinado/corrigido, os demais recebem `merged`/`merged_into`; gravado no historico. |
| Dividir | Ponto deve ficar estritamente dentro do intervalo; cria dois eventos com tempos, textos e evidencias visuais; preview pode ser desligado em Configuracoes → Revisao. Mudar o ponto com preview ligada exige carregar preview novo. |
| Duplicar | Copia o texto corrigido/processado do evento selecionado para o tempo atual do playhead; cria novo evento pendente e registra procedencia. Bloqueado durante analise ativa. |
| Refazer OCR | Le um frame dentro do evento usando ROI salva (ou bbox se ROI nao existir), registra nova leitura e crop/frame. Retorna `applied=false`: nao sobrescreve automaticamente OCR inicial nem correcao; usuario decide como aplicar. |
| Criar manual | Grava evento `pending`, `timing_quality=manual`, texto declarado pelo revisor e confianca definida pelo fluxo; tenta anexar frame inicial se a fonte local estiver disponivel. Nao apresenta o texto como OCR. |
| Replace / substituicao | Regras localizadas por projeto podem ser aplicadas a analises futuras e novo OCR. Preview e aplicacao em lote no texto de eventos sao rotas separadas; alteracoes entram no historico e podem sincronizar falas ja vinculadas. |
| Confirmar/remover varios | Operacoes em lote mantem historico; remover marcado e descarte logico. Exclusao definitiva e uma acao individual protegida por configuracao. |

## 3.5 Pente fino, pausa e continuidade por bloco

- **Pente fino:** so inicia quando a sessao esta `complete` e o video local e acessivel. `events/fine_sweep.py` revisita o bloco a 4 FPS, aplica ROI e OCR, compara observacoes confiaveis com eventos e procura texto estavel fora dos eventos. Pode sinalizar divergencia OCR/correcao, possivel fala ausente ou leitura inconclusiva. Registra progresso/achados em `fine_sweep_runs`; e auditoria somente leitura: nao altera texto revisado nem cria automaticamente eventos.
- **Pausa duravel de arquivo local:** `POST /api/file/{id}/pause` espera finalizar a leitura corrente e salva checkpoint, contadores, tamanho e `mtime_ns` do arquivo em `config_json`; estado `paused` sobrevive ao reinicio do backend/PC. `POST /api/file/{id}/resume` retoma o mesmo bloco no checkpoint e recusa se o video foi modificado/movido ou o ponto nao e valido. `/restart` limpa somente os eventos daquele bloco e volta do inicio. A UI confirma a escolha. Isso e diferente de encerrar uma sessao YouTube; nao promete retomada do player ao mesmo frame.
- **Progresso e blocos:** `/api/sessions/{id}` retorna sessao/status/progress; `/api/sessions/{id}/video-plan` fornece faixa e sessoes conhecidas do video. Proximo bloco cria outra sessao no fim do intervalo anterior, mantendo o projeto/fonte/idioma/ROI relevantes. Download YouTube concluido pode ser reutilizado por video ID; sessao/intervalo concluido identico pode ser reaberto.

## 3.6 Interface, rotas e comandos da Fase 1

Na area **Captura**, a tela `review_ui/templates/index.html` e `review_ui/static/app.js` ligam quatro regioes funcionais: fonte/intervalo/controles e editor de ROIs; status da sessao/atividade; player e timeline; lista, detalhes e painel Revisao e Edicao. `roi.js` implementa desenho/movimento/tamanho/edicao de ROI. `dock.js`/`dock.css` permitem mover cards entre zonas, formar abas, recolher, destacar como flutuante e redimensionar grupos; o estado usa `dubsync.docks.v1`. O motor da Captura usa zonas predefinidas e nao oferece a mesma divisao recursiva em quatro bordas da Dublagem. A tabela possui filtros de sessao, status, personagem, emocao e confianca; os detalhes separam Imagens, OCR e Texto, Metadados. O duplo clique na timeline busca o inicio; um clique no evento seleciona sem reposicionar o playhead.

### Fluxo operacional completo da captura

1. Abrir/criar um projeto no hub; entrar em **Captura** para fixar o contexto `project_id` que todas as sessoes/eventos usarao.
2. Selecionar um arquivo local ou preparar um video YouTube. No fluxo recomendado do YouTube, baixar primeiro o video sem audio para a biblioteca local do projeto; confirmar que o arquivo e valido antes de iniciar a analise.
3. Definir idioma e intervalo do bloco. Blocos sao faixas em segundos absolutos da fonte. Ajustar o comprimento do proximo bloco nao altera retroativamente sessoes concluidas.
4. Configurar ROI de dialogo no frame real do video. A caixa deve cobrir o texto de fala; se houver nome separado, configurar a area do falante. Usar vida/movimento quando a legenda mudar de lugar durante o trecho; a geometria normalizada se adapta a resolucao.
5. Iniciar analise. A interface pode primeiro indexar timestamps e carregar PaddleOCR; durante a execucao, acompanhar `stage`, tempo-fonte, quadros amostrados, chamadas/falhas de OCR e eventos criados. Em arquivo local pode pausar e retomar checkpoint; se escolher reiniciar, a limpeza e limitada a esse bloco.
6. Quando concluir o bloco, inspecionar a timeline e a lista. Abrir cada evento, comparar texto bruto/processado/corrigido com crop e frames completos, ajustar intervalo e personagem/emocao e confirmar, corrigir, unir, dividir ou descartar.
7. Se a revisao pedir nova leitura, usar **Refazer OCR** sabendo que o resultado e uma observacao, nao substituicao automatica da correcao humana. Regras Replace modificam texto processado, nao os pixels nem o OCR bruto. Usar pente fino concluido como auditoria visual adicional, sem tratar resultado como transcricao garantida.
8. Iniciar o proximo bloco no limite seguinte da fonte. Manter o mesmo projeto/video para que timeline, sessoes, personagens e evidencias continuem relacionadas. Evitar remover o video da biblioteca enquanto uma sessao ou frame ainda depende dele.

**Resultado esperado:** ao final existe uma colecao revisavel de `dialogue_events`, imagens/crops e historico, associada a sessoes e projeto. Isso ainda nao e um roteiro de falas universal nem uma confirmacao de que todas as falas foram capturadas; a conversao em falas canonicas/ocorrencias pertence ao fluxo da Fase 2 e requer revisao de repeticoes/caminhos.

| **Grupo de rotas** | **Rotas atuais mais usadas** |
|--------------------|------------------------------|
| Fonte/preview | `GET /api/health`, `GET/PUT /api/settings`, `GET /api/file-preview`, `GET /api/desktop-preview`, `POST /api/video-library/select-file` (seletor Windows). Calibracao: `POST /api/settings/calibrate` e `/api/settings/calibration/apply`; envio de imagens a OpenAI exige consentimento. |
| YouTube | `GET /api/youtube/coverage`, `POST /api/youtube/prepare`, `POST /api/youtube/{id}/start`, `/pause`, `/resume`, `/stop`. |
| Arquivo/blocos | `POST /api/file/start`, `/api/file/next-block`, `POST /api/file/{id}/pause`, `/resume`, `/restart`; `GET /api/sessions`, `/api/sessions/{id}`, `/preview`, `/video`, `/video-plan`. |
| Dialogos | `GET /api/events`, `/api/events/summary`, `/filter-options`; `POST /api/events/{id}/review/{accept|correct|update|discard}`, `/merge`, `/{id}/split`, `/{id}/split-preview`, `/{id}/duplicate`, `/{id}/retry-ocr`, `/manual`, `/bulk-accept`, `/bulk-discard`; `DELETE /api/events/{id}` so se exclusao definitiva estiver habilitada. |
| ROI/personagens | `GET /api/roi-characters?roi_id=...`, `PUT /api/roi-characters/{roi_id}`, sincronizacao de personagens detectados com eventos, endpoints de foto e referencia MP3/WAV; os arquivos e tamanho/tipo ficam ligados ao perfil da ROI. |
| Marcadores / pente fino / Replace | `GET/POST /api/sessions/{id}/markers`, `DELETE /api/markers/{id}`, `GET/POST /api/sessions/{id}/fine-sweep`; `GET/PUT /api/project/text-replacements`, preview/aplicacao atual em `/api/events/replace/preview` e `/api/events/replace`. |

Todas as rotas de projeto, exceto APIs globais liberadas, exigem contexto em `X-DubSync-Project` ou `project_id`; contexto identifica escopo de dados, nao identidade/autorizacao de usuario. Rotas de sessoes/eventos nao devem ser chamadas sem ter aberto o projeto. Arquivos de imagem sao entregues sob `/assets/{session_id}/{path}` com validacao do projeto e do caminho.

## 3.7 Testes existentes, verificacao e limites atuais

Testes de unidade `unittest` com cobertura relacionada a Fase 1: `test_core.py` (tracker, revisao, split, deduplicacao, estados e reanalise); `test_file_source_progress.py` (timestamp, seek, amostragem, progresso e VP9); `test_file_pause_resume.py` (retomada persistente e reinicio isolado do bloco); `test_roi_features.py` (movimento/vida/area do falante); `test_fine_sweep_features.py` (OCR refeito, duplicacao, pente fino); `test_settings_and_markers.py`; `test_projects.py`; `test_replacements.py`. Scripts `*_smoke.py` verificam UI real com Playwright. Comando de unidade da raiz do workspace: `.\Dupsync\.venv\Scripts\python.exe -m unittest discover -s Dupsync\tests -t .`. Os testes nao foram executados nesta revisao documental.

Limites que o proximo desenvolvedor nao deve mascarar como implementados: analise de gameplay ativa; transcricao/analise de audio; diarizacao por voz; deteccao garantida de toda fala; classificacao confiavel de personagem/emocao sem ROI humana; sincronizacao automatica com audio do jogo; runtime de plugins. O OCR pode errar texto estilizado, movimento, flashes, fontes pequenas ou frames sem timestamp legivel. Revisor deve corrigir texto/tempo e conferir evidencias. A rota alternativa Chromium/Playwright depende de instalacao externa; a recomendacao operacional e baixar video sem audio e analisar localmente.

# 4. Fase 2 - Dublagem e producao de takes

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>OBJETIVO</strong></p>
<p>Usar as falas elegiveis da Captura para produzir audio, preservar
historico de takes, aprovar uma versao por fala/ocorrencia e entregar
masters de alta qualidade para a Integracao.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Entidade/Conceito** | **Contrato esperado**                                                         |
|-----------------------|-------------------------------------------------------------------------------|
| line_id               | Identificador estavel da fala canonica.                                       |
| occurrence_id         | Onde aquela fala ocorre no material/jogo.                                     |
| capture event_id      | Evento da sessao de captura; nao usar como ID canonico de runtime.            |
| character             | Personagem persistente independente de uma ocorrencia.                        |
| voice_profile         | Identidade vocal/provedor/configuracao do personagem.                         |
| voice_reference_asset | Referencia de voz autorizada, ligada ao perfil de voz.                        |
| audio_take            | Uma geracao de audio. Regenerar cria novo take, nunca sobrescreve o anterior. |
| tts_job               | Fila persistida para geracao e retry.                                         |

## 4.1 Geracao individual e em lote

| **Modo** | **Comportamento confirmado no codigo** |
|---|---|
| Individual | Take Studio envia uma ocorrencia para POST /api/dubbing/lines/{line_id}/takes. Cada solicitacao cria um AudioTake e um TtsJob novos. |
| Lote | POST /api/dubbing/batch aceita selected, character, block, pending e video_character. O escopo video_character percorre sessoes do mesmo video e evita duplicar ocorrencias. |

Cada nova geracao cria outro take/versionamento para a ocorrencia; nao sobrescreve o historico. O lote valida falas aceitas/corrigidas e exige personagem estavel, nome confirmado e perfil vocal pronto. Se houver itens sem esses requisitos, retorna a lista de pendencias para decisao do usuario; ignore_missing e uma escolha explicita. A geracao individual pode liberar uma fala ainda nao aprovada somente com manual_override=true, mas ainda exige personagem e perfil prontos.

**Estados efetivos (separar take de job):**

| **Registro** | **Fluxo/estados usados** |
|---|---|
| AudioTake | queued → generating → review → approved ou rejected; falhas podem terminar em error e cancelamento antes de iniciar em cancelled. Takes concluidos ficam em review para avaliacao humana. |
| TtsJob | queued → generating → completed; tambem error ou cancelled. Falhas transitorias voltam a queued enquanto houver tentativa, com max_attempts=3. |
| Aprovacao | No fluxo de aprovacao, ao aprovar um take, outros takes antes aprovados para a mesma occurrence_id voltam a review e uma entrada approval_replaced e gravada no historico. |

Os rotulos generated/completed podem aparecer em compatibilidade ou em contagens da interface, mas a transicao normal do worker e take review e job completed. A duracao alvo vem de DialogueOccurrence.target_duration_ms; o worker mede a duracao do WAV gerado, calcula delta em ms e percentual, e a UI classifica pelo valor absoluto: ate 5% bom, ate 15% atencao, acima de 15% ruim. Esse indicador nao ajusta automaticamente o audio nem substitui a revisao.

## 4.2 Duracao, elegibilidade e master de audio

| **Regra** | **Comportamento real** |
|---|---|
| Alvo | AudioTake recebe target_duration_ms da ocorrencia. Se o alvo estiver ausente ou for zero, o percentual de delta fica sem valor e a interface mostra sem alvo. |
| Comparacao | Ao concluir, o DubSync mede a duracao real do WAV via ffprobe e persiste actual_duration_ms, duration_delta_ms e duration_delta_percent. A UI colore pela diferenca absoluta: ≤5% bom, ≤15% atencao e >15% ruim. |
| Elegibilidade | Lotes incluem editorial_status accepted/corrected. A geracao individual tem override explicito para falas nao aprovadas. Falas descartadas nao entram nos lotes normais. |
| Master do OmniVoice incluido | O servico OmniVoice grava WAV PCM_16 na taxa de amostragem do modelo, usando 24 kHz como fallback. O worker copia os bytes retornados para o master final e valida a duracao; nao reamostra nem converte canais. |
| Referencia vocal | Uploads e extracoes de audio sao normalizados por FFmpeg para mono, 24 kHz, PCM16. O original e a copia normalizada sao mantidos como assets de referencia. |
| Conversao para runtime | O codigo ainda nao implementa conversao/empacotamento de runtime; exportar outro codec deve preservar o master. |

**Divergencia de configuracao:** settings.py fixa master_format como wav_24khz_mono, mas o worker nao aplica essa conversao ao WAV do modelo. Logo, 24 kHz mono e o formato das referencias normalizadas, nao uma garantia do master gerado. Antes de prometer esse formato, validar e alinhar implementacao, configuracao e teste do modelo carregado.

## 4.3 OmniVoice e provider de TTS

**Estado verificado no codigo:** existe uma implementacao local incluida em `Dupsync/docker/omnivoice/`, servida por FastAPI no contêiner OmniVoice. O DubSync usa o adaptador `providers/omnivoice.py`, o controlador Docker em `providers/docker.py` e os modulos `dubbing/service.py`, `dubbing/jobs.py` e `dubbing/api.py`. A URL padrao e `http://127.0.0.1:8015` (mapeada pelo Compose para a porta 8000 do contêiner); a configuracao permite o provider desligado/offline e nao prova que o modelo esteja carregado no momento.

O contrato implementado inclui `/health`, `/v1/model/status`, `/v1/monitoring`, `/v1/voices/prepare`, `/v1/tts/generate`, `/v1/jobs/{job_id}` e `/v1/audio/{take_id}`. A fila local e persistida e sequencial; o worker cria take WAV, preserva versoes anteriores e faz retries. Referencias anexadas sao normalizadas localmente antes de serem usadas. Isso descreve o codigo e nao confirma disponibilidade do Docker/modelo nem uma geracao real nesta inspeção.

## 4.4 Mapa real do codigo da Fase 2

**Escopo desta verificacao:** leitura estatica do checkout em Dupsync. Os fluxos abaixo estao implementados no codigo; nao foi consultado o banco local nesta atualizacao nem comprovado que o Docker/modelo OmniVoice esteja ativo e gere audio nesta maquina.

### Modulos e arquivos

| **Responsabilidade** | **Arquivo/caminho** | **Contrato observado** |
|---|---|---|
| Catalogo, importacao, sincronizacao Captura→Dublagem, falas, personagens, perfis, fila, takes, aprovacao e exportacao | Dupsync/dubbing/service.py | DubbingService mantem a logica de negocio e transacoes SQLAlchemy. O initialize cria/reconcilia uma ocorrencia por evento de captura nao mesclado; nao deduplica por texto. |
| API local de Dublagem | Dupsync/dubbing/api.py | Router /api/dubbing, modelos Pydantic e bloqueio de chamadas que nao chegam por loopback. |
| Caminhos e processamento de audio | Dupsync/dubbing/audio.py | Confina caminhos a .data, permite formatos de referencia conhecidos, usa FFmpeg/ffprobe, calcula SHA-256 e duracao. |
| Worker/consumo da fila | Dupsync/dubbing/jobs.py | DubbingWorker: thread local unica e sequencial, espera modelo carregado, executa job, grava WAV e aplica retry limitado. |
| Integracao HTTP do provider | Dupsync/providers/omnivoice.py | OmniVoiceProvider encapsula health/status, preparacao de clone, geracao, consulta de job e download de WAV. |
| Inicio/parada/status do contêiner | Dupsync/providers/docker.py | OmniVoiceDocker usa Docker Compose isolado; auto start esta desligado por padrao. |
| API, fila e geracao do modelo | Dupsync/docker/omnivoice/app/api.py, jobs.py, model_service.py, schemas.py | Servico FastAPI incluido localmente; usa k2-fsa/OmniVoice. |
| Compose e imagem | Dupsync/docker/omnivoice/docker-compose.yml, Dockerfile | Publica somente em 127.0.0.1:8015 e monta dados/cache locais. |
| UI e comportamento | Dupsync/review_ui/templates/index.html, static/dubbing.js, static/dubbing.css | Lista Falas e Ocorrencias, Take Studio, personagens, perfis, preview sincronizado, producao/fila, ajustes e historico de takes. |
| Configuracao | Dupsync/app/settings.py | base_url permitida em loopback:8015, timeout limitado, auto_start_docker=false e master_format fixado como wav_24khz_mono (este ultimo nao e aplicado ao master pelo worker). |

### Persistencia, entidades e migrations

O SQLite configurado fica em Dupsync/.data/dubsync.sqlite3; o estado e os arquivos pertencem aos dados locais e nao foram lidos nesta revisao. database/models.py define, entre outras, estas entidades da Fase 2:

- DubbingCharacter (tabela characters): character_id estavel, projeto, vinculo legacy_character_profile_id com ROI, default_voice_profile_id, nome, display_name, aliases_json, verificacao do nome e active.
- VoiceProfile: character_id, provider/provider_voice_key, voice_mode, idioma, referencia ativa, texto de referencia/preview, velocidade, instrucao, parametros, autorizacao e status.
- VoiceReferenceAsset: perfil, caminhos original/normalizado, duracao, taxa/canais/codec, texto, origem, qualidade e SHA-256.
- DialogueLine: texto OCR de origem e texto para dublagem, personagem, direcao, emocao/intensidade, idiomas e editorial_status; identidade canonica de fala e line_id.
- DialogueOccurrence: occurrence_id, line_id, alvo de duracao, cena/rota, condicoes e campos previstos para gatilho de runtime.
- CaptureLink: liga ocorrencia a event_id/session_id e tempos da fonte. A sincronizacao de correcoes da Captura atualiza o texto espelhado sem sobrescrever adaptacao de dublagem manual, conforme a logica/teste existente.
- AudioTake: registro proprio por geracao, texto/instrucao/configuracoes usados, perfil, alvo/duracao real/delta, master e historico de estado; novas geracoes nao sobrescrevem o audio anterior.
- TtsJob: estado, worker, contador/maximo de tentativas, provider_job_id e batch_id/batch_mode.
- AudioTakeReview (tabela audio_take_review_history): trilha de aprovar, rejeitar e substituicao de aprovacao.
- InterpretationPreset: instrucoes reutilizaveis de emocao/intensidade no projeto.

O schema e definido por SQLAlchemy e database/store.py chama create_all e depois migrations inline, sem Alembic. O codigo reconhece ate PRAGMA user_version=6: v2 registra o baseline aditivo de Dublagem; v3 adiciona configuracoes/perfil vocal e settings do take; v4 adiciona escopo de projeto e campos do hub; v5 adiciona rastreio de lote aos TtsJobs; v6 adiciona preview_text ao perfil. README.md dentro de dubbing ainda fala em user_version=2 e esta desatualizado; nao o usar para inferir o schema atual.

### API do DubSync usada pela tela

Todas as rotas abaixo usam o prefixo /api/dubbing. A API rejeita clientes que nao sejam loopback (127.0.0.1/::1). Upload de referencia e limitado a 50 MiB.

| **Metodo/rota** | **Uso** |
|---|---|
| GET /overview; GET /lines; GET /lines/{line_id} | Indicadores, lista filtravel por sessao/status/personagem/busca e detalhe de fala/ocorrencia. |
| PUT /lines/{line_id} | Persiste dub_text, character_id, emotion, emotion_intensity e direction. |
| POST/PUT /characters; PUT /characters/{id}/voice-profile; GET /characters/{id}/roi-audio; POST /characters/{id}/import-roi-audio | Cadastro/edicao/associacao e leitura/importacao do audio do ROI. Existe alias legado import-roi-mp3 oculto do schema OpenAPI. |
| POST /voice-profiles; PUT /voice-profiles/{id}; PUT /voice-profiles/{id}/active; DELETE /voice-profiles/{id}; DELETE /voice-profiles/{id}/reference; POST /voice-profiles/{id}/sync-roi-audio | Ciclo de vida do perfil, autorizacao/ativacao, troca de referencia e sincronizacao do ROI. |
| POST /voice-profiles/{id}/references | Upload do corpo de audio; query filename e reference_text opcionais; WAV, MP3, M4A, AAC, FLAC, OGG ou OPUS. Normaliza via FFmpeg. |
| POST /voice-profiles/{id}/generated-reference | Recebe WAV de candidato escolhido no modo design; persiste asset oficial e o liga ao perfil autorizado. |
| POST /voice-profiles/{id}/preview | Sintese temporaria sincrona; devolve audio/wav para teste de perfil e pre-candidatos. |
| POST /voice-references/extract; POST /voice-references/{id}/approve | Extrai intervalo de audio de uma sessao de video e permite aprovar qualidade de referencia fora de 3–10 s. |
| POST /lines/{line_id}/takes | Gera/enfileira take individual (HTTP 202). |
| POST /batch | Enfileira selecionadas, personagem no bloco, bloco, pendentes ou personagem no video (HTTP 202). |
| GET /queue; POST /queue/{pause,resume,retry-errors,cancel-pending} | Estado/progresso e controles da fila. |
| GET /takes/{id}; POST /takes/{id}/review; GET /takes/{id}/audio | Detalhe, aprovacao/rejeicao e download/stream do WAV. |
| GET/POST /presets; GET /export; GET /runtime-manifest | Presets, exportacao JSON/CSV de roteiro e manifesto de rascunho. |
| GET /provider; POST /provider/{start,stop,restart} | Estado e controle do Compose OmniVoice. Comandos Docker retornam inicio em segundo plano; isso nao prova modelo carregado. |

Principais payloads: LineChanges aceita somente campos definidos (dub_text ate 4000 chars, character_id, emotion, intensidade 0..1, direction ate 2000); TakeBody leva occurrence_id, perfil opcional, instrucao, velocidade 0.5..2, manual_override e generation_settings; BatchBody aceita ate 500 occurrence_ids e ate 5000 session_ids; TakeReviewBody recebe action approve/reject e nota ate 1000. VoiceProfileBody inclui personagem, nome, idioma, modo clone/design, default_speed, instrucao, preset, textos de referencia/preview, generation_settings e consentimento use_authorized. Campos extras sao rejeitados nos schemas marcados extra=forbid.

### Fila, worker e ciclo do take

1. A API valida fala/ocorrencia, revisao editorial, personagem ativo/verificado, perfil ativo/autorizado/pronto e texto nao vazio. Clone exige referencia ready; design exige atributos/instrucao, consentimento e candidato oficial escolhido para que o perfil seja ready.
2. A transacao cria AudioTake em queued e TtsJob em queued; take_id e novo a cada geracao e version aumenta por occurrence_id.
3. O worker espera o model_status indicar model_loaded. Se estiver offline/carregando, mantem o job na fila. O worker processa um job por vez; pode pausar/retomar. Em clone, prepara a referencia local no provider antes de sintetizar.
4. O provider cria job remoto local, o worker consulta seu status, baixa os bytes WAV, grava em arquivo temporario, verifica duracao com ffprobe e faz replace atomico para o master. O sucesso muda o take para review e o job para completed.
5. Falhas voltam a queued ate tres tentativas; esgotado o limite, take/job ficam error. Jobs pendentes podem ser cancelados. Ao iniciar worker, jobs que estavam generating na sessao anterior sao marcados error para intervencao/retry; nao sao retomados silenciosamente.

### Perfis, personagens e criacao de voz

O character_id do DubSync e a identidade estavel. O cadastro e inicialmente espelhado do personagem do ROI e guarda legacy_character_profile_id; linhas referenciam character_id, e VoiceProfile tambem e filho desse personagem. Assim, trocar nome/alias no catalogo nao cria nova identidade de voz.

Os modos de perfil sao:
- clone: usa referencia autorizada anexada no ROI ou upload; salvar perfil de clonagem pode importar/sincronizar o audio do ROI. Pode-se extrair trecho do audio de uma sessao de video. A referencia original e a normalizada sao mantidas.
- design: o usuario descreve genero/idade/pitch/estilo/ritmo/textura e instrucao. A UI chama tres previews locais, permite ouvir e escolher uma. A escolhida precisa durar 3–10 s; e normalizada e salva como referencia oficial. O perfil passa a usar essa referencia como identidade para takes futuros (provider_voice_key dubsync:design-candidate). Isto e desenho de voz por prompt + selecao de amostra do OmniVoice; nao e treinamento/fine-tuning de um modelo novo.

Em ambos os modos, o nome do personagem precisa estar confirmado e use_authorized deve ser explicitamente true. Referencia curta recomendada: 3–10 s; arquivo aceito de 0,5–60 s, mas fora da faixa recebe needs_review e exige aprovacao. Remover referencia limpa a associacao no perfil; excluir perfil e bloqueado quando ha takes associados, preservando o historico usado.

### Dados em disco, exportacao e limites

- Banco: Dupsync/.data/dubsync.sqlite3 (caminho configurado; banco desta maquina nao consultado).
- Dados por projeto: Dupsync/.data/projects/{slug}/dubbing/; para o slug replaced, raiz .data/projects/replaced/dubbing/.
- Masters: audio/{occurrence_id_sanitizado}/{take_id}.wav. Arquivo e relativo ao data_root e audio_master_path guarda o relativo.
- Temporario do provider: temp/omnivoice/{take_id}.wav; removido depois que master e gravado.
- Referencias: voices/{voice_profile_id}/refs/, com original e copia normalizada; asset registra caminhos relativos ao data_root.
- Logs: logs/jobs.jsonl no projeto; compose em logs/omnivoice-compose.log. O worker omite texto completo/referencia do log de job.
- Cache do modelo: Dupsync/.data/omnivoice-model-cache/. A URL padrao local e 127.0.0.1:8015 e o Compose a mapeia para a porta 8000 interna. auto_start_docker e false por padrao; modelo, GPU/CPU e disponibilidade precisam de verificacao runtime.
- GET /export produz roteiro JSON ou CSV, nao um pacote com masters. GET /runtime-manifest seleciona takes aprovados, mas devolve runtime=not_implemented; nao e um contrato integrado ao jogo.

### Testes existentes e smoke manual recomendado

Os testes de Fase 2 estao em:
- tests/test_dubbing_blocks.py: reabertura de schema v6, escopo e deduplicacao de lote, sessoes de video, contagem de progresso, links evento/ocorrencia e sincronizacao da correcao de captura preservando texto adaptado.
- tests/test_dubbing_roi_bridge.py: upload/ponte de audio ROI WAV/MP3 para personagem da Dublagem.
- tests/test_core.py e tests/test_projects.py: cobertura relacionada a revisao/correcao de Captura, projetos e dados compartilhados.

Comando disponivel (a partir da pasta Dupsync; nao executado nesta atualizacao):
python -m pytest tests/test_dubbing_blocks.py tests/test_dubbing_roi_bridge.py

Smoke manual recomendado, tambem nao executado nesta atualizacao:
1. Iniciar a UI DubSync e o provider local; em GET /api/dubbing/provider confirmar api_online=true e model_loaded=true antes da sintese.
2. Selecionar fala accepted/corrected, personagem com nome confirmado e perfil ativo/autorizado/pronto.
3. Gerar um take individual; acompanhar GET /api/dubbing/queue ate take review/job completed, reproduzir o WAV e conferir alvo/delta.
4. Aprovar o take, gerar uma nova versao e verificar que a anterior retorna a review e que o historico registra approval_replaced.
5. Enfileirar um pequeno lote de selecionadas e conferir progresso, erros/retry e cancelamento sem alterar outras ocorrencias.

### Quadro preenchido da Fase 2

| **Area** | **Mapa confirmado** |
|---|---|
| dubbing service/API | Dupsync/dubbing/service.py e api.py; router /api/dubbing, local-only, endpoints listados acima. |
| provider OmniVoice | Dupsync/providers/omnivoice.py e providers/docker.py; serviço FastAPI incluído em Dupsync/docker/omnivoice/. |
| jobs/queue | Dupsync/dubbing/jobs.py + DubbingService; SQLite com job sequencial, batch, progresso, pause/resume, cancelamento e retry limitado. |
| takes | database/models.py AudioTake + AudioTakeReview; WAV imutavel por versão, revisao, aprovacao/rejeicao/historico. |
| personagens/vozes | DubbingCharacter, VoiceProfile, VoiceReferenceAsset; identidade ancorada no ROI e consentimento explicito. |
| models/migrations | database/models.py e database/store.py; SQLAlchemy create_all + migrations inline ate schema v6; v5 lotes, v6 preview_text. |
| UI Dublagem | review_ui/templates/index.html, static/dubbing.js, static/dubbing.css, static/dubbing-dock.js e static/dubbing-layout.css; usa arvore de divisao 2D e gerente de layouts nomeados compartilhado. |
| tests | tests/test_dubbing_blocks.py e tests/test_dubbing_roi_bridge.py; comandos e limites descritos acima, sem execucao nesta revisao. |

# 5. Fase 3 - Integracao, plugins e publicacao runtime

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>ESTADO</strong></p>
<p>Esta fase e PLANEJADA; o codigo atual nao implementa plugin/adaptador de jogo nem execucao runtime. Existe uma especificacao dedicada da Fase 3. Este documento guarda os contratos e decisoes que as Fases 0, 1 e 2 precisam conhecer para nao bloquear uma implementacao futura.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Decisao**              | **Regra consolidada**                                                                                  |
|--------------------------|--------------------------------------------------------------------------------------------------------|
| Entidade principal da UI | Jogo. Plugins nao ficam permanentemente em uma coluna paralela.                                        |
| Plugins                  | Possuem area/submenu proprio para instalar, atualizar, habilitar, remover e inspecionar.               |
| Vinculo                  | Cada jogo seleciona/configura seu plugin/adaptador ativo; um plugin generico pode servir varios jogos. |
| REPLACED                 | Piloto planejado via Unity IL2CPP + Articy; hipotese sujeita a prova no runtime do jogo.           |
| Trigger preferido        | ID interno/deterministico, por exemplo Articy ExternalId.                                              |
| Fallback                 | Visual Runtime com frames/pixels/OCR/audio quando nao existir integracao interna viavel.               |
| Empacotamento            | Fase 3 converte master para formato adequado ao plugin e gera manifesto/pacote runtime.                |

**Estado dos campos no schema atual:** `dialogue_occurrences` ja contem `runtime_trigger_type`, `runtime_trigger_id`, `scene_id`, `route_id` e `condition_json`; `AudioTake` liga o take aprovado a `line_id` e `occurrence_id`. Esses campos estarem no banco nao significa que exista leitura de gatilhos do jogo, exportador de pacote ou runtime. Antes de alterar o schema, confirme `database/models.py`, `database/store.py` e a versao do banco real.

## 5.1 Gate de aceite do primeiro piloto

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>TESTE OBRIGATORIO</strong></p>
<p>Depois do smoke test de uma fala, executar pelo menos 10 dublagens
reais no REPLACED. Meta: 10/10 falas corretas, nenhum take errado,
nenhuma reproducao duplicada e comportamento previsivel em inicio/fim.
Pausa, avancar, interrupcao e troca de cena entram na validacao conforme
o plugin permitir.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 6. Fase 4 - Hora da Jogatina

**Visao:** o DubSync principal funciona como centro de configuracao,
enquanto um runtime leve pode ficar em background durante a gameplay. Um
Game Bridge/plugin dentro do jogo envia eventos para o Runtime Agent,
que resolve o manifesto e reproduz/entrega o audio conforme o modo de
integracao.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>DubSync.exe (UI, configuracao, producao)<br />
|<br />
+--&gt; DubSyncRuntime.exe (leve, background)<br />
|<br />
+--&gt; Game Bridge / plugin / mod / adapter<br />
+--&gt; Audio runtime ou integracao interna<br />
+--&gt; Visual Runtime como fallback</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

- A Fase 4 nao deve carregar OmniVoice, editor de voz ou ferramentas
  pesadas desnecessarias durante a partida.

- Overlay e atalhos podem ser opcionais para diagnostico; o modo final
  pode ficar invisivel.

- A tela Hora da Jogatina deve consumir os artefatos publicados pela
  Fase 3, nao recriar a logica de integracao.

# 7. Contratos compartilhados de dados

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>ATENCAO</strong></p>
<p>Esta secao precisa ser atualizada por qualquer fase que altere
models.py, migrations ou contratos de persistencia. Nao duplicar tabelas
para resolver conflitos locais.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Conceito**       | **Responsabilidade**                          | **Observacao**                                         |
|--------------------|-----------------------------------------------|--------------------------------------------------------|
| Project            | Agrupa fonte/jogo e configuracoes do trabalho | Confirmar modelo real.                                 |
| Character          | Identidade do personagem                      | Nao confundir com texto OCR do nome em uma ocorrencia. |
| DialogueLine       | Fala canonica                                 | line_id deve permanecer estavel.                       |
| DialogueOccurrence | Ocorrencia da fala                            | Pode receber trigger runtime e contexto de rota.       |
| CaptureLink/Event  | Liga ocorrencia ao que foi capturado          | event_id nao substitui line_id/occurrence_id.          |
| VoiceProfile       | Identidade vocal e defaults                   | Referencia pertence ao perfil, nao a cada fala.        |
| AudioTake          | Versao gerada de audio                        | Multiplos takes, um aprovado por regra da aplicacao.   |
| TTSJob             | Fila persistente                              | Retry/erro/progresso.                                  |
| Runtime binding    | Trigger e pacote do jogo                      | Fase 3.                                                |

## 7.1 Snapshot historico antes da Fase 3

**Nao usar estes numeros como verdade atual sem consultar o banco. Eles
sao apenas um marco de passagem registrado em 06/10/2026.**

| **Dado observado**        | **Snapshot**                                        |
|---------------------------|-----------------------------------------------------|
| dialogue_occurrences      | 52 ocorrencias consultadas no banco daquele momento |
| runtime_trigger_id / type | 0/52 preenchidas                                    |
| audio_takes               | 8 takes registrados                                 |
| takes approved            | 0 no snapshot consultado                            |
| /runtime-manifest         | Rascunho: a rota existe, mas o codigo atual retorna `runtime: not_implemented`; nao e um manifesto pronto para consumo |

**Schema atual no codigo:** `database/store.py` aplica migracoes ate `PRAGMA user_version=6` e registra versoes em `schema_migrations`. O snapshot numerico acima e historico; nao foi consultado o conteudo atual do banco do usuario nesta atualizacao.

## 7.2 Migration rules

- Nao editar banco existente manualmente para “fazer caber” a fase nova.

- Criar migration reproduzivel e reversivel sempre que possivel.

- Preservar dados existentes das Fases 1 e 2.

- Registrar no historico deste documento o nome/ID da migration e o
  motivo.

- Se renomear coluna/API, registrar compatibilidade e estrategia de
  transicao.

# 8. Padroes de UX e linguagem visual

| **Regra**            | **Aplicacao**                                                                                                                 |
|----------------------|-------------------------------------------------------------------------------------------------------------------------------|
| Verde                | Concluido na etapa atual. Sempre usar icone + texto, nao apenas cor.                                                          |
| Azul                 | Pronto/validado para a proxima acao.                                                                                          |
| Amarelo              | Processamento/revisao.                                                                                                        |
| Vermelho             | Erro/bloqueio.                                                                                                                |
| Cinza                | Pendente/nao revisado.                                                                                                        |
| Consistencia         | Fases novas devem seguir estrutura visual e navegacao ja usadas no app, evitando menus laterais que fujam do padrao aprovado. |
| Cards encaixaveis    | Telas novas devem seguir o contrato de cards, minimizacao, arraste, redimensionamento e persistencia da secao 8.2.              |
| Game-first na Fase 3 | Jogo fica em destaque; plugin aparece como propriedade/configuracao do jogo e possui tela administrativa separada.            |

## 8.1 Decisoes de UI que nao devem ser reabertas sem necessidade

- Gerar Take individual permanece no Take Studio e atua somente sobre a
  fala selecionada.

- Geracao em lote fica no painel Producao do Bloco, com escopo e
  progresso explicitamente visiveis.

- Na Fase 3, a tela principal nao exibe uma lista permanente de plugins
  ao lado da lista de jogos.

- Logs detalhados da Fase 3 ficam em Testes/Diagnostico, nao ocupando a
  tela principal o tempo inteiro.

- A tela Hora da Jogatina e de execucao/monitoramento, nao de edicao de
  voz ou producao.

## 8.2 Padrao para cards, encaixe e layouts por tela

**Regra para telas novas:** todo painel de trabalho independente deve ser um card encaixavel, usando o modelo espacial da Dublagem como referencia. A implementacao atual ainda tem dois motores de encaixe: Captura (`dock.js`) organiza grupos em zonas predefinidas; Dublagem (`dubbing-dock.js`) usa uma arvore de divisoes horizontais/verticais. O gerente de layouts nomeados (`layout-presets.js`) ja e compartilhado, mas o motor generico de docking ainda nao foi unificado. Nao apresentar a diferenca entre os motores como paridade existente.

### Comportamento obrigatorio do card

- Cada card tem ID estavel, titulo claro, cabecalho arrastavel e acoes explicitas para minimizar/restaurar e destacar/reencaixar. Controles interativos no cabecalho (botoes, campos, seletores) nao iniciam arraste. Os controles devem ter `aria-label`, estado expandido/recolhido e tooltip que explique a acao.
- O arraste parte do cabecalho. O alvo mostra a borda de encaixe antes de soltar: esquerda, direita, acima ou abaixo cria uma nova divisao; centro adiciona o card como aba do grupo. Cards com abas mostram qual aba esta ativa e permitem restaurar uma aba minimizada.
- Cada divisao tem divisor arrastavel nos dois eixos, proporcao ajustavel e limites minimos para que o conteudo continue utilizavel. Ao inserir, mover, redimensionar ou remover uma divisao, os cards vizinhos devem ocupar automaticamente a area liberada; nao deixar faixas ou buracos vazios no grid.
- Minimizar conserva o card no mesmo ramo/posicao da divisao. Em uma divisao vertical (cards um acima do outro), o card vira uma barra horizontal no rodape da propria celula e o vizinho cresce para baixo. Em uma divisao horizontal (cards lado a lado), vira uma barra estreita vertical, com o titulo girado 90 graus no sentido anti-horario; o vizinho cresce para o lado liberado. Minimizar nao deve mandar o card para o rodape global nem reservar a altura/largura original.
- Um card destacado continua movel e redimensionavel como janela flutuante, tem controle para voltar ao encaixe e preserva sua posicao de origem. O posicionamento deve ser limitado a area de trabalho e corrigido quando a janela mudar de tamanho.
- O corpo do card se adapta a largura e altura recebidas: usar grid/flex com filhos que possam encolher (`min-width: 0`, `min-height: 0`), rolagem dentro do conteudo quando necessaria e sem dimensoes fixas que quebrem as outras orientacoes. Divisores/controles continuam acessiveis por foco e teclado.

### Estado e persistencia

Layout e apresentacao sao preferencia local de interface: salvar automaticamente depois de arraste, encaixe, troca de aba, minimizacao, redimensionamento e destaque/reencaixe. Nao escrever essas preferencias no SQLite e nao alterar estado de sessao, evento, fala, perfil ou take. Cada tela recebe um `scope` proprio; Captura e Dublagem nao podem sobrescrever nem restaurar layouts uma da outra.

| **Escopo atual** | **Chave de estado** | **Formato/observacao** |
|------------------|---------------------|------------------------|
| Captura (`capture`) | `dubsync.docks.v1` | Zonas `left`, `center`, `right`, `bottom` e `float`; grupos guardam IDs dos cards, aba ativa e peso; cards flutuantes guardam x/y/largura/altura; estado tambem guarda minimizados e zona de origem. |
| Dublagem (`dubbing`) | `dubsync.dub.docks.v4` | `version: 4`; arvore com nos `split` (`axis: x/y`, `ratio`, dois filhos) e `group` (IDs/aba ativa); lista de minimizados, cards flutuantes/geometria e alvos/posicao de retorno. |
| Presets nomeados | `dubsync.layout-presets.v1:<scope>` e `dubsync.layout-presets-selected.v1:<scope>` | O primeiro guarda nome, ID, snapshot do layout e `updatedAt`; o segundo guarda o preset selecionado. A UI oferece criar, aplicar, atualizar, renomear, excluir e restaurar padrao por tela. |

Para uma tela nova, registrar seu adapter em `layout-presets.js` com `scope`, `label`, `storageKey`, `extraKeys`, `legacyKeys` e `defaultKeys`. Usar um scope unico e uma chave local versionada; ao adicionar/remover cards, validar os IDs e migrar o estado recuperavel sem descartar o layout inteiro. O formato versionado da Dublagem e a referencia para o futuro motor compartilhado; a forma de armazenamento deve ser centralizada quando os motores forem unificados, evitando criar mais um schema independente por tela.

### Implementacao atual e arquivos de referencia

| **Parte** | **Arquivo/contrato** | **Estado confirmado** |
|-----------|----------------------|-----------------------|
| Captura | `review_ui/static/dock.js`, `dock.css`; `data-layout-scope="capture"` em `#appGrid` | Zonas fixas, grupos/abas, recolher, flutuar e redimensionar; nao equivale a encaixe 2D em qualquer borda. |
| Dublagem | `review_ui/static/dubbing-dock.js`, `dubbing-layout.css`; `data-layout-scope="dubbing"` | Arvore recursiva x/y; encaixe nas quatro bordas ou centro/aba; divisores proporcionais; minimizacao horizontal ou vertical conforme o eixo; flutuacao redimensionavel. |
| Presets por tela | `review_ui/static/layout-presets.js` e `layout-presets.css` | Menu e persistencia nomeada genericos, com escopo isolado por tela no navegador. |
| Proxima tela | Adapter de presets + motor compartilhado baseado no modelo 2D | Padrao definido aqui; antes de declarar implementado, inspecionar a tela nova, persistencia e restauracao real. |

**Lacuna registrada:** `dock.js` da Captura ainda usa o modelo de zonas e `dubbing-dock.js` possui o modelo 2D avancado. Para consistencia total futura, migrar Captura para o motor compartilhado sem apagar os layouts existentes, migrando `dubsync.docks.v1` ou oferecendo recuperacao/restauracao segura. Novas telas nao devem copiar o motor de zonas como se ele atendesse ao encaixe em quatro direcoes.

# 9. Estrategia de testes e nao regressao

| **Nivel**    | **Teste minimo esperado**                                                                         |
|--------------|---------------------------------------------------------------------------------------------------|
| Base/Fase 0  | Aplicacao inicia; banco abre; navegacao principal funciona; nenhum dado de usuario e perdido.     |
| Fase 1       | Capturar fonte curta, criar/mover ROI, detectar ao menos um dialogo, revisar e persistir.         |
| Fase 2       | Gerar 1 take individual, ouvir/medir, regenerar sem sobrescrever, aprovar; executar pequeno lote. |
| UI/layout    | Em cada tela nova, encaixar card nos quatro lados e como aba, minimizar em ambos os eixos, redimensionar, recarregar e aplicar preset; confirmar redistribuicao sem espacos vazios e isolamento por tela. |
| Fase 3 smoke | Observar 1 trigger real, mapear 1 take e executar corretamente.                                   |
| Fase 3 gate  | No minimo 10 dublagens reais: 10/10 corretas, zero take errado, zero duplicacao.                  |
| Fase 4       | Runtime inicia/para limpo, conecta ao jogo e respeita lifecycle de fala conforme adapter.         |

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>ANTES DE MEXER EM UMA FASE ANTIGA</strong></p>
<p>O desenvolvedor deve registrar o teste de regressao que comprova que
a mudanca nova nao quebrou o fluxo anterior. “Compila” sozinho nao e
criterio suficiente.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 10. Onboarding para nova conversa ou novo Codex

**Mensagem recomendada para abrir uma nova conversa:**

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th>Voce entrou no projeto DubSync.<br />
Pasta do codigo:
C:\Users\hitma\Documents\GitHub\leitor_legendas\Dupsync
Git: nesta inspecao, `.git` nao foi encontrado no pacote nem na raiz do workspace; confirme no checkout que receber.<br />
<br />
Antes de alterar qualquer arquivo:<br />
1. Leia integralmente o Documento Mestre de Contexto e Handoff.<br />
2. Verifique se existe `.git`; se existir, execute git status e identifique branch/alteracoes. Se nao existir, registre que este checkout nao fornece esses metadados.<br />
3. Leia README, entrypoints, models, migrations e os modulos da fase em
que vai trabalhar.<br />
4. Compare o documento com o codigo real e aponte qualquer
divergencia.<br />
5. Explique em poucas linhas a arquitetura que encontrou e quais
arquivos pretende alterar.<br />
6. Nao crie banco paralelo, models_v2, runtime_new ou estruturas
duplicadas sem justificar.<br />
7. Preserve compatibilidade com as fases anteriores e use migrations
para schema.<br />
8. Somente depois dessa leitura comece a implementacao solicitada.<br />
<br />
Ao finalizar a fase, atualize ESTE MESMO documento com o que realmente
foi implementado.</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 10.1 Checklist de retorno do desenvolvedor

- Versao/data deste documento atualizadas.

- Secao da fase atual revisada e marcada como VERIFICADO onde aplicavel.

- Mapa de arquivos/classes reais preenchido.

- Migrations e mudancas de schema registradas.

- Novos endpoints/contratos/payloads registrados.

- Comandos de execucao e testes registrados.

- Limitacoes e bugs conhecidos registrados.

- Decisoes de arquitetura que impactam fases futuras registradas.

- Historico de Handoff atualizado.

# 11. Historico de Handoff

**Cada desenvolvedor adiciona uma linha. Nao apagar entradas anteriores;
se algo for substituido, registrar nova entrada explicando a mudanca.**

| **Data**                   | **Fase**          | **Responsavel**                  | **Resumo do que mudou**                                                                          | **Commit/branch**                |
|----------------------------|-------------------|----------------------------------|--------------------------------------------------------------------------------------------------|----------------------------------|
| 06/10/2026                 | Documento inicial | ChatGPT + usuario                | Criacao do documento mestre com contexto conhecido e areas obrigatorias de validacao.            | \-                               |
| 06/10/2026                 | Revisao Fase 0    | Codex                            | Fase 0 conferida contra o codigo; corrigidos status gerais de captura, dublagem, schema e runtime. | sem metadados Git no checkout |
| 06/10/2026                 | Auditoria Fase 0  | Codex                            | Detalhados persistencia/contexto de projeto e pausa retomavel; confirmado caminho do EXE e registradas divergencias dos READMEs/schema. | sem metadados Git no checkout |
| 07/10/2026 | Revisao Fases 0 e 1 | Codex | Refeito o detalhamento do hub, launcher, ciclo de inicio/erro, contexto e limites de configuracao/schema; na Fase 1 foram especificados fonte/tempo, pipeline, tracker, criterios de deduplicacao, progresso, campos persistidos e revisao. Nenhum teste/runtime ou SQLite executado nesta atualizacao. | sem metadados Git no checkout |
| \_\_\_\_/\_\_\_\_/\_\_\_\_ | Fase 0            | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ |
| \_\_\_\_/\_\_\_\_/\_\_\_\_ | Fase 1            | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ |
| 07/10/2026 | Revisao Fase 2 | Codex | Preenchido mapa do codigo, API, schema v6, fila/takes, perfis e referencias, armazenamento, testes existentes e smoke recomendado; runtime OmniVoice nao foi validado. | sem metadados Git no checkout |
| 07/10/2026 | Padrao de UI/layout | Codex | Registrados o contrato de cards encaixaveis, minimizacao horizontal/vertical, arraste para quatro lados/abas, redimensionamento, dados locais por scope, presets nomeados e a diferenca atual entre os motores da Captura e Dublagem. | sem metadados Git no checkout |
| \_\_\_\_/\_\_\_\_/\_\_\_\_ | Fase 3            | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ | \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ |

# 12. Pendencias e perguntas abertas

| **Tema** | **Pergunta/Pendencia**                                                                   | **Dono sugerido** | **Status** |
|----------|------------------------------------------------------------------------------------------|-------------------|------------|
| Fase 0 | O prototipo OCR/TTS legado esta em projeto vizinho e nao e chamado pelo app atual; base, UI, ROI, persistencia e limites estao mapeados na secao 2. | Dev Fase 0 | CONCLUIDO nesta revisao |
| Fase 1   | Mapa de fontes, parametros, ROI, OCR, persistencia, revisao e endpoints foi preenchido na secao 3. Ainda falta validar um fluxo real com video e comparar tempos/quadros na interface desta maquina. | Dev Fase 1 | CODIGO MAPEADO; RUNTIME PENDENTE |
| Fase 2 | Perfis clone/design, criacao de candidatos, fila, takes, revisao e exportacao de roteiro estao implementados no codigo; falta comprovar provider/modelo carregado, sintese real e validar taxa/canais do master, pois master_format nao e aplicado pelo worker. | Dev Fase 2 | IMPLEMENTADO NO CODIGO; PENDENTE runtime |
| UI/layout | `layout-presets.js` e compartilhado, mas Captura e Dublagem ainda usam motores de encaixe distintos. Unificar o docking 2D e migrar a preferencia de Captura sem perder o layout existente. | Dev UI | PADRAO REGISTRADO; UNIFICACAO PENDENTE |
| Schema | Codigo atual aceita migrations ate v6; antes da Fase 3, comparar com `PRAGMA user_version` e `schema_migrations` do banco que sera usado. `dubbing/README.md` ainda cita v2. | Dev Fase 2/3 | VERIFICADO NO CODIGO; banco a conferir |
| REPLACED | Provar acesso ao ExternalId e lifecycle em runtime, pois a analise inicial foi estatica. | Dev Fase 3        | PENDENTE   |
| Runtime  | Definir processo/IPC final do DubSyncRuntime e Game Bridge.                              | Dev Fase 3/4      | PENDENTE   |

<table>
<colgroup>
<col style="width: 1%" />
<col style="width: 98%" />
</colgroup>
<thead>
<tr class="header">
<th></th>
<th><p><strong>QUANDO ESTE ARQUIVO ESTIVER BOM</strong></p>
<p>Uma conversa nova deve conseguir ler apenas este documento,
inspecionar o codigo e explicar com seguranca: o que ja funciona,
onde esta cada parte, quais contratos nao podem ser quebrados e qual e o
proximo passo. Esse e o criterio de qualidade do handoff.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>
