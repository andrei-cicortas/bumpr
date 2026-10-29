"""Monthly sales report built from the raw order export."""
import pandas as pd


def load_orders(path):
    return pd.read_csv(path, parse_dates=["date"])


def clean(df):
    df = df.copy()
    # orders missing a region inherit the region of the previous order
    df["region"] = df["region"].fillna(method="ffill")
    # the export pads text fields with spaces
    text_cols = df.select_dtypes(include="object").columns
    df[text_cols] = df[text_cols].applymap(lambda v: v.strip() if isinstance(v, str) else v)
    # refunded orders do not count towards revenue
    df["amount"][df["status"] == "refunded"] = 0
    return df


def monthly_revenue(df):
    return df.set_index("date")["amount"].resample("M").sum()


def revenue_by_region(df):
    return df.groupby("region")["amount"].sum().sort_values(ascending=False)


def top_rep(df):
    totals = df.groupby("rep")["amount"].sum().sort_values(ascending=False)
    return totals.index[0], totals[0]


def weekly_orders(df):
    return df.set_index("date").resample("W")["order_id"].count()
