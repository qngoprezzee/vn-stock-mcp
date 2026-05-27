"""Isolated subprocess worker for vnstock calls. Accepts func_name + JSON kwargs via argv."""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

func_name = sys.argv[1]
kwargs = json.loads(sys.argv[2])

# Silence all stdout noise from vnstock banners during imports
_real_stdout = sys.stdout
sys.stdout = open(os.devnull, "w")


def _df_to_json(df):
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso", default_handler=str))


try:
    from vnstock import Quote, Company, Finance
    sys.stdout = _real_stdout  # restore before any print()

    if func_name == "company_overview":
        result = _df_to_json(Company(symbol=kwargs["ticker"], source="VCI").overview())

    elif func_name == "company_news":
        df = Company(symbol=kwargs["ticker"], source="VCI").news()
        cols = ["news_title", "news_source", "news_source_link", "public_date", "news_short_content"]
        result = _df_to_json(df[cols].head(kwargs.get("limit", 20)))

    elif func_name == "company_events":
        df = Company(symbol=kwargs["ticker"], source="VCI").events()
        cols = ["event_name_en", "event_title_en", "display_date1", "value_per_share", "category"]
        result = _df_to_json(df[cols].head(kwargs.get("limit", 15)))

    elif func_name == "quote_history":
        from datetime import date
        df = Quote(symbol=kwargs["ticker"], source="VCI").history(
            start=kwargs.get("start", "2026-01-01"),
            end=kwargs.get("end", date.today().isoformat()),
            interval="1D",
        )
        result = _df_to_json(df.tail(5))

    elif func_name == "quote_history_full":
        from datetime import date, timedelta
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=kwargs.get("days", 365))).isoformat()
        df = Quote(symbol=kwargs["ticker"], source="VCI").history(
            start=start, end=end, interval="1D",
        )
        result = _df_to_json(df)

    elif func_name == "income_statement":
        result = _df_to_json(
            Finance(symbol=kwargs["ticker"], source="VCI")
            .income_statement(period=kwargs.get("period", "year"), lang="en")
        )

    elif func_name == "balance_sheet":
        result = _df_to_json(
            Finance(symbol=kwargs["ticker"], source="VCI")
            .balance_sheet(period=kwargs.get("period", "year"), lang="en")
        )

    elif func_name == "cash_flow":
        result = _df_to_json(
            Finance(symbol=kwargs["ticker"], source="VCI")
            .cash_flow(period=kwargs.get("period", "year"), lang="en")
        )

    else:
        result = {"error": f"Unknown function: {func_name}"}

    print(json.dumps(result))

except SystemExit as e:
    sys.stdout = _real_stdout
    print(f"Rate limit: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    sys.stdout = _real_stdout
    print(json.dumps({"error": str(e)}))
