"""
Batch-fetch fundamentals for all long-term watchlist tickers and store to DB.
Run manually or called by thesis.py on Sunday.

    python -m scripts.fetch_fundamentals
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.fundamentals import get_fundamentals
from data.db import init_schema, store_fundamentals
from config.settings import LONG_TERM_WATCHLIST
from utils.logger import info, warning, error


def run():
    init_schema()
    success = 0
    for ticker in LONG_TERM_WATCHLIST:
        data = get_fundamentals(ticker)
        if data:
            store_fundamentals(ticker, data)
            info(
                f"{ticker}: P/E={data['pe_ratio']:.1f}  "
                f"EPS {data['eps_growth']*100:+.1f}%  "
                f"Rev {data['revenue_growth']*100:+.1f}%  "
                f"Margin {data['gross_margin']*100:.1f}%",
                source="fundamentals",
            )
            success += 1
        else:
            warning(f"{ticker}: no data returned (FMP_KEY missing or API error)", source="fundamentals")

    info(f"Fundamentals complete: {success}/{len(LONG_TERM_WATCHLIST)} tickers stored", source="fundamentals")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        error(f"Fundamentals fetch crashed: {e}", source="fundamentals", exc=e)
        sys.exit(1)
