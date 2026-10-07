# OmniVoice isolado

Este Compose executa OmniVoice em um serviço separado do servidor principal. O provider escuta somente em `127.0.0.1:8015`; o contêiner recebe apenas a pasta de dublagem e seu cache de pesos. O worker do DubSync mantém a fila serial no SQLite e envia ao provider caminhos relativos, não caminhos arbitrários do computador.

## Inicialização manual

Na pasta `Dupsync`, execute:

```powershell
docker compose -f .\docker\omnivoice\docker-compose.yml up -d --build
```

O primeiro carregamento baixa o pacote e os pesos para `.data/omnivoice-model-cache`. Consulte o healthcheck do Compose e o status da tela Dublagem antes de enviar jobs. Para parar:

```powershell
docker compose -f .\docker\omnivoice\docker-compose.yml stop omnivoice
```

A configuração padrão usa CPU. `DUBSYNC_OMP_THREADS` ajusta o número de threads e `OMNIVOICE_MODEL_REVISION` fixa uma revisão do modelo no ambiente Compose. A preferência de início automático fica desligada até ser habilitada em Configurações → Dublagem.

Os pesos publicados têm licença CC-BY-NC; confirme se o uso pretendido é compatível. Use amostras vocais próprias ou para as quais exista autorização documentada.
