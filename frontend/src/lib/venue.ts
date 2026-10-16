/**
 * Which venue a symbol trades on — the frontend mirror of the backend's
 * `vibe_quant.data.catalog.is_equity_symbol`: a plain ticker (`AAPL`, `BRK.B`) is
 * an Alpaca US stock, a USDT/USDC/BUSD pair is a Binance perpetual.
 */

const EQUITY_SYMBOL = /^[A-Z][A-Z.]{0,5}$/;
const CRYPTO_QUOTES = ["USDT", "USDC", "BUSD"];

export function isStockSymbol(symbol: string): boolean {
  return !CRYPTO_QUOTES.some((q) => symbol.endsWith(q)) && EQUITY_SYMBOL.test(symbol);
}

/** True when every symbol is a stock (a run never mixes venues). */
export function isStockRun(symbols: readonly string[]): boolean {
  return symbols.length > 0 && symbols.every(isStockSymbol);
}

/** The catalog's default symbol for a form: BTCUSDT when present, else the first one. */
export function defaultCatalogSymbol(symbols: readonly string[]): string | undefined {
  return symbols.find((s) => s === "BTCUSDT") ?? symbols[0];
}
