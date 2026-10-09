import csv
import json
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import pandas as pd
import matplotlib

matplotlib.use("TkAgg")

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# =========================================================
# CONFIGURATION
# =========================================================

APP_TITLE = "CURRENCY & GOLD | MARKET PULSE"

BASE_URL = (
    "https://raw.githubusercontent.com/"
    "iran-market/iran-market.github.io/main/data"
)

POPULAR_URL = f"{BASE_URL}/popular.json"
LATEST_URL = f"{BASE_URL}/latest-toman.json"
SYMBOLS_URL = f"{BASE_URL}/symbols.json"
HISTORY_INDEX_URL = f"{BASE_URL}/history/index.json"

REFRESH_INTERVAL = 5 * 60 * 1000

APP_DIR = Path.home() / "CurrencyGoldDashboard"
APP_DIR.mkdir(parents=True, exist_ok=True)

CACHE_FILE = APP_DIR / "market_cache.json"

BG = "#071426"
PANEL = "#10243B"
PANEL_LIGHT = "#16314D"
GOLD = "#D9B45B"
TURQUOISE = "#5DE0D0"
WHITE = "#EDF3FB"
MUTED = "#9FB1C8"
GREEN = "#42D392"
RED = "#FF657A"

PREFERRED_ASSETS = {
    "USD_IRR_FREE",
    "EUR_IRR_FREE",
    "GOLD_18K_IRR",
    "MESGHAL_IRR",
    "SEKEH_EMAMI_IRR",
    "GOLD_OUNCE_USD",
}


# =========================================================
# DATA UTILITIES
# =========================================================

def fetch_json(url, timeout=15):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CurrencyGoldDashboard/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        content = response.read().decode("utf-8-sig")

    return json.loads(content)


def get_value(data, keys, default=None):
    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)

        if value is not None and value != "":
            return value

    return default


def to_number(value):
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if value is None:
        return None

    try:
        value = str(value).strip()
        value = value.replace(",", "").replace("٬", "")
        value = value.replace("٫", ".")

        for suffix in ("تومان", "ریال"):
            value = value.replace(suffix, "")

        return float(value.strip())

    except (ValueError, TypeError):
        return None


def format_number(value):
    if value is None:
        return "—"

    if abs(value) >= 100:
        return f"{value:,.0f}"

    if abs(value) >= 1:
        return f"{value:,.2f}"

    return f"{value:,.4f}"


def parse_datetime(value):
    if not value:
        return None

    try:
        value = str(value).strip()

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        result = datetime.fromisoformat(value)

        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)

        return result

    except (ValueError, TypeError):
        return None


def get_rows(payload):
    """
    Supports common JSON structures:
    {"data": [...]}
    {"data": {"SYMBOL": {...}}}
    {"SYMBOL": {...}}
    [...]
    """

    if isinstance(payload, list):
        return [
            item for item in payload
            if isinstance(item, dict)
        ]

    if not isinstance(payload, dict):
        return []

    for key in ("data", "items", "symbols", "results", "rows"):
        value = payload.get(key)

        if isinstance(value, list):
            return [
                item for item in value
                if isinstance(item, dict)
            ]

        if isinstance(value, dict):
            result = []

            for symbol, item in value.items():
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("symbol", symbol)
                    result.append(item)

            if result:
                return result

    result = []

    for symbol, item in payload.items():
        if isinstance(item, dict):
            item = dict(item)
            item.setdefault("symbol", symbol)

            if any(
                key in item
                for key in ("price", "value", "close", "last")
            ):
                result.append(item)

    return result


def normalize_asset(item):
    symbol = str(
        get_value(
            item,
            ("symbol", "id", "code", "key"),
            ""
        )
    ).strip()

    name = str(
        get_value(
            item,
            ("name", "title", "label", "display_name"),
            symbol or "دارایی"
        )
    )

    price_object = item.get("price")

    if isinstance(price_object, dict):
        price = to_number(
            get_value(
                price_object,
                ("value", "amount", "raw", "number")
            )
        )

        unit = get_value(
            price_object,
            ("unit", "currency"),
            ""
        )

    else:
        price = to_number(
            get_value(
                item,
                (
                    "price",
                    "value",
                    "close",
                    "last",
                    "rate",
                    "current_price",
                )
            )
        )

        unit = ""

    unit = get_value(
        item,
        ("unit", "price_unit", "currency", "quote_currency"),
        unit
    )

    change_object = item.get("change")
    change = None

    if isinstance(change_object, dict):
        change = to_number(
            get_value(
                change_object,
                ("percent", "percentage", "value")
            )
        )

        direction = str(
            get_value(
                change_object,
                ("status", "direction"),
                ""
            )
        ).lower()

        if change is not None:
            if direction in ("down", "low", "negative", "decrease"):
                change = -abs(change)

            elif direction in ("up", "high", "positive", "increase"):
                change = abs(change)

    if change is None:
        change = to_number(
            get_value(
                item,
                (
                    "change_percent",
                    "changePercent",
                    "percent_change",
                )
            )
        )

    updated = get_value(
        item,
        (
            "updated_at",
            "updatedAt",
            "timestamp",
            "last_updated",
            "datetime",
        ),
        ""
    )

    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "change": change,
        "unit": str(unit or ""),
        "updated": str(updated or ""),
    }


# =========================================================
# MARKET DATA CLIENT
# =========================================================

class MarketClient:

    def fetch_prices(self):
        errors = []

        for url in (POPULAR_URL, LATEST_URL):
            try:
                payload = fetch_json(url)
                rows = get_rows(payload)

                assets = [
                    normalize_asset(item)
                    for item in rows
                ]

                assets = [
                    item for item in assets
                    if item["symbol"] and item["price"] is not None
                ]

                if assets:
                    return assets, url

            except Exception as exc:
                errors.append(f"{url}\n{exc}")

        raise RuntimeError(
            "دریافت قیمت‌ها ناموفق بود.\n\n"
            + "\n\n".join(errors)
        )

    def fetch_history(self, symbol):
        safe_symbol = urllib.parse.quote(symbol, safe="_-")

        url = (
            f"{BASE_URL}/history/"
            f"{safe_symbol}.json"
        )

        payload = fetch_json(url)

        rows = get_rows(payload)

        if isinstance(payload, dict):
            for key in (
                "data",
                "history",
                "items",
                "candles",
                "prices",
            ):
                if isinstance(payload.get(key), list):
                    rows = payload[key]
                    break

        points = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            timestamp = get_value(
                row,
                ("date", "timestamp", "time", "datetime", "t")
            )

            price = to_number(
                get_value(
                    row,
                    ("close", "price", "value", "last", "c")
                )
            )

            if price is None or timestamp is None:
                continue

            dt = parse_datetime(timestamp)

            if dt:
                points.append((dt, price))

        points.sort(key=lambda point: point[0])

        return points


# =========================================================
# MAIN APPLICATION
# =========================================================

class CurrencyGoldDashboard(tk.Tk):

    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1280x820")
        self.minsize(1000, 680)
        self.configure(bg=BG)

        self.client = MarketClient()

        self.assets = []
        self.history_points = []
        self.alerts = {}

        self.current_history_symbol = None
        self.fetching = False

        self.status_var = tk.StringVar(
            value="آماده دریافت اطلاعات"
        )

        self.updated_var = tk.StringVar(
            value="آخرین دریافت: —"
        )

        self.filter_var = tk.StringVar()
        self.selected_var = tk.StringVar()

        self.setup_styles()
        self.build_interface()
        self.load_cache()

        self.after(500, self.refresh_prices)
        self.after(
            REFRESH_INTERVAL,
            self.automatic_refresh
        )

    # -----------------------------------------------------
    # STYLES
    # -----------------------------------------------------

    def setup_styles(self):
        style = ttk.Style(self)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=WHITE,
            rowheight=30,
            borderwidth=0,
            font=("Segoe UI", 10),
        )

        style.configure(
            "Treeview.Heading",
            background=PANEL_LIGHT,
            foreground=GOLD,
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        )

        style.map(
            "Treeview",
            background=[("selected", "#28506D")],
            foreground=[("selected", WHITE)],
        )

        style.configure(
            "TButton",
            background=PANEL_LIGHT,
            foreground=WHITE,
            padding=(10, 7),
            borderwidth=0,
        )

        style.map(
            "TButton",
            background=[("active", "#28506D")],
        )

        style.configure(
            "TEntry",
            fieldbackground=PANEL_LIGHT,
            foreground=WHITE,
        )

        style.configure(
            "TCombobox",
            fieldbackground=PANEL_LIGHT,
            foreground=WHITE,
        )

    # -----------------------------------------------------
    # USER INTERFACE
    # -----------------------------------------------------

    def build_interface(self):

        header = tk.Frame(self, bg=BG)
        header.pack(
            fill="x",
            padx=22,
            pady=(18, 12)
        )

        tk.Label(
            header,
            text="MARKET PULSE",
            bg=BG,
            fg=GOLD,
            font=("Segoe UI", 24, "bold"),
        ).pack(side="left")

        tk.Label(
            header,
            text="CURRENCY  •  GOLD  •  COINS",
            bg=BG,
            fg=TURQUOISE,font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=18, pady=(10, 0))

        ttk.Button(
            header,
            text="دریافت قیمت‌ها",
            command=self.refresh_prices,
        ).pack(side="right", padx=4)

        ttk.Button(
            header,
            text="Excel",
            command=self.export_excel,
        ).pack(side="right", padx=4)

        ttk.Button(
            header,
            text="CSV",
            command=self.export_csv,
        ).pack(side="right", padx=4)

        # STATUS BAR

        status_frame = tk.Frame(
            self,
            bg=PANEL,
            highlightbackground="#29445E",
            highlightthickness=1,
        )

        status_frame.pack(
            fill="x",
            padx=22,
            pady=(0, 12)
        )

        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=PANEL,
            fg=WHITE,
            font=("Segoe UI", 10),
        )

        self.status_label.pack(
            side="left",
            padx=12,
            pady=10
        )

        tk.Label(
            status_frame,
            textvariable=self.updated_var,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(
            side="right",
            padx=12
        )

        # PRICE CARDS

        self.cards_frame = tk.Frame(
            self,
            bg=BG
        )

        self.cards_frame.pack(
            fill="x",
            padx=18,
            pady=(0, 14)
        )

        # MAIN CONTENT

        content = tk.Frame(
            self,
            bg=BG
        )

        content.pack(
            fill="both",
            expand=True,
            padx=22,
            pady=(0, 12)
        )

        left = tk.Frame(
            content,
            bg=BG
        )

        left.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(0, 12)
        )

        right = tk.Frame(
            content,
            bg=PANEL,
            highlightbackground="#29445E",
            highlightthickness=1,
            width=470,
        )

        right.pack(
            side="right",
            fill="both",
            expand=True
        )

        right.pack_propagate(False)

        # TABLE HEADER

        table_header = tk.Frame(
            left,
            bg=BG
        )

        table_header.pack(
            fill="x",
            pady=(0, 8)
        )

        tk.Label(
            table_header,
            text="قیمت دارایی‌ها",
            bg=BG,
            fg=WHITE,
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")

        search_entry = ttk.Entry(
            table_header,
            textvariable=self.filter_var,
            width=22,
        )

        search_entry.pack(side="right")

        self.filter_var.trace_add(
            "write",
            lambda *_: self.populate_table()
        )

        # PRICE TABLE

        columns = (
            "name",
            "symbol",
            "price",
            "change",
            "unit",
        )

        self.table = ttk.Treeview(
            left,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        headers = {
            "name": "نام دارایی",
            "symbol": "نماد",
            "price": "قیمت",
            "change": "تغییر درصدی",
            "unit": "واحد",
        }

        widths = {
            "name": 130,
            "symbol": 140,
            "price": 110,
            "change": 90,
            "unit": 65,
        }

        for column in columns:
            self.table.heading(
                column,
                text=headers[column]
            )

            self.table.column(
                column,
                width=widths[column],
                anchor="center",
            )

        self.table.pack(
            fill="both",
            expand=True
        )

        self.table.bind(
            "<<TreeviewSelect>>",self.on_table_select
        )

        self.table.tag_configure(
            "up",
            foreground=GREEN
        )

        self.table.tag_configure(
            "down",
            foreground=RED
        )

        self.table.tag_configure(
            "normal",
            foreground=WHITE
        )

        # CHART AREA

        tk.Label(
            right,
            text="تحلیل روند قیمت",
            bg=PANEL,
            fg=GOLD,
            font=("Segoe UI", 15, "bold"),
        ).pack(
            anchor="w",
            padx=14,
            pady=(14, 8)
        )

        chart_controls = tk.Frame(
            right,
            bg=PANEL
        )

        chart_controls.pack(
            fill="x",
            padx=12,
            pady=(0, 8)
        )

        tk.Label(
            chart_controls,
            text="دارایی:",
            bg=PANEL,
            fg=MUTED,
        ).pack(side="left")

        self.asset_combo = ttk.Combobox(
            chart_controls,
            textvariable=self.selected_var,
            state="readonly",
            width=25,
        )

        self.asset_combo.pack(
            side="left",
            padx=6
        )

        self.asset_combo.bind(
            "<<ComboboxSelected>>",
            self.load_selected_history
        )

        ttk.Button(
            chart_controls,
            text="نمودار",
            command=self.load_selected_history,
        ).pack(side="left", padx=4)

        self.chart_host = tk.Frame(
            right,
            bg=PANEL
        )

        self.chart_host.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=4
        )

        self.chart_message = tk.Label(
            self.chart_host,
            text="یک دارایی انتخاب کن.",
            bg=PANEL,
            fg=MUTED,
        )

        self.chart_message.pack(expand=True)

        # ALERTS

        alert_frame = tk.Frame(
            right,
            bg=PANEL_LIGHT
        )

        alert_frame.pack(
            fill="x",
            padx=12,
            pady=(8, 12)
        )

        tk.Label(
            alert_frame,
            text="هشدار قیمت هدف",
            bg=PANEL_LIGHT,
            fg=GOLD,
            font=("Segoe UI", 10, "bold"),
        ).pack(
            side="left",
            padx=8
        )

        self.target_entry = ttk.Entry(
            alert_frame,
            width=12
        )

        self.target_entry.pack(
            side="left",
            padx=5,
            pady=7
        )

        ttk.Button(
            alert_frame,
            text="تنظیم",
            command=self.set_alert,
        ).pack(side="left", padx=3)

        ttk.Button(
            alert_frame,
            text="حذف",
            command=self.remove_alert,
        ).pack(side="left", padx=3)

        # FOOTER

        footer = tk.Frame(
            self,
            bg=BG
        )

        footer.pack(
            fill="x",
            padx=22,
            pady=(0, 12)
        )

        tk.Label(
            footer,
            text="منبع داده: Iran Market Data",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")

        tk.Label(
            footer,
            text="داده‌ها ممکن است با تأخیر منتشر شوند.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right")

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    def load_cache(self):
        if not CACHE_FILE.exists():
            return

        try:
            data = json.loads(
                CACHE_FILE.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(data.get("assets"), list):
                self.assets = data["assets"]

                self.render_all()

                self.status_var.set(
                    "نمایش داده ذخیره‌شده؛ "
                    "در حال بررسی منبع آنلاین..."
                )

        except Exception:
            pass

    def save_cache(self):
        data = {
            "assets": self.assets,
            "saved_at": datetime.now().astimezone().isoformat(),
        }

        CACHE_FILE.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8",
        )

    # -----------------------------------------------------
    # FETCH PRICES
    # -----------------------------------------------------

    def refresh_prices(self):
        if self.fetching:
            return

        self.fetching = True

        self.status_var.set(
            "در حال دریافت اطلاعات..."
        )

        self.status_label.configure(
            fg=TURQUOISE
        )

        threading.Thread(
            target=self.fetch_worker,
            daemon=True,
        ).start()

    def fetch_worker(self):
        try:
            assets, source = self.client.fetch_prices()

            self.after(
                0,
                lambda: self.fetch_success(
                    assets,
                    source
                )
            )

        except Exception as exc:
            message = str(exc)

            self.after(
                0,
                lambda: self.fetch_failure(message)
            )

    def fetch_success(self, assets, source):
        self.fetching = False

        self.assets = assets

        try:
            self.save_cache()
        except Exception:
            pass

        self.render_all()

        self.status_var.set(
            f"دریافت موفق؛ {len(assets)} دارایی"
        )

        self.status_label.configure(
            fg=GREEN
        )

        self.updated_var.set(
            "آخرین دریافت برنامه: "
            + datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

        self.check_alerts()

    def fetch_failure(self, error):
        self.fetching = False

        self.status_var.set(
            "دریافت ناموفق؛ داده ذخیره‌شده ممکن است موجود باشد."
        )

        self.status_label.configure(
            fg=RED
        )

        messagebox.showwarning(
            "خطای دریافت اطلاعات",
            error[:1500]
        )

    def automatic_refresh(self):
        self.refresh_prices()

        self.after(
            REFRESH_INTERVAL,
            self.automatic_refresh
        )

    # -----------------------------------------------------
    # RENDER DATA
    # -----------------------------------------------------

    def render_all(self):
        self.populate_table()
        self.render_cards()

        labels = [
            self.asset_label(asset)
            for asset in self.assets
        ]

        self.asset_combo["values"] = labels

        if labels and self.selected_var.get() not in labels:
            self.selected_var.set(labels[0])

    def asset_label(self, asset):
        return (
            f'{asset["name"]} '
            f'[{asset["symbol"]}]'
        )

    def populate_table(self):
        if not hasattr(self, "table"):
            return

        self.table.delete(
            *self.table.get_children()
        )

        query = self.filter_var.get().strip().lower()

        for asset in self.assets:
            if query:
                searchable = (
                    asset["name"]
                    + " "
                    + asset["symbol"]
                ).lower()

                if query not in searchable:
                    continue

            change = asset.get("change")

            if change is None:
                change_text = "—"
                tag = "normal"

            else:
                change_text = f"{change:+.2f}%"

                if change > 0:
                    tag = "up"
                elif change < 0:
                    tag = "down"
                else:
                    tag = "normal"

            self.table.insert(
                "",
                "end",
                iid=asset["symbol"],
                values=(
                    asset["name"],asset["symbol"],
                    format_number(asset["price"]),
                    change_text,
                    asset.get("unit", ""),
                ),
                tags=(tag,),
            )

    def render_cards(self):
        for widget in self.cards_frame.winfo_children():
            widget.destroy()

        if not self.assets:
            return

        preferred = [
            asset for asset in self.assets
            if asset["symbol"] in PREFERRED_ASSETS
        ]

        selected_assets = preferred[:6]

        if not selected_assets:
            selected_assets = self.assets[:6]

        for index, asset in enumerate(selected_assets):
            card = tk.Frame(
                self.cards_frame,
                bg=PANEL,
                highlightbackground="#29445E",
                highlightthickness=1,
            )

            card.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=4,
            )

            self.cards_frame.grid_columnconfigure(
                index,
                weight=1,
                uniform="cards",
            )

            tk.Label(
                card,
                text=asset["name"],
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 10, "bold"),
                wraplength=140,
            ).pack(
                anchor="w",
                padx=10,
                pady=(10, 4)
            )

            tk.Label(
                card,
                text=format_number(asset["price"]),
                bg=PANEL,
                fg=GOLD,
                font=("Segoe UI", 15, "bold"),
            ).pack(
                anchor="w",
                padx=10
            )

            change = asset.get("change")

            if change is None:
                text = "تغییر: —"
                color = MUTED

            else:
                text = f"تغییر: {change:+.2f}%"

                color = (
                    GREEN if change > 0
                    else RED if change < 0
                    else MUTED
                )

            tk.Label(
                card,
                text=text,
                bg=PANEL,
                fg=color,
                font=("Segoe UI", 9),
            ).pack(
                anchor="w",
                padx=10,
                pady=(4, 10)
            )

    # -----------------------------------------------------
    # HISTORY CHART
    # -----------------------------------------------------

    def on_table_select(self, _event=None):
        selection = self.table.selection()

        if not selection:
            return

        symbol = selection[0]

        asset = next(
            (
                item for item in self.assets
                if item["symbol"] == symbol
            ),
            None
        )

        if asset:
            self.selected_var.set(
                self.asset_label(asset)
            )

    def load_selected_history(self, _event=None):
        label = self.selected_var.get()

        asset = next(
            (
                item for item in self.assets
                if self.asset_label(item) == label
            ),
            None
        )

        if not asset:
            return

        symbol = asset["symbol"]

        self.current_history_symbol = symbol

        for widget in self.chart_host.winfo_children():
            widget.destroy()

        tk.Label(
            self.chart_host,
            text="در حال دریافت تاریخچه...",
            bg=PANEL,
            fg=MUTED,
        ).pack(expand=True)

        threading.Thread(
            target=self.history_worker,
            args=(symbol,),
            daemon=True,
        ).start()

    def history_worker(self, symbol):
        try:
            points = self.client.fetch_history(symbol)

            self.after(
                0,
                lambda: self.show_history(
                    symbol,
                    points,
                    None
                )
            )

        except Exception as exc:
            error = str(exc)

            self.after(
                0,
                lambda: self.show_history(
                    symbol,
                    [],
                    error
                )
            )

    def show_history(self, symbol, points, error):
        if symbol != self.current_history_symbol:
            return

        for widget in self.chart_host.winfo_children():
            widget.destroy()

        self.history_points = points

        if not points:
            message = (
                "تاریخچه برای این دارایی در دسترس نیست."
                if not error
                else "دریافت تاریخچه ناموفق بود."
            )

            tk.Label(
                self.chart_host,
                text=message,
                bg=PANEL,
                fg=MUTED,
                wraplength=380,
            ).pack(expand=True)

            return

        figure = Figure(
            figsize=(5, 3.5),
            dpi=95,
            facecolor=PANEL,
        )

        axis = figure.add_subplot(111)
        axis.set_facecolor(PANEL)

        dates = [point[0] for point in points]
        prices = [point[1] for point in points]

        axis.plot(
            dates,
            prices,
            color=TURQUOISE,
            linewidth=2,
        )

        axis.fill_between(
            dates,
            prices,
            min(prices),
            color=TURQUOISE,
            alpha=0.10,
        )

        axis.set_title(
            symbol,
            color=GOLD,
            fontsize=10,
        )

        axis.tick_params(
            colors=MUTED,
            labelsize=8,
        )

        axis.grid(
            True,
            color="#29445E",
            alpha=0.5,
        )

        for spine in axis.spines.values():
            spine.set_color("#35506B")

        figure.autofmt_xdate()
        figure.tight_layout()

        canvas = FigureCanvasTkAgg(
            figure,
            master=self.chart_host,
        )

        canvas.draw()

        canvas.get_tk_widget().pack(
            fill="both",
            expand=True,
        )

    # -----------------------------------------------------
    # PRICE ALERTS
    # -----------------------------------------------------

    def get_selected_asset(self):
        label = self.selected_var.get()

        return next(
            (
                asset for asset in self.assets
                if self.asset_label(asset) == label
            ),
            None
        )

    def set_alert(self):
        asset = self.get_selected_asset()

        if not asset:
            messagebox.showinfo(
                "هشدار",
                "ابتدا یک دارایی انتخاب کن."
            )
            return

        target = to_number(
            self.target_entry.get()
        )

        if target is None or target <= 0:
            messagebox.showwarning(
                "مقدار نامعتبر",
                "قیمت هدف باید عددی مثبت باشد."
            )
            return

        self.alerts[asset["symbol"]] = target

        messagebox.showinfo(
            "هشدار تنظیم شد",
            f'قیمت هدف {asset["name"]}: '
            f'{format_number(target)}'
        )

    def remove_alert(self):
        asset = self.get_selected_asset()

        if not asset:
            return

        self.alerts.pop(
            asset["symbol"],
            None
        )

        messagebox.showinfo(
            "هشدار",
            "هشدار این دارایی حذف شد."
        )

    def check_alerts(self):
        for asset in self.assets:
            symbol = asset["symbol"]

            if symbol not in self.alerts:
                continue

            target = self.alerts[symbol]
            price = asset["price"]

            if price is not None and price >= target:
                messagebox.showinfo(
                    "هشدار قیمت",
                    f'{asset["name"]} به قیمت هدف رسید.\n'
                    f'قیمت فعلی: {format_number(price)}'
                )

                del self.alerts[symbol]

    # -----------------------------------------------------
    # EXPORT CSV
    # -----------------------------------------------------

    def export_csv(self):
        if not self.assets:
            messagebox.showinfo(
                "خروجی CSV",
                "داده‌ای برای ذخیره وجود ندارد."
            )
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="market_prices.csv",
        )

        if not path:
            return

        try:
            columns = [
                "symbol",
                "name",
                "price",
                "change",
                "unit",
                "updated",
            ]

            with open(
                path,
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as file:

                writer = csv.DictWriter(
                    file,
                    fieldnames=columns,
                )

                writer.writeheader()

                for asset in self.assets:
                    writer.writerow({
                        key: asset.get(key, "")
                        for key in columns
                    })

            messagebox.showinfo(
                "موفق",
                f"فایل ذخیره شد:\n{path}"
            )

        except Exception as exc:
            messagebox.showerror(
                "خطای خروجی",
                str(exc)
            )

    # -----------------------------------------------------
    # EXPORT EXCEL
    # -----------------------------------------------------

    def export_excel(self):
        if not self.assets:
            messagebox.showinfo(
                "خروجی Excel",
                "داده‌ای برای ذخیره وجود ندارد."
            )
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile="market_prices.xlsx",
        )

        if not path:
            return

        try:
            columns = [
                "symbol",
                "name",
                "price",
                "change",
                "unit",
                "updated",
            ]

            dataframe = pd.DataFrame([
                {
                    key: asset.get(key, "")
                    for key in columns
                }
                for asset in self.assets
            ])

            with pd.ExcelWriter(
                path,
                engine="openpyxl",
            ) as writer:

                dataframe.to_excel(
                    writer,
                    index=False,
                    sheet_name="Market Prices",
                )

                if self.history_points:
                    history_df = pd.DataFrame([
                        {
                            "timestamp": date.isoformat(),
                            "price": price,
                        }
                        for date, price in self.history_points
                    ])

                    history_df.to_excel(
                        writer,
                        index=False,
                        sheet_name="Price History",
                    )

            messagebox.showinfo(
                "موفق",
                f"فایل Excel ذخیره شد:\n{path}"
            )

        except Exception as exc:
            messagebox.showerror(
                "خطای Excel",
                str(exc)
            )


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":
    app = CurrencyGoldDashboard()
    app.mainloop()