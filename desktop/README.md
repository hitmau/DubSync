# DubSync Desktop

Aplicação nativa WPF (.NET 10) em desenvolvimento ao lado do serviço Python atual. A camada desktop usa a API local existente e não abre nem migra diretamente o SQLite; sessões, falas, evidências e metadados continuam sob responsabilidade do serviço DubSync.

## Estado da migração

- **Fase 0:** shell nativo, navegação, painéis encaixáveis, layouts persistidos, escala da interface, projetos e configurações. A verificação visual em DPI alto ainda está pendente.
- **Fase 1 — primeira fatia:** player de vídeo local com busca, velocidades de 0,5× a 5× e legendas sincronizadas aos tempos salvos; ROI desenhável; início de bloco; sessões com pausa/retomada; Captura Real; e revisão de falas com evidências. A faixa sobre o jogo permite criar, mover, redimensionar e remover ROIs; a calibração por IA pede consentimento para enviar até três frames e só aplica a sugestão à primeira ROI depois de confirmação.
- **Compatibilidade:** YouTube permanece na Captura clássica; Dublagem e Integração abrem os módulos atuais até a migração nativa dessas áreas.
- **Captura Real:** o desktop chama as rotas `api/runtime-capture/*`; o serviço Python abre o overlay sobre o jogo e continua responsável pelo monitor visual e OCR. O desktop não espelha o jogo. O encadeamento OCR→correspondência→take e a medição de impacto no FPS ainda precisam de validação end-to-end. A calibração também depende da chave e do modelo de IA configurados no serviço.

## Compilar no Windows

No diretório do repositório:

```powershell
dotnet restore desktop\DubSync.Desktop\DubSync.Desktop.csproj
dotnet publish desktop\DubSync.Desktop\DubSync.Desktop.csproj -c Release -o desktop\artifacts\release
```

O aplicativo conecta à API local na porta configurada nas preferências do desktop (padrão `8766`). O serviço ativo durante esta etapa é anterior às rotas de Captura Real e responde `404` para a descoberta de janelas. A tela nativa e o publish podem ser verificados sem interromper esse serviço; o teste real da Captura Real fica pendente até uma atualização controlada em momento seguro.
