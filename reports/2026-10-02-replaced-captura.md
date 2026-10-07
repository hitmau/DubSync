# Validação visual curta — REPLACED (2026-10-02)

Vídeo: `https://www.youtube.com/watch?v=qPueFah9uXg&list=PL2TUrSmxPbczXGqXrhqWnzh8RGQc9MSG6`. O DubSync abriu somente `v=qPueFah9uXg` e iniciou em 02:55 (175 s), onde há diálogos visíveis. Sessão final: `71abed1e-4b9b-485f-9e91-59cf9bd7f39b`, intervalo de 12 s, blocos de 6 s, OCR em português e ROI de legenda `x=.25, y=.73, width=.50, height=.10` no vídeo de 1280×720.

Resultado registrado no SQLite e em `.data/sessions/71abed1e-4b9b-485f-9e91-59cf9bd7f39b/`:

| Medida | Resultado |
|---|---:|
| Estado final | `complete`, sem erro |
| Segmentos | 2, com 183 e 179 frames |
| Frames capturados | 362, entre 175.053 s e 186.991 s |
| FPS efetivo | 30.10 |
| Frames analisados | 90 |
| Chamadas OCR / falhas | 26 / 0 |
| Eventos / imagens PNG | 2 / 20 |

O primeiro evento contém `R.E.A.C.H. Warren, foi registrada uma hora extra adicional.` (176.267–181.200 s). O segundo contém `WARREN Obrigado por me:lembrar pela sexta vez, R.E.A.C.H.` (181.600–185.858 s). A pontuação em `me:lembrar` ainda precisa de correção humana. As duas frases progressivas foram agrupadas como um evento por fala; cada evento preserva observações OCR, crop da ativação e final, frames completos em −1000/−500/−250 ms, início, melhor frame, fim e +250 ms.

`ffprobe` encontrou somente uma trilha de vídeo em cada MP4, sem trilha de áudio. Os arquivos `0000.jsonl` e `0001.jsonl` mantêm timestamps por frame e `capture_backend=video_canvas`. O vídeo foi capturado diretamente do elemento de vídeo do Chromium, sem áudio, controles do YouTube ou pixels de outras janelas.

Testes de código: `python -m unittest discover -s tests -p 'test_*.py' -v` (8 testes), `python -m compileall -q capture events database app`, `node --check review_ui/static/app.js` e `python tests/ui_smoke.py` (1920×1080) passaram. O teste real confirma o fluxo curto, mas não mede recall, precisão nem erro temporal contra uma anotação manual. O milestone de 10–15 minutos permanece pendente.

A interface local exibiu os dois eventos reais na tabela; ao selecionar um, carregou o crop de 509×63 px e dez miniaturas/assets, sem erro de JavaScript. O ajuste de recorte/ROI foi verificado também no smoke test de 1920×1080.

Também foi repetido o teste com início exatamente em **02:50**: sessão `91737fb8-83ab-4864-94a3-935895c64f2f`, 12 s, blocos de 6 s e a mesma ROI proporcional. O player entregou 854×480 nessa execução. A sessão terminou em `complete`, com 362 frames (170.040–182.000 s), 30.09 FPS efetivos, 90 frames analisados, 27 OCRs sem falha e dois eventos. A primeira fala foi catalogada completa; a segunda começou em 181.500 s e ficou corretamente marcada como truncada pelo fim do intervalo. O falso positivo de um caractere visto em tentativa anterior não reapareceu. `ffprobe` confirmou somente vídeo nos dois segmentos finais.
