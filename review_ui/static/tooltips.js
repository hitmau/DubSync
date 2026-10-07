/* Ajuda contextual curta sem adicionar ruído visual aos painéis. */
(() => {
  const help = {
    playButton: "Inicia a análise. Durante a leitura de um arquivo, clique para pausar e salvar o ponto do bloco. Depois, escolha continuar dali ou apagar apenas esse bloco e reiniciar.",
    pauseButton: "Pausa a análise. O progresso do trecho e os eventos já salvos permanecem no projeto.",
    stopButton: "Encerra a sessão atual e preserva os dados que já foram processados.",
    previewToggle: "Reproduz ou pausa o vídeo local na prévia; a reprodução não analisa áudio.",
    addRoiButton: "Ativa o desenho de uma nova região onde o OCR procurará diálogos.",
    selectCaptureButton: "Seleciona manualmente a área do vídeo quando a fonte exige recorte de tela.",
    previewFile: "Carrega o arquivo no player para navegar e ajustar as regiões antes de analisar.",
    startFile: "Inicia o primeiro bloco no intervalo entre Início e Finalizar em, usando as ROIs ativas.",
    nextSegmentButton: "Depois que um bloco termina, continua do ponto exato em que ele acabou. Se o tempo configurado ultrapassar a duração do vídeo, analisa apenas o tempo restante.",
    addAudioMarker: "Cria um ponto temporal manual para sincronização futura. Nenhum áudio é capturado ou processado.",
    followDialogue: "Quando ativo, ao reproduzir ou navegar, seleciona automaticamente o diálogo sob o cursor da timeline e mostra seus detalhes.",
    mergeButton: "Une os eventos marcados na tabela, preservando o histórico original.",
    acceptButton: "Confirma o evento selecionado após revisar texto, tempo e imagens.",
    correctButton: "Salva o Texto Corrigido sem substituir o resultado OCR original.",
    adjustButton: "Salva os novos tempos de início e fim e os metadados do evento.",
    mergePrevious: "Une este evento com o anterior na mesma sessão.",
    mergeNext: "Une este evento com o próximo na mesma sessão.",
    splitButton: "Abre uma prévia para dividir o evento em dois tempos distintos.",
    discardButton: "Marca o evento selecionado como falso positivo; mantém o registro para auditoria.",
    openVideoFile: "Abre o seletor de arquivos para escolher um vídeo MP4 ou WebM no computador.",
    fitPreview: "Atualiza o enquadramento da prévia e das ROIs sobre o vídeo.",
    runCalibration: "Envia alguns frames visuais do trecho à API configurada para sugerir ajustes de ROI e OCR, após consentimento.",
    applyCalibration: "Aplica à próxima análise as sugestões de calibração visual revisadas.",
    saveSettings: "Salva as preferências locais de detecção, pré-processamento, OCR e calibração.",
    layoutMenuToggle: "Abre os layouts desta tela para salvar, criar, renomear, atualizar, aplicar ou excluir organizações dos cards.",
    roiAddWindow: "Adiciona uma janela na qual esta ROI pode gerar diálogos. Informe início e fim na referência temporal escolhida.",
    roiAddPoint: "Adiciona uma posição temporal da ROI; use X, Y, largura e altura em porcentagem do frame completo.",
    roiMarkSpeaker: "Fecha temporariamente este editor para liberar o vídeo. Arraste um retângulo sobre o nome do personagem; o editor reabre para você aplicar as configurações.",
    roiDialogCancel: "Fecha as configurações desta ROI sem aplicar as alterações feitas neste editor.",
    roiDialogSave: "Valida e guarda estas regras de ROI para a próxima sessão de análise visual.",
    previewSplit: "Mostra o frame completo no tempo escolhido antes de dividir o evento.",
    cancelSplit: "Fecha a divisão sem modificar o evento.",
    confirmSplit: "Divide o evento no frame e tempo escolhidos, preservando os registros originais.",
    testOpenAi: "Verifica a conexão com a chave local da API OpenAI sem mostrar a chave na interface.",
    cancelSettings: "Fecha a janela de configurações sem salvar alterações pendentes.",
    closeSettings: "Fecha a janela de configurações; alterações não salvas serão descartadas.",
  };
  const dubbingFields = {
    dubSpeed: "Ajusta a velocidade da fala gerada. Use valores entre 0,5× e 2×; alterações podem mudar o ritmo e a duração do take.",
    dubDuration: "Define uma duração desejada para o áudio. Quando preenchida, a duração alvo substitui o ajuste de velocidade; vazio usa a duração automática.",
    dubDenoise: "Ativa a redução de ruído do modelo durante a geração. Pode deixar a fala mais limpa e também alterar detalhes da voz.",
    dubNumStep: "Escolhe o número de etapas da geração: 16 prioriza rapidez; 32 tende a levar mais tempo e pode melhorar a qualidade. O resultado depende do modelo.",
    dubGuidanceScale: "Controla a intensidade com que o modelo segue o texto e as instruções. Mudanças podem afetar fidelidade e naturalidade; comece pelo valor padrão.",
    dubTShift: "Parâmetro interno de deslocamento temporal do OmniVoice. O efeito depende da versão do modelo; mantenha o padrão se não estiver comparando resultados.",
    dubPositionTemperature: "Temperatura de amostragem dos elementos de posição. O efeito exato depende do modelo; valores diferentes do padrão podem mudar a variação da fala.",
    dubClassTemperature: "Temperatura de amostragem dos elementos de classe. O efeito exato depende do modelo; mantenha o padrão como referência para comparar mudanças.",
    dubLayerPenalty: "Parâmetro interno de penalidade entre camadas usado pelo OmniVoice. O efeito depende da versão do modelo; ajuste em pequenos passos e compare com o padrão.",
    dubNormalizeText: "Pede ao modelo para normalizar números antes da síntese, ajudando na leitura em voz alta. Confira datas, siglas e valores depois de gerar.",
    dubPreprocessPrompt: "Na clonagem, prepara o áudio e o texto da amostra antes de criar a referência vocal. Não se aplica a perfis de voz desenhada.",
    dubPostprocessOutput: "Ativa o pós-processamento do OmniVoice para tratar silêncios longos no áudio gerado.",
    dubPadDuration: "Adiciona este tempo de silêncio nas extremidades do áudio gerado. Use 0 para não acrescentar silêncio.",
    dubFadeDuration: "Aplica uma transição suave no início e no fim do áudio para reduzir cortes bruscos. Use 0 para desativar.",
    dubAudioChunkDuration: "Define a duração máxima aproximada de cada trecho quando o texto longo for dividido para geração.",
    dubAudioChunkThreshold: "Define a partir de qual duração o áudio/texto longo pode ser dividido em trechos. Abaixo desse limite, a divisão não é iniciada.",
  };
  const sectionHelp = {
    ".dub-advanced-settings > summary": "Abra para ajustar parâmetros avançados enviados ao OmniVoice nos próximos takes. Os valores padrão são um ponto de partida seguro.",
  };
  const byTab = { youtube: "Configura link e download do YouTube para análise visual local.", file: "Usa um arquivo local ou um vídeo já baixado do YouTube.", images: "Mostra o crop original e os frames completos do evento.", text: "Mostra OCR original, texto corrigido e confiança.", metadata: "Mostra origem, tempos e hashes do evento.", lifetime: "Define os intervalos em que esta ROI fica ativa na linha do tempo.", speaker: "Configura a área vinculada que contém o nome do personagem.", motion: "Configura posição e tamanho da ROI em diferentes tempos do vídeo.", detection: "Ajusta a amostragem e os critérios de detecção visual.", preprocess: "Ajusta contraste, nitidez e outras cópias temporárias usadas pelo OCR.", ocr: "Ajusta o detector e a confiança mínima do OCR local.", calibration: "Configura a calibração visual opcional pela API OpenAI.", markers: "Explica as marcas temporais de diálogo e de sincronização futura." };
  function apply() {
    document.querySelectorAll("button").forEach((button) => {
      if (button.title || button.dataset.tooltip) return;
      const text = button.textContent.trim();
      button.title = help[button.id] || byTab[button.dataset.mode] || byTab[button.dataset.detailTab] || byTab[button.dataset.roiTab] || byTab[button.dataset.settingsTab] ||
        (button.dataset.anchor ? `Abrir ${text.replace(/^\d+\.\s*/, "")} na janela principal.` : "") ||
        (button.classList.contains("detail-thumb") ? `Mostrar ${text} na imagem principal do evento selecionado.` : "") ||
        (text ? `${text}: controle desta área do DubSync; clique para executar a ação indicada.` : button.getAttribute("aria-label") || "Controle do painel.");
    });
    for (const [id, description] of Object.entries(dubbingFields)) {
      const field = document.getElementById(id);
      if (!field) continue;
      if (!field.title) field.title = description;
      const label = field.closest("label");
      if (label && !label.title) label.title = description;
    }
    for (const [selector, description] of Object.entries(sectionHelp)) {
      const section = document.querySelector(selector);
      if (section && !section.title) section.title = description;
    }
  }
  apply();
  let pending = false;
  new MutationObserver(() => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; apply(); }); } }).observe(document.body, { childList: true, subtree: true });
  const tooltip = document.getElementById("timelineTooltip");
  const marker = (target) => target instanceof Element ? target.closest(".timeline-dialogue-point,.timeline-event") : null;
  function move(event) {
    const button = marker(event.target);
    if (!button?.dataset.tooltip) { tooltip.hidden = true; return; }
    tooltip.textContent = button.dataset.tooltip;
    tooltip.hidden = false;
    tooltip.style.left = `${Math.max(6, Math.min(window.innerWidth - tooltip.offsetWidth - 7, event.clientX + 13))}px`;
    tooltip.style.top = `${Math.max(6, Math.min(window.innerHeight - tooltip.offsetHeight - 7, event.clientY + 16))}px`;
  }
  document.getElementById("timelineTrack").addEventListener("pointermove", move);
  document.getElementById("timelineTrack").addEventListener("pointerleave", () => { tooltip.hidden = true; });
  document.getElementById("timelineTrack").addEventListener("focusin", (event) => {
    const button = marker(event.target);
    if (!button?.dataset.tooltip) return;
    const rect = button.getBoundingClientRect();
    move({ target: button, clientX: rect.left, clientY: rect.bottom });
  });
  document.getElementById("timelineTrack").addEventListener("focusout", () => { tooltip.hidden = true; });
})();
