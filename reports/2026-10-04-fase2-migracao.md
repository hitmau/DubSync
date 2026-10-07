# DubSync — plano de migração aditiva da Fase 2

**Data:** 04/10/2026  
**Projeto:** `Dupsync`  
**Base examinada:** `.data/dubsync.sqlite3` em modo somente leitura  
**Estado anterior:** `PRAGMA user_version = 0`; a Fase 1 já tinha seis tabelas persistentes.

## Inventário antes da alteração

- 3 sessões de arquivo e 80 eventos; duas sessões concluídas cobrem o mesmo intervalo de 730 segundos a partir de 170 segundos.
- Na sessão concluída mais recente: 15 eventos `accepted`/`corrected`, 38 `pending` e 14 `discarded`.
- 14 perfis legados de ROI; 55 eventos têm vínculo com esses perfis. Nenhum perfil possui referência vocal cadastrada.
- O histórico de revisão contém 95 registros. As tabelas, eventos, imagens e correções da Fase 1 serão preservados.
- Nenhuma sessão de captura estava em andamento quando o serviço foi parado para o backup.

## Backup verificado

O backup integral foi criado antes de qualquer escrita em:

`.data/backups/fase2-pre-migracao-20261004-190408/`

Contém uma cópia SQLite consistente e os 827 arquivos de imagem/frame atualmente referenciados pelos eventos (2.024.129.769 bytes), preservados nos mesmos caminhos relativos por sessão. `backup-manifest.json` registra a versão anterior, tabelas, caminhos, tamanhos e hashes SHA-256. O hash SHA-256 da cópia SQLite é `68d9b0e7c4e1ec25bda1e79c3b87b9a23e04025230a30ac470c5bb2a33208422`.

## Alterações planejadas

Registrar explicitamente a Fase 1 como versão 1 e aplicar a versão 2 por meio de `schema_migrations` e `PRAGMA user_version`. A mudança só adiciona tabelas e índices; nenhuma coluna, linha ou asset da Fase 1 será removido, renomeado ou reescrito.

As novas tabelas são:

1. `projects` — jogo, build e idiomas;
2. `characters` — identidade estável e aliases, com origem/validação dos cadastros legados;
3. `voice_profiles` — perfil por personagem e idioma;
4. `voice_reference_assets` — referências, metadados e hash;
5. `dialogue_lines` — fala canônica, texto fonte/revisado, idioma e estado editorial;
6. `dialogue_occurrences` — cena/rota/condição, sequência local, janela alvo e gatilho futuro;
7. `capture_links` — relação rastreável com sessão/evento e tempos do vídeo;
8. `audio_takes` — versões de áudio, duração, provider, estado e aprovação;
9. `tts_jobs` — fila persistente com tentativas e erros;
10. `scenes` — nós narrativos;
11. `scene_edges` — transições e condições entre nós;
12. `routes` — percurso e escolhas;
13. `capture_traversals` — percurso observado ligado à sessão e build;
14. `interpretation_presets` — instruções de interpretação reutilizáveis.
15. `audio_take_review_history` — decisões e substituições de aprovação do take.

Criar índices para as relações/estados usados nas listas, filtros, takes e fila. A migração materializará cada evento existente em uma linha e ocorrência próprias, mantendo o estado editorial, o texto OCR e corrigido e um `capture_link` para o `event_id` original. A inicialização não deduplicará por texto, não aprovará pendências e não criará takes fictícios. Os 14 cadastros legados serão espelhados como não verificados; correções editoriais da Fase 1 não serão alteradas.

O projeto inicial é `replaced`, com idioma de origem `pt` observado nas sessões e idioma de dublagem `pt-BR` definido pela especificação. O texto revisado será pré-preenchido para edição, mas a geração permanecerá bloqueada até haver perfil de voz e referência válidos. A API OmniVoice ficará restrita a `127.0.0.1:8015`; o runtime da Fase 3 ficará fora desta alteração.
