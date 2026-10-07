# DubSync

Catálogo local de diálogos que aparecem visualmente em gameplays. Este primeiro milestone trabalha apenas com pixels: não captura nem analisa áudio, não traduz e não gera voz.

## Iniciar no Windows

1. Na pasta `Dupsync`, execute `.\setup.ps1` uma vez. O prefixo `.\` é necessário no PowerShell para executar um script da pasta atual. O instalador cria `.venv` com Python 3.13 x64 e instala PaddlePaddle CPU e as dependências fixadas. Node.js 22+ e FFmpeg/ffprobe precisam estar disponíveis no PATH; Node fornece o runtime JavaScript usado pelo yt-dlp.
2. Abra `DubSyncLauncher.exe`. Ele inicia o servidor e mostra o estado, o endereço neste PC e o endereço da rede local. A porta inicial é `8766`; altere-a no campo **Porta** e clique em **Aplicar porta**. Ao mudar a porta, o Windows pedirá autorização administrativa para atualizar a regra do firewall restrita ao perfil privado e à sub-rede local.
3. Marque **Iniciar com o Windows** se quiser iniciar automaticamente após entrar na sua conta. Fechar a janela pelo **X** mantém o app na bandeja; clique duas vezes no ícone para reabrir a janela ou use o botão direito para abrir o DubSync, copiar o endereço, reiniciar ou finalizar. **Finalizar** encerra o servidor e o iniciador.

Para iniciar pelo terminal, execute `.\run.ps1`; ele usa a porta salva pelo iniciador e impede uma segunda instância do servidor. Para recompilar o EXE a partir do código-fonte, execute `.\launcher\build.ps1`. O iniciador é pequeno porque usa o Python e o OCR já instalados em `.venv`; mantenha-o na pasta `Dupsync`.

O banco, as gravações silenciosas, o manifesto de frames e as imagens ficam em `.data/`. O diretório está ignorado pelo Git. A porta escolhida fica em `%APPDATA%\DubSync\port.txt`. Para interromper o servidor iniciado pelo terminal, use `Ctrl+C`.

## Tutorial rápido de uso

Este é o caminho recomendado para fazer a primeira análise. O DubSync trabalha somente com a imagem do jogo: não processa áudio.

1. **Escolha o vídeo e o trecho.** Na aba **YouTube**, cole a URL, informe o início e a duração (até quatro horas por bloco) e escolha o idioma esperado nos diálogos. Clique em **Baixar vídeo** e acompanhe o progresso na tela. O download é silencioso e fica em `videos/`. Ao terminar, o app muda para **Arquivo** e seleciona esse vídeo automaticamente. Para um arquivo que já está no computador, selecione **Arquivo** e escolha o caminho diretamente.
2. **Confira o vídeo e marque as áreas de diálogo.** Use o player para encontrar um momento em que o texto esteja visível. Clique em **Adicionar região de diálogo (ROI)** e arraste sobre a região em que os diálogos aparecem; mova uma ROI arrastando seu interior e redimensione pelo canto inferior direito. Adicione outras regiões se o jogo mostrar texto em mais de um lugar. As ROIs restringem a busca visual e podem ser ajustadas antes de cada análise.
3. **Inicie a análise.** Clique em **Iniciar análise** (ou **Analisar arquivo**, no fluxo de arquivo local). O DubSync amostra os frames, procura mudanças nas ROIs e executa OCR nos candidatos estáveis. Acompanhe o estado e os contadores em **Status da Sessão** e **Segmento e Processamento**. Use **Pausar** para suspender e **Parar** para encerrar a análise atual; o player de pré-visualização serve para assistir e navegar pelo vídeo.
4. **Confira os diálogos encontrados.** Os eventos aparecem na timeline e na lista. Clique em um ponto/bloco da timeline ou em uma linha da tabela para selecionar o mesmo evento e levar o player ao início correspondente. Use os filtros para localizar eventos pendentes, descartados ou com confiança baixa.
5. **Revise e corrija.** Em **Detalhes do Evento Selecionado**, abra **Imagens** para conferir os frames da cena e o recorte, **OCR e Texto** para comparar o reconhecimento e a correção, ou **Metadados** para ver os dados do evento. Na faixa **Revisão e Edição**, escolha **Confirmar**, **Corrigir Texto**, **Ajustar Tempos**, **Juntar com Anterior**, **Juntar com Próximo**, **Dividir** ou **Descartar**, conforme necessário. O texto original reconhecido é preservado quando você corrige o texto.
6. **Continue ou refaça um trecho.** Blocos concluídos ficam salvos no SQLite. Em **Arquivo**, informe **Próximo bloco (minutos)** e clique em **Próximo bloco** para continuar exatamente no fim do intervalo anterior. Se o vídeo estiver perto do fim, o DubSync reduz automaticamente a duração ao tempo restante. **Analisar arquivo** executa de novo o intervalo selecionado; ao encontrar um evento na mesma região e no mesmo ponto temporal de uma análise anterior do mesmo arquivo, substitui o registro anterior pelo resultado novo. Os demais eventos daquele vídeo ficam preservados. Na aba **YouTube**, pedir novamente o mesmo intervalo que já foi concluído reabre a sessão salva.

Se precisar melhorar a busca, abra **Configurações** e use as subabas de detecção, pré-processamento e OCR. As configurações salvas valem para análises futuras. A calibração visual por IA é opcional: ela examina uma pequena amostra de imagens, sem áudio, e só altera os parâmetros locais quando você aplica a sugestão. **＋ Início de áudio** apenas registra um ponto para sincronização futura; não captura nem analisa som.

### Organizar os painéis

Arraste um painel pelo título. Solte-o **no centro** de outro quadro para reuni-los em abas; solte-o **perto da borda** para criar um quadro separado antes ou depois. É possível mover quadros entre as colunas esquerda, central e direita e a faixa inferior. Clique nas abas para alternar entre painéis agrupados. O botão **▢** destaca o painel como quadro flutuante: arraste seu título para posicioná-lo livremente, use o canto inferior direito para redimensioná-lo e clique em **▣** para encaixá-lo novamente.

O botão **−** no canto superior direito recolhe um painel até restar só sua linha de título; **+** o expande. Arraste os divisores **↔** e **↕** para ajustar a largura das colunas e o tamanho dos quadros. A organização fica salva neste navegador. **Restaurar layout**, na barra superior, volta à distribuição original.

## Fluxo do YouTube

1. Informe a URL, o início, a duração a analisar (até quatro horas) e o idioma do texto.
2. Clique em **Baixar vídeo**. O yt-dlp baixa a faixa de vídeo de maior qualidade disponível, sem selecionar nem baixar faixas de áudio, listas de reprodução, legendas ou miniaturas. O vídeo fica em `videos/` com título e ID do YouTube no nome; um manifesto JSON o identifica como download do YouTube. O manifesto detalhado da sessão continua em `.data/sessions/<session_id>/download_manifest.json`.
   A interface mostra percentual, volume transferido, velocidade e tempo estimado. Se a transferência for interrompida, o botão **Retomar download** continua do arquivo parcial preservado.
3. Depois do download, o DubSync ativa a aba **Arquivo**, preenche o caminho e informa título, ID do YouTube, resolução e tamanho. O estado `preparing` aparece como **Pronto para calibrar e iniciar**: ajuste as ROIs enquanto assiste ao vídeo no player e então clique em **Iniciar análise**.
4. O player permite reproduzir, pausar e buscar no vídeo inteiro. A timeline de eventos acompanha o playhead; clicar no fundo busca aquele tempo, clicar num evento apenas o seleciona, dar duplo clique busca o início, e arrastar um bloco ou suas bordas edita os tempos. O DubSync analisa a faixa selecionada diretamente do arquivo, usa os timestamps de apresentação do ffprobe e mantém os tempos da fala na linha do tempo original do YouTube. A pausa e a parada atuam na análise local; nenhum navegador é aberto para reproduzir o vídeo.
5. A timeline, a tabela, os detalhes e a área inferior de revisão acompanham os eventos gravados no SQLite. Confirme, corrija, ajuste os tempos, una eventos adjacentes, divida ou descarte falsos positivos. Em **Configurações → Revisão**, desmarque **Mostrar prévia do frame ao dividir diálogos** para abrir a divisão sem buscar a imagem antes da confirmação; o ponto temporal continua editável, e os frames originais dos dois eventos continuam salvos.
6. Na timeline, **um clique** seleciona o diálogo sem mover o playhead; **duplo clique** busca o início. **⧉ Duplicar** copia o texto do evento selecionado para a posição atual do playhead, com procedência explícita das imagens de referência. **◉ Refazer OCR** relê o frame atual dentro do evento (ou o meio do evento) usando a ROI configurada; compare a nova leitura na janela antes de aplicá-la como correção. O OCR original não é substituído.
7. Ao concluir um bloco, a janela mostra **100%** e permite abrir o próximo bloco ou **Passar pente fino!**. A varredura relê visualmente o trecho em 4 FPS, compara os textos salvos e procura texto estável fora dos eventos. O progresso e os indícios aparecem em **Segmento e Processamento**; eventos divergentes e possíveis falas ausentes ficam amarelos na timeline. O filtro **Divergências do pente fino** mostra somente os eventos sinalizados. Resultados são indícios para revisão humana: uma leitura inconclusiva não prova ausência de legenda, e nenhum texto corrigido é sobrescrito.

O campo **Personagem (opcional)** em **Revisão e Edição** atribui a fala a quem está falando. Ao salvar um evento com esse campo preenchido, o DubSync cria ou reutiliza automaticamente o personagem em **⚙ da ROI → Personagem**, associa o evento e registra nas observações o evento e o tempo de origem. Ao abrir a lista da ROI, os valores já existentes nos eventos também são sincronizados; confira se cada valor é realmente um nome, pois o OCR pode confundir texto do jogo com personagens. A leitura de uma área específica do nome continua disponível como complemento. Você pode anexar manualmente um arquivo MP3 ou WAV de referência agora ou depois; o caminho, nome e tamanho são guardados no banco em `.data/character_voice_samples/`. A referência de voz é opcional. Na tela Dublagem, importe o MP3 ou WAV para usá-lo como referência de clonagem; o áudio é normalizado localmente depois da confirmação de autorização.

Os intervalos concluídos ficam registrados nas sessões do SQLite e aparecem no painel **Status da Sessão** para o mesmo vídeo. Pedir novamente o mesmo início, duração e idioma reabre a sessão concluída sem baixar nem analisar outra vez. Uma nova faixa pode reaproveitar o arquivo de vídeo já baixado; somente o intervalo solicitado é analisado.

## Ajustes de OCR e calibração visual

Abra **Configurações** para editar os parâmetros em subabas: amostragem/detecção, pré-processamento, PaddleOCR, calibração OpenAI e marcas temporais. O pipeline aproveita técnicas visuais do LeitorLegendas — CLAHE, limiarização Otsu/adaptativa/manual, ampliação e nitidez — aplicadas em cópias temporárias. Os pixels e os resultados OCR originais continuam preservados; correções humanas são armazenadas à parte.

O arquivo `Dupsync/.env.local` é o local ignorado pelo Git para `OPENAI_API_KEY`. A tela informa apenas se a chave existe, nunca seu conteúdo. **Testar conexão** consulta a disponibilidade da API. Para calibrar, abra uma sessão concluída, escolha uma posição do player e confirme o envio das imagens; o DubSync envia de 3 a 10 quadros reduzidos daquele trecho, sem áudio. A resposta sugere uma ROI e ajustes dos parâmetros locais. Ela é comparada com o PaddleOCR como referência de calibração, não como uma medição validada de CER, e nada muda até **Aplicar sugestão**. As alterações valem para análises futuras.

Cada evento OCR aparece como um ponto na timeline: clique para selecionar sem mover o player e dê duplo clique para ir ao início. **＋ Início de áudio** grava manualmente a posição do vídeo como uma marca futura de sincronização; isso não lê nem processa a trilha de áudio.

## Atalhos e zoom da timeline

Em **Configurações → Atalhos**, clique em uma combinação para gravá-la novamente. Os padrões são Espaço para reproduzir/pausar; setas esquerda/direita para mover pelo passo principal (1 s); Alt+setas para o passo fino (0,1 s); Shift+setas para o passo rápido (5 s); J/K para o diálogo anterior/seguinte; N para adicionar um diálogo manual; `=` e `−` para aproximar/afastar a timeline; Shift+; para ajustar ao trecho inteiro; D para centralizar a vista ampliada no playhead. Os três passos temporais podem ser personalizados em segundos. As combinações ficam salvas localmente no navegador e não interceptam digitação em campos.

Também é possível usar **Ctrl+scroll** sobre a timeline; o zoom mantém o tempo sob o cursor como referência. Os botões **−**, **＋** e **Ajustar** diminuem, aumentam e restauram a vista do trecho inteiro. O modelo segue atalhos úteis do After Effects: Espaço para pré-visualização, J/K para navegar entre itens temporais e `=`/`−` para zoom no tempo ([referência oficial de atalhos](https://helpx.adobe.com/br/after-effects/desktop/get-started/keyboard-shortcuts/keyboard-shortcuts-reference.html)).

**＋ Diálogo** ou o atalho **N** abre a anotação manual no tempo atual do player. O texto entra no SQLite como anotação manual, separado da procedência OCR; quando o vídeo local está disponível, o DubSync também guarda um frame original de contexto.

Ao lado, **◎ Acompanhar diálogo** ativa a seleção automática durante a reprodução ou busca no player. Quando o cursor de tempo entra num evento detectado, a linha da tabela, o bloco da timeline e os detalhes passam a mostrar esse evento sem alterar a posição do vídeo. Clique novamente para desligar. Passe o mouse sobre um marcador de diálogo para ver o texto reconhecido e o tempo em um tooltip.

O botão **⚙** de cada ROI abre três subabas de configuração:

- **Tempo de vida:** marque a opção e adicione janelas de início/fim em segundos. **Vídeo completo** usa o timestamp do arquivo; **Relativo ao trecho** soma o início da análise. Fora dessas janelas, a região não é analisada.
- **Personagem:** marque a opção e informe a área proporcional do nome, ou use **Marcar área do nome na prévia** e arraste um retângulo sobre o vídeo. Essa segunda ROI é lida por OCR ao iniciar um diálogo e o nome detectado preenche **Personagem** quando a confiança permite. O crop e a observação OCR originais ficam preservados. Esta função adapta a ideia de área vinculada do LeitorLegendas sem importar código nem configuração dele.
- **Movimento:** marque a opção e registre a posição inicial e outras posições com tempo, X, Y, largura e altura em porcentagem. Entre dois pontos a ROI move e muda de tamanho por interpolação; depois do último ponto, permanece na posição final. O retângulo da prévia acompanha o player.

Clique em **Aplicar à próxima análise** antes de iniciar uma nova sessão. Essas configurações são salvas junto à sessão e não alteram silenciosamente eventos já processados. Os controles **▢** e **−** ficam agrupados à direita do título de cada card: o primeiro solta/encaixa o painel e o segundo o recolhe ou expande. Os botões exibem ajuda ao passar o mouse.

O download é da melhor qualidade de vídeo disponível, não necessariamente pequeno. O vídeo é preservado para reprocessamento. O yt-dlp e o runtime Node.js são dependências adicionais porque permitem obter os formatos adaptativos atuais do YouTube; FFmpeg/ffprobe já são usados para validar a faixa de vídeo e timestamps. O pacote `yt-dlp[default]` inclui yt-dlp-ejs para desafios atuais do YouTube. Uma alternativa manual seria baixar o vídeo fora do DubSync e usar **Arquivo**, mas isso não atenderia ao fluxo em que o próprio DubSync recebe o link e seleciona a melhor qualidade.

## Arquivo local

Selecione **Arquivo**, informe um caminho local e ajuste início, duração e ROIs. **Pré-visualizar arquivo** mostra o frame de calibração e **Analisar arquivo** processa somente os timestamps de apresentação do vídeo. O DubSync lê a trilha de vídeo; nenhuma trilha de áudio é aberta. Depois de um bloco concluído, **Próximo bloco (minutos)** define quanto processar a seguir; o início é calculado a partir do fim do bloco anterior, e o último bloco usa só o que resta até o final do arquivo.

## Organização

`capture/` fornece frames, relógios, segmentos silenciosos e gravação; `detector/` acompanha mudanças visuais dentro das ROIs; `ocr/` reconhece candidatos; `events/` mantém a máquina de estados e agrupa variações; `database/` persiste sessões, eventos e histórico de revisão; `review_ui/` é a camada local de interface (templates/estáticos servidos por `app/`). A implementação da seleção por canvas usa ROIs proporcionais ao frame e não importa código do projeto vizinho.

O esquema guarda OCR e recorte originais separados do texto corrigido, mais cenas completas em PNG para `−1000`, `−500`, `−250 ms`, início, melhor frame estável, fim e `+250 ms` quando os frames existem. A divisão cria novos assets originais e preserva texto OCR e histórico da operação.

## Estado do milestone

A estrutura visual segue o layout desktop de referência em três colunas com revisão fixa na base e funciona melhor em 1920×1080. Em telas menores, a aplicação mantém o formato desktop e usa rolagem em vez de mudar para mobile. **Jogo** permanece desabilitado nesta fase. O YouTube baixa somente a faixa de vídeo; o ffprobe valida que os arquivos baixados não contêm faixa de áudio. O DubSync não executa reconhecimento de voz.

Uma [validação curta com o gameplay de REPLACED](reports/2026-10-02-replaced-captura.md) confirmou o fluxo visual anterior. O teste de captura ao vivo do link encontrou um erro de reprodução do player; seu diagnóstico está em [reports/2026-10-03-diagnostico-0250-1500.md](reports/2026-10-03-diagnostico-0250-1500.md). Este fluxo de download local substitui a captura do player. A aprovação do milestone de 10–15 minutos e as metas de recall, precisão, duplicatas e erro temporal ainda exigem uma captura e anotação manual completas depois da mudança.
