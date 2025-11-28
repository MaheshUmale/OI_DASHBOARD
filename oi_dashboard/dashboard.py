from dash import Dash, dcc, html, Input, Output, State, callback_context
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from database import SessionLocal, Stock, OIData
from datetime import datetime
import logging

logger = logging.getLogger('oi_dashboard.dashboard')

def init_dashboard(server):
    """Create a Dash app."""
    dash_app = Dash(
        server=server,
        routes_pathname_prefix='/',
        external_stylesheets=[dbc.themes.CYBORG],
        title="NSE OI Dashboard"
    )

    # Layout
    dash_app.layout = dbc.Container([
        dbc.Row([
            dbc.Col(html.H1("NSE OI Analysis Dashboard", className="text-center text-primary mb-4"), width=12)
        ]),

        dbc.Row([
            dbc.Col([
                html.Label("Select Stock/Index:", className="text-white"),
                dcc.Dropdown(
                    id='stock-dropdown',
                    multi=False,
                    placeholder="Select a symbol...",
                    className="text-dark"
                ),
            ], width=12, md=6),
            dbc.Col([
                 dbc.Button("Refresh Data", id="refresh-btn", color="primary", className="mt-4"),
            ], width=12, md=2)
        ], className="mb-4"),

        dbc.Tabs([
            dbc.Tab(label="Analysis", tab_id="tab-analysis"),
            dbc.Tab(label="Summary", tab_id="tab-summary"),
            dbc.Tab(label="OI Change Chart", tab_id="tab-oi-change"),
            dbc.Tab(label="OI vs Price Chart", tab_id="tab-oi-change-time-series"),
        ], id="tabs", active_tab="tab-analysis"),

        html.Div(id="tab-content", className="p-4")

    ], fluid=True, className="p-4")

    init_callbacks(dash_app)

    return dash_app

def init_callbacks(dash_app):
    @dash_app.callback(
        Output('stock-dropdown', 'options'),
        Input('refresh-btn', 'n_clicks')
    )
    def update_dropdown(n_clicks):
        db = SessionLocal()
        try:
            stocks = db.query(Stock).order_by(Stock.symbol).all()
            options = [{'label': s.symbol, 'value': s.symbol} for s in stocks]
            return options
        finally:
            db.close()

    @dash_app.callback(
        Output('tab-content', 'children'),
        [Input('tabs', 'active_tab'),
         Input('stock-dropdown', 'value'),
         Input('refresh-btn', 'n_clicks')]
    )
    def render_content(active_tab, selected_symbol, n_clicks):
        if active_tab == "tab-summary":
            return render_summary()
        elif active_tab == "tab-analysis":
            if not selected_symbol:
                return html.Div("Please select a stock to view analysis.", className="text-warning")
            return render_analysis([selected_symbol]) # Pass as a list for compatibility
        elif active_tab == "tab-oi-change":
            if not selected_symbol:
                return html.Div("Please select a stock.", className="text-warning")
            return render_oi_change_chart([selected_symbol])
        elif active_tab == "tab-oi-change-time-series":
            if not selected_symbol:
                return html.Div("Please select a stock.", className="text-warning")
            return render_oi_change_time_series_chart([selected_symbol])
        return html.Div("404: Tab not found")

    # Register OI Change Chart callback
    init_oi_change_callback(dash_app)
    init_oi_change_time_series_callback(dash_app)

def render_summary():
    db = SessionLocal()
    try:
        stocks = db.query(Stock).all()
        data = []

        # Helper to calculate interpretation
        def calculate_interpretation(current, past):
            if not past:
                return "N/A"
            change_ltp = current.ltp - past.ltp
            change_oi = current.call_oi - past.call_oi

            if change_ltp > 0 and change_oi > 0:
                return "Long Buildup"
            elif change_ltp < 0 and change_oi > 0:
                return "Short Buildup"
            elif change_ltp < 0 and change_oi < 0:
                return "Long Unwinding"
            elif change_ltp > 0 and change_oi < 0:
                return "Short Covering"
            return "Neutral"

        for stock in stocks:
            records = db.query(OIData).filter(OIData.stock_id == stock.id).order_by(OIData.id.desc()).limit(20).all()

            if not records:
                continue

            current = records[0]

            # Find past records for 3m/5m/15m interp
            def find_past_record(minutes_ago):
                target_time = datetime.strptime(f"{current.date} {current.timestamp}", "%Y-%m-%d %H:%M") - pd.Timedelta(minutes=minutes_ago)
                best_match = None
                min_diff = float('inf')
                for r in records:
                    try:
                        r_time = datetime.strptime(f"{r.date} {r.timestamp}", "%Y-%m-%d %H:%M")
                        diff = abs((target_time - r_time).total_seconds())
                        if diff < min_diff and diff <= 120:
                            min_diff = diff
                            best_match = r
                    except:
                        continue
                return best_match

            rec_3m = find_past_record(3)
            rec_5m = find_past_record(5)
            rec_15m = find_past_record(15)

            # Calculate % Change in OI (using Call OI as per existing logic)
            # Avoid division by zero
            prev_oi = current.call_oi - current.change_in_call_oi
            if prev_oi and prev_oi != 0:
                pct_oi_change = (current.change_in_call_oi / prev_oi) * 100
            else:
                pct_oi_change = 0.0

            data.append({
                'Symbol': stock.symbol,
                'LTP': current.ltp,
                'Change LTP': current.change_in_ltp,
                '% OI Change': pct_oi_change,
                'OI Interp': current.oi_interpretation,
                '3m': calculate_interpretation(current, rec_3m),
                '5m': calculate_interpretation(current, rec_5m),
                '15m': calculate_interpretation(current, rec_15m),
                'Timestamp': current.timestamp
            })

        if not data:
             return html.Div("No data available.", className="text-muted")

        df = pd.DataFrame(data)

        # Define the 4 groups
        groups = ["Long Buildup", "Short Buildup", "Short Covering", "Long Unwinding"]

        # Helper to create a table for a group
        def create_group_table(group_name, df_group):
            if df_group.empty:
                return dbc.Card([
                    dbc.CardHeader(group_name, className="text-center fw-bold"),
                    dbc.CardBody("No stocks in this category.", className="text-muted text-center")
                ], className="mb-4 h-100")

            # Sort by % OI Change descending (absolute magnitude or just descending?)
            # Usually traders want to see highest positive change for Buildups.
            # For Unwinding/Covering (negative OI change), maybe they want most negative?
            # But here we calculated % change based on the diff.
            # Let's sort by absolute magnitude of % change to show "biggest movers".
            df_group = df_group.copy()
            df_group['abs_change'] = df_group['% OI Change'].abs()
            df_group = df_group.sort_values(by='abs_change', ascending=False).drop(columns=['abs_change'])

            rows = []
            for index, row in df_group.iterrows():
                rows.append(html.Tr([
                    html.Td(row['Symbol'], className="fw-bold"),
                    html.Td(f"{row['LTP']:.2f}"),
                    html.Td(f"{row['% OI Change']:.2f}%", style={'color': 'cyan'}),
                    # html.Td(row['3m']), # Optional: hide these to save space in grid? User asked for them, let's keep but maybe abbreviate
                    html.Td(row['3m']),
                    html.Td(row['5m']),
                    html.Td(row['15m']),
                ]))

            table = dbc.Table([
                html.Thead(html.Tr([
                    html.Th("Symbol"), html.Th("LTP"), html.Th("% OI"), html.Th("3m"), html.Th("5m"), html.Th("15m")
                ])),
                html.Tbody(rows)
            ], bordered=True, color="dark", hover=True, striped=True, size="sm", className="small") # Small table

            # Set card color based on trend
            border_color = "secondary"
            if group_name == "Long Buildup": border_color = "success"
            elif group_name == "Short Buildup": border_color = "danger"
            elif group_name == "Short Covering": border_color = "success" # Bullish
            elif group_name == "Long Unwinding": border_color = "warning"

            return dbc.Card([
                dbc.CardHeader(f"{group_name} ({len(df_group)})", className=f"text-center fw-bold text-{border_color}"),
                dbc.CardBody(table, className="p-0")
            ], className=f"mb-4 h-100 border-{border_color}", style={"borderWidth": "2px"})

        # Create the 4 quadrants
        cols = []
        for group in groups:
            df_group = df[df['OI Interp'] == group]
            cols.append(dbc.Col(create_group_table(group, df_group), md=6, lg=6, className="mb-4"))

        # Handle "Neutral" or others?
        # Maybe add a "Others" section if needed, but user specifically asked for these 4.

        return dbc.Row(cols)

    except Exception as e:
        logger.exception(f"Error rendering summary: {e}")
        return html.Div(f"Error loading summary: {e}", className="text-danger")
    finally:
        db.close()

def render_analysis(selected_symbols):
    graphs = []
    db = SessionLocal()
    try:
        logger.info(f"Rendering analysis for: {selected_symbols}")
        today = datetime.now().date()

        for symbol in selected_symbols:
            stock = db.query(Stock).filter(Stock.symbol == symbol).first()
            if not stock:
                logger.warning(f"Stock {symbol} not found in DB")
                continue

            # Filter for today's data only
            records = db.query(OIData).filter(
                OIData.stock_id == stock.id,
                OIData.date == today
            ).order_by(OIData.id).all()

            if not records:
                logger.warning(f"No records found for {symbol} today")
                continue

            logger.info(f"Found {len(records)} records for {symbol} today")

            data_list = []
            for r in records:
                # Combine date and timestamp
                dt_str = f"{r.date} {r.timestamp}"
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                except ValueError:
                    # Fallback if parsing fails
                    dt = r.timestamp

                data_list.append({
                    'timestamp': dt,
                    'ltp': r.ltp,
                    'call_oi': r.call_oi,
                    'put_oi': r.put_oi,
                    'max_pain': r.max_pain if hasattr(r, 'max_pain') else None
                })

            df = pd.DataFrame(data_list)

            # Determine latest Max Pain
            latest_max_pain = None
            if 'max_pain' in df.columns:
                valid_mp = df['max_pain'].dropna()
                if not valid_mp.empty:
                    latest_max_pain = valid_mp.iloc[-1]

            # Create figure with secondary y-axis
            fig = go.Figure()

            # Add Traces
            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['ltp'], name="LTP", line=dict(color='cyan', width=1)))

            # Logic for Max Pain display
            mp_text = ""
            if latest_max_pain:
                mp_text = f" | Max Pain: {latest_max_pain}"

                # Check if Max Pain is within visible range of LTP
                ltp_min = df['ltp'].min()
                ltp_max = df['ltp'].max()
                # Add a 5% buffer to the range check
                range_buffer = (ltp_max - ltp_min) * 0.5 if ltp_max != ltp_min else ltp_max * 0.01
                if range_buffer == 0: range_buffer = ltp_max * 0.01

                if (ltp_min - range_buffer) <= latest_max_pain <= (ltp_max + range_buffer):
                     fig.add_trace(go.Scatter(x=df['timestamp'], y=df['max_pain'], name="Max Pain", line=dict(color='yellow', dash='dash', width=1)))

            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['call_oi'], name="Call OI", yaxis="y2", line=dict(color='red', dash='dot', width=1)))
            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['put_oi'], name="Put OI", yaxis="y2", line=dict(color='green', dash='dot', width=1)))

            # Layout - Compact
            fig.update_layout(
                title=dict(text=f"{symbol}{mp_text}", font=dict(size=11), y=0.98, yanchor='top'),
                template="plotly_dark",
                yaxis=dict(title=None, showgrid=False), # Hide y-axis title to save space
                yaxis2=dict(title=None, overlaying="y", side="right", showgrid=False),
                legend=dict(x=0, y=1.1, orientation="h", font=dict(size=8)),
                xaxis=dict(title=None), # Hide x-axis title
                margin=dict(l=5, r=5, t=25, b=5), # Tighter margins
                height=300 # Fixed small height
            )

            # Add to grid column (width=4 means 3 graphs per row on large screens)
            graphs.append(dbc.Col(
                dbc.Card([
                    dbc.CardBody(dcc.Graph(figure=fig, config={'displayModeBar': False}))
                ], className="mb-2 shadow-sm"), # Reduced margin bottom
                xs=12, sm=6, md=4, lg=3 # Responsive grid: 1 on mobile, 2 on sm, 3 on md, 4 on lg
            ))

        if not graphs:
             return html.Div("No data found for selected symbols today.", className="text-muted")

        return dbc.Row(graphs) # Wrap columns in a Row

    finally:
        db.close()

def render_oi_change_chart(selected_symbols):
    """Render the OI Change vs Strike chart tab with dropdown and time range selector."""
    if not selected_symbols:
        return dbc.Card(dbc.CardBody("Please select a stock to view the OI Change Chart."))

    # For this chart, we only use the first selected stock
    symbol = selected_symbols[0]

    db = SessionLocal()
    try:
        # Layout wrapped in Card
        layout = dbc.Card([
            dbc.CardHeader("OI Change vs Strike Price"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.H4(f"Displaying OI Change for: {symbol}", className="text-info"),
                        html.Label("Time Range:", className="text-white mb-2"),
                        dbc.ButtonGroup([
                            dbc.Button("5m", id="btn-5m", outline=True, color="primary", size="sm"),
                            dbc.Button("10m", id="btn-10m", outline=True, color="primary", size="sm"),
                            dbc.Button("15m", id="btn-15m", outline=True, color="primary", size="sm", active=True),
                            dbc.Button("30m", id="btn-30m", outline=True, color="primary", size="sm"),
                            dbc.Button("1h", id="btn-1h", outline=True, color="primary", size="sm"),
                            dbc.Button("2h", id="btn-2h", outline=True, color="primary", size="sm"),
                            dbc.Button("3h", id="btn-3h", outline=True, color="primary", size="sm"),
                            dbc.Button("Full Day", id="btn-full", outline=True, color="primary", size="sm"),
                        ], className="mb-4"),
                    ], width=12),
                ]),
                dbc.Row([
                    dbc.Col(html.Div(id="oi-change-chart-container"), width=12)
                ])
            ])
        ])
        return layout
    finally:
        db.close()


def init_oi_change_callback(dash_app):
    @dash_app.callback(
        Output('oi-change-chart-container', 'children'),
        [Input('stock-dropdown', 'value'),
         Input('btn-5m', 'n_clicks'),
         Input('btn-10m', 'n_clicks'),
         Input('btn-15m', 'n_clicks'),
         Input('btn-30m', 'n_clicks'),
         Input('btn-1h', 'n_clicks'),
         Input('btn-2h', 'n_clicks'),
         Input('btn-3h', 'n_clicks'),
         Input('btn-full', 'n_clicks')],
        prevent_initial_call=True
    )
    def update_oi_change_chart(selected_symbols, *button_clicks):
        if not selected_symbols:
            return html.Div("Please select a stock from the main dropdown.", className="text-warning")

        # Use the first selected symbol for this chart
        selected_stock = selected_symbols[0]

        ctx = callback_context
        time_range_label = "15m"  # Default
        time_range_minutes = 15

        if ctx.triggered:
            button_id = ctx.triggered[0]['prop_id'].split('.')[0]
            time_map = {
                'btn-5m': (5, '5m'),
                'btn-10m': (10, '10m'),
                'btn-15m': (15, '15m'),
                'btn-30m': (30, '30m'),
                'btn-1h': (60, '1h'),
                'btn-2h': (120, '2h'),
                'btn-3h': (180, '3h'),
                'btn-full': (999999, 'Full Day')
            }
            if button_id in time_map:
                time_range_minutes, time_range_label = time_map[button_id]

        return generate_oi_change_chart(selected_stock, time_range_minutes, time_range_label)

def generate_oi_change_chart(symbol, time_range_minutes, time_range_label="15m"):
    """Generate the OI Change vs Strike bar chart for a given symbol and time range."""
    from database import OptionChainData
    from datetime import datetime, timedelta

    db = SessionLocal()
    try:
        # Get stock
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if not stock:
            return html.Div(f"No data available for {symbol}.", className="text-warning")

        # Get current time and calculate time range
        now = datetime.now()
        current_date = now.date()
        current_time_str = now.strftime("%H:%M")

        # Calculate target time (X minutes ago)
        if time_range_minutes >= 999999:  # Full Day
            target_time = datetime.combine(current_date, datetime.min.time())
        else:
            target_time = now - timedelta(minutes=time_range_minutes)

        # Get latest option chain data (current)
        latest_data = db.query(OptionChainData).filter(
            OptionChainData.stock_id == stock.id,
            OptionChainData.date == current_date
        ).order_by(OptionChainData.id.desc()).all()

        if not latest_data:
            return html.Div(f"No option chain data available for {symbol} today.", className="text-warning")

        # Group latest data by strike
        latest_by_strike = {}
        current_expiry = None
        for entry in latest_data:
            if current_expiry is None:
                current_expiry = entry.expiry_date
            if entry.expiry_date == current_expiry:  # Only current expiry
                latest_by_strike[entry.strike_price] = entry

        # Get historical data from target time
        # Find the closest timestamp to target_time
        past_data = db.query(OptionChainData).filter(
            OptionChainData.stock_id == stock.id,
            OptionChainData.date == current_date,
            OptionChainData.expiry_date == current_expiry
        ).all()

        # Group by timestamp
        data_by_time = {}
        for entry in past_data:
            ts = entry.timestamp
            if ts not in data_by_time:
                data_by_time[ts] = {}
            data_by_time[ts][entry.strike_price] = entry

        # Find closest past timestamp
        target_time_str = target_time.strftime("%H:%M")
        available_times = sorted(data_by_time.keys())

        # Find the closest timestamp <= target_time
        past_time_key = None
        for t in available_times:
            if t <= target_time_str:
                past_time_key = t
            else:
                break

        if not past_time_key or past_time_key not in data_by_time:
            # Fallback to first available time if target not found
            past_time_key = available_times[0] if available_times else None

        past_by_strike = data_by_time.get(past_time_key, {}) if past_time_key else {}

        # Calculate changes
        strikes = sorted(latest_by_strike.keys())
        call_oi_changes = []
        put_oi_changes = []

        for strike in strikes:
            current_entry = latest_by_strike.get(strike)
            past_entry = past_by_strike.get(strike)

            if current_entry and past_entry:
                call_change = current_entry.call_oi - past_entry.call_oi
                put_change = current_entry.put_oi - past_entry.put_oi
            elif current_entry:
                # No past data, use current change value from NSE
                call_change = current_entry.call_oi_change
                put_change = current_entry.put_oi_change
            else:
                call_change = 0
                put_change = 0

            call_oi_changes.append(call_change)
            put_oi_changes.append(put_change)

        # Get current price (from latest OIData or fetch from NSE)
        from data_fetcher import fetch_oi_data
        live_data = fetch_oi_data(symbol)
        current_price = live_data.get('records', {}).get('underlyingValue', 0) if live_data else 0

        # Filter strikes to show only active range around current price
        # Show strikes within ±15% of current price (or at least ±1500 points for indices)
        if current_price > 0:
            price_range = max(current_price * 0.15, 1500)  # 15% or 1500 points, whichever is larger
            min_strike = current_price - price_range
            max_strike = current_price + price_range

            # Filter data
            filtered_data = []
            for i, strike in enumerate(strikes):
                if min_strike <= strike <= max_strike:
                    filtered_data.append((strike, call_oi_changes[i], put_oi_changes[i]))

            if filtered_data:
                strikes = [d[0] for d in filtered_data]
                call_oi_changes = [d[1] for d in filtered_data]
                put_oi_changes = [d[2] for d in filtered_data]

        # Create bar chart
        fig = go.Figure()

        # Add Put OI bars (green)
        fig.add_trace(go.Bar(
            x=strikes,
            y=put_oi_changes,
            name='Put OI Increase',
            marker_color='#00FF00',  # Bright green
            opacity=0.8
        ))

        # Add Call OI bars (red)
        fig.add_trace(go.Bar(
            x=strikes,
            y=call_oi_changes,
            name='Call OI Increase',
            marker_color='#FF3333',  # Brighter red
            opacity=0.8
        ))

        # Add vertical line for current price
        fig.add_vline(x=current_price, line_dash="dash", line_color="cyan", line_width=2,
                      annotation_text=f"{symbol} {current_price}", annotation_position="top",
                      annotation_font_size=10)

        fig.update_layout(
            title=dict(text=f"OI Change ({time_range_label}) on {datetime.now().strftime('%d %b')} - {symbol} @ {current_price}", font=dict(size=14)),
            template="plotly_dark",
            xaxis_title=dict(text="Strike Price", font=dict(size=11)),
            yaxis_title=dict(text="Change in OI", font=dict(size=11)),
            barmode='relative',
            height=500,
            hovermode='x unified',
            legend=dict(x=0, y=1, orientation="h", font=dict(size=10)),
            font=dict(size=10)
        )

        # Calculate totals
        total_call_increase = sum(c for c in call_oi_changes if c > 0)
        total_put_increase = sum(p for p in put_oi_changes if p > 0)
        # Net change across all strikes (could be negative)
        net_call_change = sum(call_oi_changes)
        net_put_change = sum(put_oi_changes)
        # Put vs Call net difference (positive means puts increased more)
        put_vs_call_net = net_put_change - net_call_change

        summary = dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        html.H6("Call OI Increase", className="text-danger mb-1"),
                        html.H4(f"+{total_call_increase:,.0f}", className="text-white mb-0")
                    ], className="p-2")
                ], className="mb-2")
            ], width=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        html.H6("Put OI Increase", className="text-success mb-1"),
                        html.H4(f"+{total_put_increase:,.0f}", className="text-white mb-0")
                    ], className="p-2")
                ], className="mb-2")
            ], width=6),
        ], className="mb-4")
        # Additional summary row showing net changes and put vs call
        net_summary = dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        html.H6("Net Call Change", className="text-danger mb-1"),
                        html.H4(f"{net_call_change:,.0f}", className="text-white mb-0")
                    ], className="p-2")
                ], className="mb-2")
            ], width=4),
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        html.H6("Net Put Change", className="text-success mb-1"),
                        html.H4(f"{net_put_change:,.0f}", className="text-white mb-0")
                    ], className="p-2")
                ], className="mb-2")
            ], width=4),
            dbc.Col([
                dbc.Card([
                    dbc.CardBody([
                        html.H6("Put vs Call (Net)", className="text-info mb-1"),
                        html.H4(("+" if put_vs_call_net >=0 else "") + f"{put_vs_call_net:,.0f}", className="text-white mb-0")
                    ], className="p-2")
                ], className="mb-2")
            ], width=4),
        ], className="mb-4")

        return html.Div([
            summary,
            net_summary,
            dcc.Graph(figure=fig, config={'displayModeBar': True})
        ])

    except Exception as e:
        logger.exception(f"Error generating OI change chart for {symbol}: {e}")
        return html.Div(f"Error loading data: {e}", className="text-danger")
    finally:
        db.close()

def render_oi_change_time_series_chart(selected_symbols):
    """Render the OI Change vs Price chart tab with dropdown and toggle."""
    if not selected_symbols:
        return dbc.Card(dbc.CardBody("Please select a stock to view the OI vs Price Chart."))

    symbol = selected_symbols[0]

    db = SessionLocal()
    try:
        # Layout wrapped in Card
        layout = dbc.Card([
            dbc.CardHeader("OI vs Price Chart"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.H4(f"Displaying OI vs Price for: {symbol}", className="text-info"),
                        html.Label("Y2 Axis Represents:", className="text-white mb-2"),
                        dbc.RadioItems(
                            options=[
                                {'label': 'Change in OI', 'value': 'change'},
                                {'label': 'Total OI', 'value': 'total'},
                            ],
                            value='change',
                            id="oi-time-series-toggle",
                            inline=True,
                            className="text-white"
                        ),
                    ], width=12, md=8),
                ]),
                dbc.Row([
                    dbc.Col(html.Div(id="oi-change-time-series-chart-container"), width=12)
                ])
            ])
        ])
        return layout
    finally:
        db.close()

def init_oi_change_time_series_callback(dash_app):
    @dash_app.callback(
        Output('oi-change-time-series-chart-container', 'children'),
        [Input('stock-dropdown', 'value'),
         Input('oi-time-series-toggle', 'value')]
    )
    def update_oi_change_time_series_chart(selected_symbols, y2_axis_reps):
        if not selected_symbols:
            return html.Div("Please select a stock.", className="text-warning")

        selected_stock = selected_symbols[0]
        return generate_oi_change_time_series_chart(selected_stock, y2_axis_reps)

def generate_oi_change_time_series_chart(symbol, y2_axis_reps):
    """Generate the OI Change vs Price time series chart."""
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if not stock:
            return html.Div("No data available for " + symbol, className="text-warning")

        today = datetime.now().date()
        records = db.query(OIData).filter(
            OIData.stock_id == stock.id,
            OIData.date == today
        ).order_by(OIData.id).all()

        if not records:
            return html.Div("No records found for " + symbol + " today.", className="text-warning")

        df = pd.DataFrame([r.__dict__ for r in records])

        # Calculate change in OI since start of day
        df['call_oi_change_sod'] = df['call_oi'] - df['call_oi'].iloc[0]
        df['put_oi_change_sod'] = df['put_oi'] - df['put_oi'].iloc[0]

        fig = go.Figure()

        fig.add_trace(go.Scatter(x=df['timestamp'], y=df['ltp'], name="Price", line=dict(color='cyan', width=2), yaxis="y1"))

        if y2_axis_reps == 'change':
            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['call_oi_change_sod'], name="CE OI Change", line=dict(color='red', width=2), yaxis="y2"))
            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['put_oi_change_sod'], name="PE OI Change", line=dict(color='green', width=2), yaxis="y2"))
        else:
            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['call_oi'], name="Total CE OI", line=dict(color='red', width=2), yaxis="y2"))
            fig.add_trace(go.Scatter(x=df['timestamp'], y=df['put_oi'], name="Total PE OI", line=dict(color='green', width=2), yaxis="y2"))

        title_str = symbol + " - Price vs OI (" + y2_axis_reps.capitalize() + ")"
        fig.update_layout(
            title=title_str,
            template="plotly_dark",
            yaxis=dict(title="Price"),
            yaxis2=dict(title="Open Interest", overlaying='y', side='right'),
            legend=dict(x=0.01, y=0.99)
        )

        return dcc.Graph(figure=fig)
    finally:
        db.close()
