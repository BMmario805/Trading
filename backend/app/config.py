"""Catálogo de instrumentos seguidos por la aplicación.

Cada símbolo define de dónde salen sus precios (binance / yahoo) y cómo
buscarlo en las fuentes de sentimiento (StockTwits, Reddit).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Symbol:
    id: str                      # identificador interno usado por la API
    label: str                   # nombre mostrado en la UI
    category: str                # metales | cripto | acciones | indices | forex | futuros
    source: str                  # binance | yahoo | swissquote
    source_symbol: str           # símbolo en la fuente de precios
    stocktwits: str | tuple[str, ...] | None = None
    reddit_query: str = ""
    reddit_subs: tuple[str, ...] = field(default_factory=tuple)
    is_crypto: bool = False
    note: str = ""               # aclaración sobre el origen de los datos (UI)


SYMBOLS: list[Symbol] = [
    # --- Metales: el foco principal es el oro ---
    # Spot real de Swissquote para el precio en vivo; velas de futuros GC
    # reescaladas a la base spot actual (Yahoo no ofrece velas de XAUUSD).
    Symbol("XAUUSD", "Oro Spot (XAU/USD)", "metales", "swissquote", "XAU/USD",
           # el stream XAUUSD apenas lleva etiquetas: se agregan los streams
           # de futuros (GC_F) y del ETF (GLD), que sí las tienen
           ("XAUUSD", "GC_F", "GLD"),
           "gold OR XAUUSD OR XAU",
           ("Gold", "Forex", "Daytrading", "wallstreetbets"),
           note="Precio: spot Swissquote · Velas: futuros GC ajustados a la base spot"),
    Symbol("PAXG", "Oro 24/7 (PAXG)", "metales", "binance", "PAXGUSDT",
           "PAXG.X", "PAXG OR gold price", ("Gold", "CryptoCurrency"), True,
           note="Oro tokenizado (1 PAXG = 1 oz): cotiza 24/7 en Binance"),

    # --- Cripto (Binance) ---
    Symbol("BTC", "Bitcoin", "cripto", "binance", "BTCUSDT", "BTC.X",
           "bitcoin OR BTC", ("CryptoCurrency", "Bitcoin", "CryptoMarkets"), True),
    Symbol("ETH", "Ethereum", "cripto", "binance", "ETHUSDT", "ETH.X",
           "ethereum OR ETH", ("CryptoCurrency", "ethereum", "CryptoMarkets"), True),
    Symbol("SOL", "Solana", "cripto", "binance", "SOLUSDT", "SOL.X",
           "solana OR SOL", ("CryptoCurrency", "solana", "CryptoMarkets"), True),
    Symbol("XRP", "XRP", "cripto", "binance", "XRPUSDT", "XRP.X",
           "XRP OR ripple", ("CryptoCurrency", "Ripple", "CryptoMarkets"), True),
    Symbol("DOGE", "Dogecoin", "cripto", "binance", "DOGEUSDT", "DOGE.X",
           "dogecoin OR DOGE", ("CryptoCurrency", "dogecoin"), True),

    # --- Acciones (Yahoo) ---
    Symbol("AAPL", "Apple", "acciones", "yahoo", "AAPL", "AAPL",
           "AAPL OR Apple stock", ("wallstreetbets", "stocks", "investing")),
    Symbol("MSFT", "Microsoft", "acciones", "yahoo", "MSFT", "MSFT",
           "MSFT OR Microsoft stock", ("wallstreetbets", "stocks", "investing")),
    Symbol("NVDA", "NVIDIA", "acciones", "yahoo", "NVDA", "NVDA",
           "NVDA OR Nvidia", ("wallstreetbets", "stocks", "investing")),
    Symbol("TSLA", "Tesla", "acciones", "yahoo", "TSLA", "TSLA",
           "TSLA OR Tesla stock", ("wallstreetbets", "stocks", "investing")),
    Symbol("AMZN", "Amazon", "acciones", "yahoo", "AMZN", "AMZN",
           "AMZN OR Amazon stock", ("wallstreetbets", "stocks", "investing")),
    Symbol("META", "Meta", "acciones", "yahoo", "META", "META",
           "META stock", ("wallstreetbets", "stocks", "investing")),
    Symbol("GOOGL", "Alphabet", "acciones", "yahoo", "GOOGL", "GOOGL",
           "GOOGL OR Google stock", ("wallstreetbets", "stocks", "investing")),

    # --- Índices (Yahoo, vía ETF más líquidos) ---
    Symbol("SPY", "S&P 500 (SPY)", "indices", "yahoo", "SPY", "SPY",
           "SPY OR S&P 500", ("wallstreetbets", "stocks", "investing")),
    Symbol("QQQ", "Nasdaq 100 (QQQ)", "indices", "yahoo", "QQQ", "QQQ",
           "QQQ OR Nasdaq", ("wallstreetbets", "stocks", "investing")),

    # --- Forex (Yahoo) ---
    Symbol("EURUSD", "EUR/USD", "forex", "yahoo", "EURUSD=X", None,
           "EURUSD OR euro dollar", ("Forex", "Daytrading")),
    Symbol("GBPUSD", "GBP/USD", "forex", "yahoo", "GBPUSD=X", None,
           "GBPUSD OR cable forex", ("Forex", "Daytrading")),
    Symbol("USDJPY", "USD/JPY", "forex", "yahoo", "USDJPY=X", None,
           "USDJPY OR yen forex", ("Forex", "Daytrading")),

    # --- Futuros (Yahoo) ---
    Symbol("ES", "E-mini S&P 500", "futuros", "yahoo", "ES=F", "ES_F",
           "ES futures OR S&P futures", ("FuturesTrading", "Daytrading")),
    Symbol("NQ", "E-mini Nasdaq", "futuros", "yahoo", "NQ=F", "NQ_F",
           "NQ futures OR Nasdaq futures", ("FuturesTrading", "Daytrading")),
    Symbol("GC", "Oro (Gold)", "futuros", "yahoo", "GC=F", "GLD",
           "gold price OR gold futures", ("Gold", "wallstreetbets", "investing")),
    Symbol("CL", "Petróleo WTI", "futuros", "yahoo", "CL=F", "USO",
           "oil price OR crude WTI", ("oil", "investing", "Daytrading")),
]

SYMBOLS_BY_ID: dict[str, Symbol] = {s.id: s for s in SYMBOLS}

# Segundos entre sondeos de precio en vivo, por fuente
POLL_INTERVAL = {"binance": 2.0, "yahoo": 6.0, "swissquote": 2.0}

# Caché de sentimiento (segundos)
SENTIMENT_TTL = 300
