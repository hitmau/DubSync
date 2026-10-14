# DubSync Game Scanner

Aplicação local para reconhecer evidências técnicas de um jogo antes de escolher a integração da Fase 3.

## Iniciar no Windows

Na pasta `Dupsync`, execute:

```powershell
.\game_scanner\run.ps1
```

Também é possível iniciar com `python -m game_scanner` a partir da pasta `Dupsync`. A interface usa apenas Python, Tkinter e `ttk`; não adiciona dependências ao `requirements.txt` do DubSync.

## Primeiro marco implementado

- Seleção de pasta ou executável por diálogo do Windows.
- Diagnóstico em segundo plano das oito camadas, com progresso.
- Inventário de arquivos sem seguir links simbólicos.
- Leitura de `FileVersion` e `ProductVersion` PE sem iniciar o executável.
- Busca limitada de nomes de assinatura em assemblies e metadados; conteúdo do jogo não é executado.
- Evidência por arquivo, estado e confiança em cada camada.
- Separação explícita entre detecção estática e validação pendente em runtime.
- Recomendação de integração com as evidências que a sustentam.
- Leitura somente dos manifestos declarativos dos plugins existentes para sugerir candidato por jogo; o código do plugin não é carregado.
- Histórico local em `Dupsync/.data/game_scanner/` e exportação JSON versionada.
- Cards, painel de evidências e janela de detalhes no padrão visual neon do mockup.

O relatório não altera arquivos do jogo, não instala loaders, não cria hooks e não confirma compatibilidade apenas por encontrar nomes.

## Abrir e observar jogo

Após selecionar o executável, use **Abrir e observar jogo**. O scanner se conecta ao processo se ele já estiver aberto; caso contrário, inicia o executável na sua pasta. A observação registra PID, janela e módulos carregados quando o Windows permitir. Com a janela do jogo em primeiro plano, captura a janela visível a cada cinco segundos e registra textos candidatos por OCR. **Registrar fala** espera três segundos para você voltar ao jogo e então salva um frame e o OCR correspondente. Um clique duplo na evidência visual abre o frame. **Parar observação** encerra apenas o scanner, não o jogo.

Cada sessão é salva em `.data/game_scanner/runtime/OBS_*/observation.json`, com os frames ao lado. O relatório runtime é separado do scan estático. Texto visto por OCR confirma apenas pixels visíveis; não prova callback, ID de fala, modding ou compatibilidade do adapter. Quando o jogo estiver coberto por outra janela, a captura visual é suspensa para evitar evidência incorreta.

## Arquitetura

```text
game_scanner/
├── core/          modelos, inventário passivo e orquestração
├── detectors/     oito detectores independentes
├── reports/       serialização e exportação
├── ui/            interface Tkinter/ttk
└── docs/          especificação e referência visual recebidas
```

`core` e `detectors` não dependem de Tkinter. O relatório usa `schema_version` e nomes de camadas estáveis para que a API/registry da Fase 3 possa consumi-lo depois sem importar a interface.

## Limites da análise atual

O scan é estático e tem limites intencionais de quantidade/tamanho para não varrer mídia de jogo sem fim. Uma assinatura ausente não prova que uma tecnologia não exista. Classes, IDs e callbacks encontrados em assemblies/metadata ainda precisam ser observados no jogo em execução antes de recomendar um adapter como funcional.

`FileVersion` e `ProductVersion` são os recursos declarados no executável; podem ser a versão do Unity ou da toolchain e não necessariamente a versão comercial do jogo. O scanner mantém os rótulos explícitos e não os converte em uma versão de release.

O perfil REPLACED do documento de especificação é uma referência conhecida, não um resultado preenchido artificialmente. O scanner só apresentará achados depois de ler os arquivos da instalação selecionada.

Para REPLACED, a tela também consegue distinguir a referência do projeto do scan novo: o manifesto atual registra callbacks observados em piloto anterior, mas mantém `runtime_hook_confirmed` como falso. Isso aparece como contexto do candidato, nunca como prova de que a instalação selecionada foi verificada.
