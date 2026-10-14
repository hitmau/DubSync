# DubSync Game Scanner
## Especificação do App de Diagnósticos em 8 Camadas

**Projeto relacionado:** DubSync  
**Objetivo:** diagnosticar tecnicamente um jogo antes da integração de dublagem  
**Status deste documento:** especificação dedicada do scanner/diagnóstico  
**Escopo inicial:** leitura e diagnóstico; não alterar arquivos do jogo automaticamente

---

## 1. Visão geral

O **DubSync Game Scanner** é uma ferramenta autônoma de diagnóstico técnico para jogos.

A função dele é receber uma **pasta de instalação** ou um **executável do jogo**, inspecionar os arquivos e produzir um relatório estruturado que ajude o DubSync a responder:

- qual engine o jogo usa;
- como a build foi produzida;
- onde podem estar os sistemas de diálogo e legenda;
- qual tecnologia de áudio está presente;
- quais eventos internos podem servir de gatilho de fala;
- que caminhos de modding/injeção parecem viáveis;
- quando uma integração nativa não for possível, qual fallback visual pode ser usado.

A ferramenta deve **explicar por que chegou a cada conclusão**, preservando evidências encontradas durante a varredura.

### Regra principal

O scanner **não deve decidir sozinho que uma integração está confirmada apenas porque encontrou nomes ou arquivos compatíveis**.

Ele deve distinguir claramente:

- **detectado**: há evidência concreta em arquivos;
- **provável**: há fortes indícios, mas falta validação;
- **não confirmado**: requer teste em runtime;
- **não encontrado**: nenhuma evidência foi localizada;
- **não aplicável**: a camada não faz sentido para aquele jogo.

---

## 2. Relação com o DubSync

O Game Scanner não substitui a Fase 3.

Ele serve como ferramenta de reconhecimento técnico para alimentar a arquitetura da **Fase 3 — Integração**.

Fluxo esperado:

```text
Jogo / pasta / executável
        │
        ▼
DubSync Game Scanner
        │
        ├── diagnóstico em 8 camadas
        ├── evidências
        ├── confiança
        └── recomendações
        │
        ▼
Fase 3 — Integração
        │
        ├── escolher adapter
        ├── criar/usar plugin específico
        ├── usar plugin genérico
        └── usar Visual Runtime como fallback
```

A arquitetura da Fase 3 continua orientada por **jogo**, com um plugin/adaptador ativo por `game_profile`.

O scanner deve ajudar a decidir qual caminho faz sentido antes de implementar o adapter.

---

# 3. As 8 camadas de diagnóstico

## Camada 1 — Engine

### Objetivo

Descobrir qual engine foi usada pelo jogo.

### Exemplos de resultado

- Unity
- Unreal Engine
- Godot
- engine proprietária
- desconhecida

### Evidências possíveis

- arquivos e DLLs características;
- nomes de diretórios;
- metadata;
- executáveis auxiliares;
- arquivos de configuração;
- estruturas conhecidas da engine.

### Saída esperada

```json
{
  "layer": "engine",
  "status": "detected",
  "value": "Unity",
  "confidence": "high",
  "evidence": [
    "GameAssembly.dll",
    "*_Data/"
  ]
}
```

---

## Camada 2 — Build / Runtime

### Objetivo

Descobrir **como o jogo foi compilado/executado**.

Essa camada é especialmente importante em Unity.

### Exemplos

- Unity Mono
- Unity IL2CPP
- Unreal nativo
- .NET
- runtime proprietário

### Por que importa

Dois jogos Unity podem exigir estratégias completamente diferentes.

Por exemplo:

```text
Unity
├── Mono
└── IL2CPP
```

O tipo de runtime influencia:

- carregador/mod framework;
- possibilidade de reflection;
- forma de acessar classes;
- hooks;
- inspeção de metadata;
- bridge a ser usado.

### Evidências possíveis

- `GameAssembly.dll`;
- `global-metadata.dat`;
- assemblies managed;
- runtime libraries;
- estrutura de diretórios.

---

## Camada 3 — Sistema de diálogo / narrativa

### Objetivo

Descobrir se o jogo usa um sistema identificável para controlar falas, cenas, escolhas e progressão narrativa.

### Exemplos de tecnologias ou sinais

- Articy
- Ink
- Yarn
- sistema próprio
- classes de diálogo internas
- objetos de conversa
- IDs persistentes de fala

### O que procurar

- classes relacionadas a diálogo;
- `DialogueFragment`;
- speaker;
- text;
- IDs;
- scene;
- nodes;
- choices;
- flags;
- flow players;
- narrative state.

### Resultado desejado

Identificar se existe uma **chave estável de fala**.

Exemplo:

```text
ExternalId
ShortId
DialogueId
SubtitleId
NodeId
```

Se existir uma chave interna estável, ela tem prioridade sobre reconhecimento visual.

---

## Camada 4 — Legendas / localização

### Objetivo

Descobrir como o jogo exibe, organiza e localiza texto.

### O scanner deve procurar

- arquivos de localização;
- tabelas de strings;
- subtitle managers;
- classes de legenda;
- recursos por idioma;
- IDs de legenda;
- eventos de início/fim;
- idioma ativo.

### Questões que esta camada deve responder

- O texto aparece associado a algum ID?
- Existe classe que inicia e encerra uma legenda?
- O speaker é conhecido?
- Há texto original/localizado acessível?
- Existe duração da linha?
- Existe evento de troca de legenda?

Essa camada pode ser crucial para a sincronização da dublagem.

---

## Camada 5 — Áudio

### Objetivo

Descobrir como o jogo gerencia áudio.

### Tecnologias esperadas

- FMOD
- Wwise
- áudio nativo da engine
- sistema próprio

### O scanner deve registrar

- middleware detectado;
- bibliotecas;
- bancos;
- quantidade aproximada quando for possível determinar de forma barata;
- caminhos relevantes;
- indícios de buses ou eventos;
- formatos encontrados.

### Importante

Detectar FMOD ou Wwise **não significa automaticamente que o DubSync deve modificar bancos de áudio**.

O resultado serve para orientar a estratégia.

Possíveis estratégias futuras:

```text
áudio externo
FMOD/Wwise
substituição de mídia
```

A primeira prova de conceito deve preferir o caminho com menor quantidade de variáveis.

---

## Camada 6 — Eventos / Runtime

### Objetivo

Encontrar pontos internos que possam sinalizar eventos em tempo de execução.

### Eventos de interesse

```text
dialogue_start
dialogue_end
pause
resume
scene_changed
choice_changed
speaker_changed
```

### Exemplos de sinais procurados

- métodos `InitLine`;
- métodos `ReleaseLine`;
- mudança de subtitle;
- callbacks;
- listeners;
- state machines;
- eventos de UI;
- controllers de diálogo.

### Resultado esperado

O relatório deve apontar:

1. evento candidato;
2. classe/módulo em que foi encontrado;
3. evidência;
4. se é apenas análise estática;
5. o que precisa ser confirmado em runtime.

Essa camada é a ponte entre análise de arquivos e o futuro **Game Bridge / Mod**.

---

## Camada 7 — Modding / Injeção

### Objetivo

Avaliar de que maneira o DubSync poderia observar o jogo ou instalar um bridge.

### Caminhos possíveis

- mod loader;
- plugin nativo;
- BepInEx;
- MelonLoader;
- loader específico;
- injeção compatível com a engine;
- API oficial;
- logs externos;
- bridge separado.

> A ferramenta não deve assumir automaticamente que um loader específico funciona apenas porque a engine foi detectada.

### Saída esperada

```text
Método potencial
Compatibilidade conhecida
Evidência
Risco
Necessidade de teste
```

### Regra

No primeiro milestone o Game Scanner deve **diagnosticar**, não instalar silenciosamente loaders nem alterar arquivos do jogo.

---

## Camada 8 — Fallback Visual

### Objetivo

Avaliar se o jogo pode ser integrado usando o fallback universal do DubSync quando não existir um gatilho interno confiável.

### Recursos

- captura de frames;
- regiões de interesse;
- OCR;
- texto;
- personagem;
- posição;
- assinatura visual;
- contexto de cena;
- pixels.

### Relação com o DubSync

Esse caminho corresponde ao **Visual Runtime**.

A hierarquia desejada é:

```text
1. ID/evento interno confiável
2. integração por engine/mod/plugin
3. middleware/evento externo confiável
4. reconhecimento híbrido
5. Visual Runtime
```

O fallback visual não deve ser tratado como primeira opção quando o jogo fornece um ID interno estável.

---

# 4. Extensões futuras

Essas análises foram discutidas como possíveis extensões, mas **não fazem parte das oito camadas principais**.

## Anti-cheat / DRM

Objetivo futuro:

- detectar presença de anti-cheat;
- sinalizar risco de injeção;
- evitar sugerir integração invasiva em jogos protegidos.

## Assets

Objetivo futuro:

- inventariar bundles;
- catálogos;
- assets de localização;
- bancos;
- pacotes úteis à análise.

Essas extensões devem ser tratadas como módulos adicionais, não como substituição das oito camadas.

---

# 5. Arquitetura interna proposta

A arquitetura deve ser modular.

```text
game_scanner/
├── core/
│   ├── scanner.py
│   ├── context.py
│   ├── evidence.py
│   └── report.py
│
├── detectors/
│   ├── engine.py
│   ├── build_runtime.py
│   ├── dialogue.py
│   ├── localization.py
│   ├── audio.py
│   ├── runtime_events.py
│   ├── modding.py
│   └── visual_fallback.py
│
├── signatures/
│   ├── unity/
│   ├── unreal/
│   ├── middleware/
│   └── narrative/
│
├── analyzers/
│   └── ...
│
└── ui/
    └── ...
```

A nomenclatura final pode ser adaptada ao projeto real.

O importante é preservar a separação:

```text
scan
↓
detecção
↓
análise
↓
evidência
↓
confiança
↓
recomendação
↓
relatório
```

---

# 6. Modelo de evidência

Cada conclusão deve possuir evidências rastreáveis.

### Exemplo

```json
{
  "finding": "Unity IL2CPP",
  "status": "detected",
  "confidence": "high",
  "evidence": [
    {
      "type": "file",
      "path": "GameAssembly.dll"
    },
    {
      "type": "file",
      "path": "Game_Data/il2cpp_data/Metadata/global-metadata.dat"
    }
  ]
}
```

### Regra

Não retornar apenas:

```text
Unity IL2CPP
```

Retornar:

```text
Unity IL2CPP
Confiança: alta
Por quê:
- GameAssembly.dll encontrado
- global-metadata.dat encontrado
```

A explicabilidade é parte central do scanner.

---

# 7. Confiança

O projeto precisa representar confiança, porém a escala numérica exata ainda não foi congelada.

### Modelo inicial recomendado

```text
HIGH
MEDIUM
LOW
UNKNOWN
```

Ou, na UI:

```text
Alta
Média
Baixa
Não determinada
```

Esse modelo pode ser refinado depois.

### Regra de ouro

Confiança de análise estática **não equivale a funcionamento em runtime**.

Exemplo:

```text
Articy detectado       → confiança alta
ExternalId encontrado  → confiança alta
ExternalId acessível durante gameplay → NÃO CONFIRMADO até teste real
```

---

# 8. Relatório

O scanner deve gerar pelo menos dois formatos.

## 8.1. Relatório estruturado

Formato sugerido:

```text
JSON
```

Uso:

- consumo pelo DubSync;
- comparação entre scans;
- integração com adapters;
- automação.

Exemplo conceitual:

```json
{
  "game": {
    "name": "REPLACED",
    "path": "..."
  },
  "diagnostics": {
    "engine": {},
    "build_runtime": {},
    "dialogue": {},
    "localization": {},
    "audio": {},
    "runtime_events": {},
    "modding": {},
    "visual_fallback": {}
  },
  "recommendation": {
    "integration_type": "game_specific_adapter",
    "adapter_candidate": "replaced_articy"
  }
}
```

## 8.2. Relatório legível

A interface e/ou exportação deve produzir uma versão resumida para humanos.

Exemplo:

```text
REPLACED

Engine
✓ Unity
Confiança: alta

Build
✓ IL2CPP
Confiança: alta

Diálogo
✓ Articy detectado
✓ DialogueFragment encontrado

Eventos
! InitLine e ReleaseLine encontrados
Runtime ainda precisa ser validado

Áudio
✓ FMOD detectado

Integração recomendada
REPLACED Articy Runtime

Fallback
Visual Runtime disponível
```

---

# 9. Interface

A interface inicial pode ser simples.

## Entrada

```text
[ Selecionar pasta do jogo ]
[ Selecionar executável ]
[ Iniciar diagnóstico ]
```

## Progresso

```text
Camada 1/8  Engine
Camada 2/8  Build
Camada 3/8  Diálogo
...
```

## Resultado

Mostrar oito cards ou linhas de diagnóstico:

```text
1  Engine
2  Build / Runtime
3  Diálogo / Narrativa
4  Legendas / Localização
5  Áudio
6  Eventos Runtime
7  Modding / Injeção
8  Fallback Visual
```

Cada item deve exibir:

- estado;
- confiança;
- resumo;
- evidências;
- recomendação;
- detalhes.

---

# 10. Integração com a Fase 3

O scanner deve se conectar conceitualmente aos componentes já previstos da Fase 3.

Pontos de integração:

```text
integration/diagnostics.py
integration/registry.py
integration/plugins/loader.py
integration/plugins/validator.py
IntegrationAdapter
```

Fluxo:

```text
Game Scanner
     │
     ▼
Diagnostic Report
     │
     ▼
Plugin Registry
     │
     ├── adapter específico encontrado
     ├── adapter genérico compatível
     └── Visual Runtime
```

### Exemplo

```text
Scanner detecta:

Unity
IL2CPP
Articy
DialogueFragment
SubtitlesManager
FMOD
```

Resultado:

```text
Recomendação:
REPLACED Articy Runtime

Prioridade:
Game-specific Integration Adapter
```

---

# 11. Exemplo conhecido — REPLACED

Os achados já levantados para REPLACED servem como primeiro caso real de validação do scanner.

## Engine

```text
Unity
```

## Build

```text
IL2CPP
```

## Diálogo

Foram encontrados indícios de:

```text
Articy
DialogueFragment
ExternalId
ShortId
speaker
text
```

## Legenda

Foram identificados nomes relacionados a:

```text
SubtitlesManager
InitLine
ReleaseLine
LastLine
CurrentLineDuration
```

## Áudio

```text
FMOD
```

## Eventos

Candidatos:

```text
InitLine
ReleaseLine
```

## Integração sugerida

```text
REPLACED Articy Runtime
```

## Importante

Esses achados nasceram de análise estática.

O scanner deve reportar algo equivalente a:

```text
ExternalId encontrado: SIM
Uso em runtime: A VALIDAR
```

Não deve afirmar automaticamente que o hook funciona.

---

# 12. Exemplo preliminar — Hollow Knight

O Hollow Knight foi citado durante a discussão como outro candidato útil para validar o scanner.

O único dado já levantado nessa conversa foi:

```text
Unity 2020.2
```

O restante das oito camadas deve ser descoberto pelo scanner.

Não preencher antecipadamente diálogo, áudio, runtime ou modding sem evidência real.

Isso transforma Hollow Knight em um bom caso de teste:

```text
REPLACED
→ perfil conhecido

Hollow Knight
→ perfil parcialmente desconhecido
```

Assim podemos verificar se o scanner realmente descobre tecnologia em vez de depender de regras feitas especificamente para REPLACED.

---

# 13. Recomendações de saída

O scanner deve terminar com uma recomendação de caminho de integração.

Exemplos:

```text
GAME_SPECIFIC_ADAPTER
GENERIC_UNITY_ADAPTER
GENERIC_UNREAL_ADAPTER
MIDDLEWARE_ADAPTER
EXTERNAL_SIGNAL
HYBRID_RUNTIME
VISUAL_RUNTIME
UNKNOWN
```

Esses nomes são uma proposta de representação e podem ser ajustados à nomenclatura real do projeto.

A recomendação sempre deve vir acompanhada das evidências.

---

# 14. Não objetivos do primeiro milestone

O primeiro milestone não deve:

- modificar arquivos do jogo automaticamente;
- instalar loaders sem confirmação;
- criar hooks automaticamente;
- gerar dublagem;
- alterar áudio do jogo;
- substituir a Fase 3;
- garantir compatibilidade em runtime somente por análise estática;
- executar engenharia específica para um único jogo dentro do núcleo genérico.

O scanner deve primeiro **entender e explicar**.

---

# 15. Requisitos de qualidade

## Modularidade

Cada detector deve poder evoluir sem reescrever o scanner inteiro.

## Evidência

Toda conclusão importante precisa apontar o motivo.

## Generalização

REPLACED é o primeiro caso, não o único.

## Segurança

O primeiro milestone é passivo.

## Reprodutibilidade

O mesmo jogo/build deve produzir diagnóstico comparável.

## Integração

O resultado precisa poder ser consumido pelo DubSync/Fase 3.

---

# 16. Fluxo funcional completo

```text
1. Abrir DubSync Game Scanner
2. Selecionar pasta ou executável
3. Criar contexto de scan
4. Executar Camada 1 — Engine
5. Executar Camada 2 — Build / Runtime
6. Executar Camada 3 — Diálogo / Narrativa
7. Executar Camada 4 — Legendas / Localização
8. Executar Camada 5 — Áudio
9. Executar Camada 6 — Eventos Runtime
10. Executar Camada 7 — Modding / Injeção
11. Executar Camada 8 — Fallback Visual
12. Consolidar evidências
13. Calcular confiança
14. Gerar recomendações
15. Exibir relatório
16. Exportar JSON
17. Disponibilizar resultado para a Fase 3
```

---

# 17. Critérios mínimos de aceite

O primeiro Game Scanner será considerado funcional quando:

- aceitar pasta ou executável;
- executar as oito camadas;
- gerar diagnóstico independente por camada;
- registrar evidências;
- distinguir detectado de não confirmado;
- gerar relatório JSON;
- gerar relatório legível;
- identificar corretamente os sinais já conhecidos do REPLACED;
- não inventar conclusões quando não houver evidência;
- conseguir analisar um segundo jogo sem regras hardcoded de REPLACED;
- sugerir um caminho de integração;
- não alterar o jogo durante o diagnóstico.

---

# 18. Relação com o Visual Runtime

O Visual Runtime deve permanecer como fallback universal.

O scanner não deve enxergar fallback visual como falha.

Ele é uma estratégia válida quando:

- não existe ID interno acessível;
- o jogo não aceita modding;
- não há evento confiável;
- o risco de integração interna é alto.

Diagnóstico possível:

```text
Integração interna: não confirmada
Eventos estáveis: não encontrados
Modding: risco alto

Recomendação:
VISUAL_RUNTIME
```

---

# 19. Filosofia do Game Scanner

A ferramenta não deve responder apenas:

> "Qual engine esse jogo usa?"

Ela deve responder:

> "Como esse jogo provavelmente pode conversar com o DubSync, e quais evidências sustentam essa conclusão?"

Essa é a função das oito camadas.

---

# 20. Resumo para implementação

**Nome de trabalho:** DubSync Game Scanner

**Entrada**
- pasta do jogo;
- executável.

**Saída**
- oito diagnósticos;
- evidências;
- confiança;
- recomendação;
- JSON;
- relatório legível.

**Oito camadas**
1. Engine
2. Build / Runtime
3. Diálogo / Narrativa
4. Legendas / Localização
5. Áudio
6. Eventos / Runtime
7. Modding / Injeção
8. Fallback Visual

**Integração**
- `diagnostics.py`
- registry
- loader
- validator
- adapters
- Fase 3

**Primeiro caso**
- REPLACED

**Segundo caso útil de validação**
- Hollow Knight

**Regra central**
- diagnosticar primeiro;
- provar runtime depois;
- não alterar o jogo durante o scan inicial.

