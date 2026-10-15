# DubSync Desktop

Aplicação nativa WPF (.NET 10) em desenvolvimento ao lado do serviço Python atual. A camada desktop usa a API local existente e não abre nem migra diretamente o SQLite; sessões, falas, evidências e metadados continuam sob responsabilidade do serviço DubSync.

## Estado da migração

- **Fase 0:** shell WPF, navegação, AvalonDock, escala da interface, projetos e configurações. Os presets incluem Captura padrão, Captura Real, Revisão, Compacto e Dois monitores; posições e tamanho da janela, página ativa, modo da Captura e layout personalizado são salvos no perfil do usuário do Windows.
- **Fase 1 — captura por vídeo:** arquivos locais e YouTube usam a API e o banco atuais. O Desktop prepara e reutiliza o download do YouTube, mostra a prévia, permite configurar a ROI, processa o bloco e acompanha o estado salvo da sessão. Reprodução, busca, velocidades de 0,5× a 5×, legendas sincronizadas, pausa/retomada, próximo bloco e revisão de diálogos com evidências também estão disponíveis.
- **Captura Real:** o Desktop seleciona janelas de jogo e solicita Windows Graphics Capture; o serviço Python alimenta o mesmo processador de ROI, detecção de mudanças e OCR. Os perfis Eco, Balanceado e Alta precisão separam cópia dos frames (15/30/60 FPS) da frequência de análise (5/10/20 FPS); o contrato antigo continua aceitando `fps` como frequência de análise. O overlay nativo permite criar, mover, redimensionar, renomear, travar e remover ROIs sobre o jogo, sem espelhar o jogo no Desktop. A calibração por IA pede consentimento para enviar até três frames e só aplica a sugestão à primeira ROI depois de confirmação.
- **Compatibilidade:** OCR, correspondência, sessões, falas e evidências continuam no serviço Python e no banco existente. Dublagem e Integração continuam acessíveis pelos módulos atuais enquanto essas áreas aguardam migração nativa.
- **Validação ainda necessária:** teste visual em DPI alto e em múltiplos monitores; captura real de uma janela de jogo; medição de FPS/CPU/GPU durante gameplay; e verificação end-to-end do encadeamento de fala correspondente até o disparo de take aprovado. A calibração por IA depende da chave e do modelo configurados no serviço.

## Compilar no Windows

No diretório do repositório:

```powershell
dotnet restore desktop\DubSync.Desktop\DubSync.Desktop.csproj
dotnet publish desktop\DubSync.Desktop\DubSync.Desktop.csproj -c Release -o desktop\artifacts\release
```

O aplicativo conecta à API local na porta configurada nas preferências do Desktop (padrão `8766`). Ele não abre nem migra o SQLite diretamente. A Captura Real exige que o serviço Python esteja atualizado e que Windows Graphics Capture esteja instalado; a interface informa quando o endpoint ou o backend não está disponível. A validação real do jogo deve ser feita com o serviço iniciado e uma janela de jogo visível.
