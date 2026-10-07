# Módulo de Dublagem

Este pacote implementa a Fase 2 como uma área independente da captura/revisão da Fase 1. Ele mantém a trilha `evento OCR → fala → ocorrência → capture_link → take → decisão editorial` no SQLite existente. A inicialização é aditiva; não reescreve texto, estado, frames ou imagens da Fase 1.

## Componentes

- `service.py`: importação sem deduplicação, catálogo de personagens/perfis, roteiro, takes, fila persistente, revisão e exports.
- `api.py`: endpoints HTTP da área local. O módulo bloqueia chamadas que não chegam por loopback porque referências e áudio são material sensível.
- `audio.py`: caminhos relativos confinados ao diretório de dados, normalização FFmpeg e medição real de duração.
- `jobs.py`: único worker local e sequencial; jobs permanecem enfileirados enquanto o modelo está offline/carregando.
- `../providers/omnivoice.py`: adaptador HTTP versionado para o provider local.
- `../providers/docker.py`: ciclo de vida do Compose isolado e estado do provider.
- `../docker/omnivoice/`: imagem, serviço FastAPI e estado/cache do OmniVoice, fora do contêiner principal do DubSync.

## Dados e operação

- Banco: `Dupsync/.data/dubsync.sqlite3`; `PRAGMA user_version` e `schema_migrations` registram migrações aditivas. A base deste checkout vai até v7 com a fundação de integração da Fase 3.
- Áudios/referências: `Dupsync/.data/projects/replaced/dubbing/`; nunca colocar chaves ou arquivos privados no código-fonte.
- Cache de pesos: `Dupsync/.data/omnivoice-model-cache/`.
- Logs de fila: `Dupsync/.data/projects/replaced/dubbing/logs/`; não gravam texto completo nem referência vocal.
- Compose: `Dupsync/docker/omnivoice/docker-compose.yml`; publicar apenas em `127.0.0.1:8015`. `auto_start_docker` vem desligado por padrão. A primeira inicialização do provider baixa o modelo e pode levar vários minutos.
- Use apenas voz com autorização e confirme a licença dos pesos antes de qualquer geração/distribuição. O container principal não envia as referências para serviços externos.

## Regras editoriais

Cadastros importados da ROI começam sem verificação; perfil vocal exige nome confirmado e autorização explícita. Referência clone deve ser normalizada e aprovada quando fora da faixa recomendada. A fila automática recebe somente eventos aceitos/corrigidos e perfis prontos; pendências são sinalizadas para decisão humana. O override é individual e explícito. Takes sempre aguardam revisão, guardam duração/alvo/delta e têm histórico de aprovação/rejeição.

O endpoint existente `GET /api/dubbing/runtime-manifest` foi evoluído para o schema 3 e continua sendo o contrato único do manifesto. A Fase 3 agora inclui perfis de jogo, catálogo e vínculo de plugins, associação de triggers reais, geração de pacote com áudio derivado e registros de teste em `../integration/`.

O adaptador REPLACED/Articy em `../integration/adapters/replaced_articy.py` normaliza callbacks para o contrato de eventos e deduplica `InitLine` repetido enquanto a fala permanece ativa. Ele ainda não instala hooks no jogo. O executável local não tem BepInEx, MelonLoader ou Unity Doorstop; a confirmação do loader e a execução real do adaptador continuam pendentes.

O pacote consome somente takes aprovados com trigger e master disponíveis. A conversão OGG/WAV ocorre em diretório/cache próprios e preserva o MASTER WAV. O gate final da Fase 3 exige pelo menos 10 dublagens reais, com gatilho correto, sem take incorreto ou duplicação; os dados atuais não satisfazem esse gate.

Referências da integração: `../docs/DubSync_Documento_Mestre_Contexto_Handoff.md`, `../docs/phase3/DubSync_Fase3_Integracao_Plugins_Especificacao_v3.docx` e `../docs/phase3/assets/Painel DubSync Neon para Dublagem de Jogos.png`.
