# Crypto Copilot

Copiloto pessoal de trading cripto para **Binance Futures**, com IA analítica
(OpenRouter ou Groq), alertas no Telegram, paper trade e dashboard web.

> ⚠️ **Não é robô de execução automática.** É um *assistente* que analisa o
> mercado, identifica setups e te explica o porquê. Quem aperta o botão é você.

---

## Filosofia

Esse projeto **não promete lucro**, **não adivinha preço** e **não opera sozinho**.

Ele faz o que humano faz mal: olha múltiplos pares, em múltiplos timeframes,
todo minuto, com critérios consistentes. E ele faz o que IA faz bem: interpreta
o contexto e te dá um veredicto curto antes de você abrir o gráfico.

---

## Funcionalidades

- 📈 Coleta OHLCV multi-timeframe (4h / 1h / 15m / 5m) via CCXT
- 🧮 Indicadores: RSI, MACD, EMA 9/21/200, VWAP, ATR, Bollinger, volume MA
- 🏗️ Estrutura: pivôs S/R, detecção de breakout, volume profile básico
- 📊 Contexto extra: funding rate, open interest, long/short ratio
- 🎯 4 estratégias prontas:
  - Impulse MACD (continuação de tendência)
  - RSI + VWAP + Volume (scalp pullback)
  - Breakout com volume
  - Reversão em S/R com RSI extremo + Bollinger
- 🧠 IA dual: **OpenRouter** (Claude/GPT/Gemini) ou **Groq** (Llama 3.3) — switch via `.env`
- 🔢 Score 0-100 com componentes ponderados (tendência, confluência, volume, vol., estrutura, R:R)
- 🛡️ Risco: stop por ATR, take profit por R:R, sizing por % de risco
- 📲 Alertas Telegram com markdown bonito + comentário da IA
- 🎮 Paper trade com PnL, winrate, fees
- ⏪ Backtester bar-by-bar
- 🖥️ Dashboard FastAPI (HTML puro + JS, sem build) na porta 8000

---

## Estrutura

```
crypto-copilot/
├── core/              # settings, logger, types, scoring, risk, engine, MTF
├── indicators/        # technical + structure (S/R, breakout, vol profile)
├── strategies/        # impulse_macd, rsi_vwap_volume, breakout_volume, sr_reversal
├── ai/                # base, openrouter_client, groq_client, prompts, analyst
├── alerts/            # telegram, formatter
├── exchange/          # binance_client (CCXT), stream (WebSocket)
├── paper_trade/       # simulator
├── backtests/         # engine
├── dashboard/         # FastAPI app + static + templates
├── config/            # pairs.yaml
├── data/              # paper-trade journal, parquets
├── logs/              # rotating loguru sinks
├── main.py            # entrypoint
├── requirements.txt
└── .env.example
```

---

## Instalação

Requer **Python 3.12+** e Windows / Linux / macOS.

```bash
# 1) clonar / entrar
cd crypto-copilot

# 2) virtualenv
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

# 3) dependências
pip install -r requirements.txt

# 4) configurar
copy .env.example .env       # Windows
cp .env.example .env         # Linux/macOS
# edite .env com suas chaves
```

### Chaves necessárias

| Onde            | Para que                          | Obrigatória? |
|-----------------|-----------------------------------|--------------|
| Binance Futures | OHLCV, funding, OI                | **Sim** (read-only basta) |
| Telegram Bot    | Receber alertas                   | Recomendado |
| OpenRouter      | IA (default)                      | Sim, se `AI_PROVIDER=openrouter` |
| Groq            | IA (alternativa rápida e barata)  | Sim, se `AI_PROVIDER=groq`       |

Bot Telegram: fale com [@BotFather](https://t.me/BotFather) → `/newbot` → copie o
token. Para `TELEGRAM_CHAT_ID`, mande qualquer mensagem para o bot e abra
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

---

## Como usar

```bash
# Modo padrão: engine + dashboard
python main.py

# Engine sem dashboard
python main.py run --no-dashboard

# Só uma varredura, sai depois
python main.py scan-once

# Resumo IA do mercado → Telegram
python main.py brief

# Backtest
python main.py backtest BTCUSDT 15m --limit 1500
```

Dashboard: <http://127.0.0.1:8000>

---

## Configuração rápida

Tudo via `.env`. Pontos mais importantes:

```env
AI_PROVIDER=openrouter          # ou groq
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,PEPEUSDT,BNBUSDT
TIMEFRAMES=4h,1h,15m,5m
SCAN_INTERVAL_SECONDS=60        # ciclo do scanner
MIN_SCORE_TO_ALERT=70           # 0-100, abaixo disso não alerta
ATR_STOP_MULT=1.5               # stop = entry ± ATR * mult
DEFAULT_RR_RATIO=2.0            # take = entry ± risco * RR
DEFAULT_RISK_PERCENT=1.0        # % do saldo arriscado por trade
```

Tuning por par fica em `config/pairs.yaml`.

---

## Arquitetura — fluxo de dados

```
┌─────────────┐  every 60s   ┌────────────────┐
│  Engine     │─────────────▶│ BinanceClient  │ async, retry, ratelimit
└─────┬───────┘              └────────────────┘
      │
      │  multi-TF OHLCV + funding + OI + L/S
      ▼
┌─────────────┐
│ Indicators  │ → IndicatorSnapshot por TF
└─────┬───────┘
      │
      ▼
┌─────────────┐  per symbol
│  MTF / ctx  │ → MarketContext (regime + macro_trend)
└─────┬───────┘
      │
      ▼
┌─────────────┐  fan-out
│ Strategies  │ → StrategyResult? (4 implementadas)
└─────┬───────┘
      │
      ▼
┌─────────────┐
│ Risk + Score│ → Signal (entry/stop/take/RR/score)
└─────┬───────┘
      │  if score ≥ threshold and not duplicate
      ▼
┌─────────────┐
│  AI Analyst │ → comentário curto pt-BR
└─────┬───────┘
      │
      ▼
┌─────────────┐         ┌──────────────┐
│  Telegram   │◀────────│ PaperTrader  │ updates marks each cycle
└─────────────┘         └──────────────┘

Dashboard FastAPI lê o estado vivo da Engine via /api/* (sem IPC).
```

### Decisões de design

- **Provedor de IA é abstrato** (`ai/base.py`): OpenRouter e Groq são variantes
  de um cliente OpenAI-compatível. Trocar é uma linha de `.env`.
- **Score é heurístico, transparente** (`core/scoring.py`): pesos ficam visíveis,
  não é black box. Você ajusta sem retreinar nada.
- **Stops são ATR-based**, não percentuais fixos: adaptam-se à volatilidade do par
  (PEPE precisa de stop maior que BTC).
- **Sem ordens reais por design**. Não há código de execução privada — keys de
  leitura bastam. Quando quiser automatizar, adicione um `execution/` module.
- **Estado vive no processo** (não DB): suficiente para o copiloto V1 de uso
  pessoal. Persistência fica em `logs/signals.jsonl` e `data/paper_trades.jsonl`
  para auditoria.

---

## Troubleshooting

### `Task engine crashed: binanceusdm GET .../exchangeInfo`

A Binance bloqueou seu IP no endpoint de Futures. Acontece em vários
países (Brasil entrou em 2024) — a API responde HTTP 451 ou
`"Service unavailable from a restricted location"`.

Soluções, em ordem de preferência:

1. **Use a Testnet** — coloca no `.env` e roda de novo:
   ```env
   BINANCE_TESTNET=true
   ```
   A testnet (`testnet.binancefuture.com`) costuma ficar acessível
   globalmente e os dados de mercado são realistas o suficiente pra
   validar o engine + estratégias.

2. **VPN / proxy** apontando pra região não-bloqueada (Reino Unido,
   Japão, etc.). Setar `HTTPS_PROXY=http://user:pass@host:port` no
   ambiente já é suficiente — o `httpx`/`aiohttp` que o CCXT usa
   respeitam essa variável.

3. **Trocar de exchange.** Bybit está no roadmap V3 e tem dados de
   futuros sem bloqueio geográfico tão agressivo. O design do projeto
   já abstrai isso: basta criar `exchange/bybit_client.py` espelhando
   a interface de `BinanceClient`.

A partir desta versão o engine **não derruba mais o dashboard** quando
a Binance falha no boot — fica tentando reconectar a cada `SCAN_INTERVAL_SECONDS`
e o dashboard segue acessível em `http://127.0.0.1:8000` mostrando
"aguardando dados".

### Diagnosticar erro completo do exchange

Rode `python main.py scan-once` — sai depois de uma rodada e imprime
o stack trace completo (sem truncar como o `loguru` faz no log padrão).
O log persistente também ajuda:

```bash
# Windows
type logs\copilot.log | more

# Linux/macOS
tail -f logs/copilot.log
```

### `OPENROUTER_API_KEY` ou `GROQ_API_KEY` vazia

A IA degrada gracefully — o engine continua emitindo sinais, só sem
o comentário em pt-BR. Pra ativar, preencha `.env` e:

- OpenRouter: <https://openrouter.ai/keys> (cobra por uso, créditos a partir de $5)
- Groq: <https://console.groq.com/keys> (free tier generoso pra Llama 3.3)

### Telegram não envia

Cheque na ordem:
1. `TELEGRAM_ENABLED=true` no `.env`
2. `TELEGRAM_BOT_TOKEN` correto (formato `123456:ABC-DEF...`)
3. `TELEGRAM_CHAT_ID` numérico — pegue mandando `/start` pro bot e
   abrindo `https://api.telegram.org/bot<TOKEN>/getUpdates`. O ID está
   em `result[].message.chat.id`.
4. Você falou com o bot pelo menos uma vez (caso contrário o bot não
   pode iniciar conversa).

---

## Roadmap

V1 (esse repo) — assistente analítico:
- ✅ Coleta multi-TF + indicadores
- ✅ Estratégias + score + risco
- ✅ IA comentando setups
- ✅ Telegram + dashboard + paper trade + backtest

V2 — inteligência:
- [ ] Sentimento Twitter/X (via Nitter ou X API)
- [ ] On-chain básico (CryptoQuant, Glassnode free tier)
- [ ] Detecção de whale (>$1M trades, liquidações cluster)
- [ ] ML para classificar regime (HMM ou XGBoost simples)

V3 — execução:
- [ ] Módulo `execution/` opcional, com `enable_auto_trade=False` por padrão
- [ ] Position management (parciais, trailing, breakeven)
- [ ] Integração Bybit
- [ ] Webhooks TradingView

---

## Deploy barato

| Opção                   | Custo/mês  | Notas |
|-------------------------|-----------|-------|
| **Notebook próprio**    | $0        | Liga quando quiser, simples |
| **Raspberry Pi 4 / 5**  | luz       | Roda confortável; SSD recomendado |
| **VPS Hetzner CX11**    | ~€4       | Suficiente, baixa latência EU/US |
| **Oracle Cloud Free**   | $0        | ARM Ampere — generoso pra esse uso |
| **Railway / Fly.io**    | $0-5      | Deploy git-push, bom pra começar |

Imagem leve (sem Docker até precisar): `pip install -r requirements.txt` e
`systemd` unit chamando `python main.py`.

---

## Aviso

Esse software é para fins **educacionais e de pesquisa pessoal**.
Trading com alavancagem em futuros tem risco de perda **superior ao capital**.
Os sinais são heurísticos. Os comentários da IA são interpretações de
indicadores, não previsão. Use por sua conta e risco.
