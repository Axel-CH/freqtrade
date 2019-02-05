#!/usr/bin/env python3

import asyncio
import base64
import io
import logging
import pprint  # Temp, to remove
import re
from os import listdir
from os.path import isfile, join
from pathlib import Path

import ccxt
import dash
import dash_core_components as dcc
import dash_html_components as html
import pandas as pd
import plotly.graph_objs as go
from dash.dependencies import Input, Output, State

from freqtrade.data.converter import parse_ticker_dataframe
from freqtrade.arguments import Arguments
# from freqtrade.configuration import Configuration
# from freqtrade.exchange import Exchange
# from freqtrade.optimize.backtesting import Backtesting
# from freqtrade.strategy.interface import IStrategy
from plot_dataframe import (extract_trades_of_period, generate_dataframe,
                            generate_graph, get_tickers_data, get_trading_env,
                            load_trades, plot_parse_args)

logger = logging.getLogger(__name__)


# Unsafe solution to get strategy Class name, to replace
def get_strategies():
    path_folder = "user_data/strategies"
    files_in_folder = [f for f in listdir(path_folder) if isfile(join(path_folder, f))]
    strategies_files = [n for n in files_in_folder if 'strategy' in n]

    strategies_names = []
    for sf in strategies_files:
        sf = sf.replace(".py", "").replace("_", " ").title().replace(" ", "")
        strategies_names.append(sf)

    return strategies_names


def get_pairs_from_files_names(filenames):
    pairs = []
    for filename in filenames:
        pair = re.search('(.*)-', filename).group(1).replace("_", "/")
        pairs.append(pair)
    return pairs


def parse_contents(contents, filename, date):
    content_type, content_string = contents.split(',')
    decoded = base64.b64decode(content_string)

    try:
        if 'json' in filename:
            # Assume that the user uploaded a JSON file
            timeframe = re.search('-(.*).json', filename).group(1)

            df = pd.read_json(
                io.StringIO(decoded.decode('utf-8')))

            df = parse_ticker_dataframe(df.values, timeframe, False)
            df.set_index('date', inplace=True)

    except Exception as e:
        raise
        return html.Div([
            'There was an error processing this file.'
        ])
    plot_title = filename.replace("/", "_")
    file_obj = {'df': df, 'tf': timeframe, 'title': plot_title}
    return file_obj


def get_plot_component(df, figure, plot_id):
    return html.Div([
        dcc.Graph(
            id=plot_id,
            figure=figure
        ),
        html.Hr()
    ])


def get_plot_fig(df, title, is_populated=False):
    figure = {
                'data': [
                    go.Candlestick(
                        customdata=[{'pair': title}],
                        x=df.index,
                        open=df['open'],
                        high=df['high'],
                        low=df['low'],
                        close=df['close'],
                        name='ohlc data'),
                ],
                'layout': go.Layout(
                    title=title,
                    xaxis={'rangeslider': {'visible': False}},
                    yaxis={'title': 'Price'},
                    legend={'x': 0, 'y': 1},
                    hovermode='closest',
                    clickmode='event+select'
                )
            }
    return figure


# Events Callbacks
def get_callbacks(self):
    app = self.app

    # On Exchange Selection. (for pairs)
    @app.callback(
                    Output('dropdown-pairs', 'options'),
                    [Input('dropdown-exchange', 'value')],
                    [State('dropdown-pairs', 'value')])
    def update_output_pairs(exchange, pairs):
        logger.info("update_output_pairs")
        logger.info("exchange: %s ", exchange)

        current_exchange = getattr(ccxt, exchange)()
        Webui.exchange = current_exchange
        current_exchange.load_markets()
        ccxt.exchanges

        # Init Exchange
        pairs = current_exchange.symbols
        pairs_opts = [{'label': ex, 'value': ex} for ex in pairs]

        return pairs_opts

    # On Exchange Selection. (for time frames)
    @app.callback(
                    Output('radio-timeframes', 'options'),
                    [Input('dropdown-exchange', 'value')],
                    [State('dropdown-pairs', 'value')])
    def update_output_timeframes(exchange, pairs):
        print("On Exchange Selection. (for time frames) Callback")
        timeframes = self.get_exchange_tf()
        tf_opts = [{'label': tf, 'value': tf} for tf in timeframes]
        return tf_opts

    # ON Select Ticker
    @app.callback(
        dash.dependencies.Output('pairs-plot-details', 'children'),  # replace with plots
        [
            dash.dependencies.Input('dropdown-pairs', 'value'),
            dash.dependencies.Input('radio-timeframes', 'value')]
    )
    def update_plots(pairs, timeframe):
        if pairs is not None:
            exchange = self.exchange
            # Get ticker datas
            if exchange.has['fetchOHLCV']:
                plots = []
                plot_counter = 0
                for pair in pairs:
                    plot_counter += 1
                    plot_id = f'plot_id_{plot_counter}'
                    tickers = exchange.fetch_ohlcv(pair, timeframe)
                    df = parse_ticker_dataframe(tickers, timeframe)
                    figure = get_plot_fig(df, pair)
                    figure['layout']['height'] = 800
                    plots.append(get_plot_component(df, figure, plot_id))
                    self.plot_ids.update({pair: plot_id})

                print("plot IDs : ")
                print(self.plot_ids)
            return html.Div(plots)

    # ON Data Upload
    @app.callback(
                    Output('output-data-upload', 'children'),
                    [Input('upload-data', 'contents')],
                    [State('upload-data', 'filename'),
                        State('upload-data', 'last_modified')])
    def update_upload(list_of_contents, list_of_names, list_of_dates):
        print("update upload")
        if list_of_contents is not None:
            plots = []
            plot_counter = 0
            for c, n, d in zip(list_of_contents, list_of_names, list_of_dates):
                plot_counter += 1
                plot_id = f'plot_id_{plot_counter}'
                file_obj = parse_contents(c, n, d)

                figure = get_plot_fig(file_obj['df'], file_obj['title'])
                plot_component = get_plot_component(file_obj['df'], figure, plot_id)
                plots.append(plot_component)
                self.tickers.update({file_obj['title']: file_obj['df']})
                self.plot_ids.update({file_obj['title']: plot_id})     

            return plots

    # ON Click run-backtest-button
    @app.callback(
        Output('backtest-details', 'children'),
        [Input('run-backtest-button', 'n_clicks')],
        [
            State('dropdown-exchange', 'value'),
            State('dropdown-pairs', 'value'),
            State('dropdown-strategy', 'value'),
            State('radio-timeframes', 'value'),
            State('upload-data', 'filename')]
    )
    def update_run_backtest(n_clicks, exchange, pairs, strategy_name, timeframe, upld_fl_names):
        print("Run backtest")
        print(f'Pairs {pairs}')
        print("upld_fl_names")
        print(upld_fl_names)
        if strategy_name is not None:
            is_local_upload = False

            if upld_fl_names is not None:
                if len(upld_fl_names) > 0:  # if local data
                    is_local_upload = True
                    print("local files")
                    pairs = get_pairs_from_files_names(upld_fl_names)
                else:
                    return "Please select a ticker file"

            output_plots = self.run_backtest(
                                            exchange,
                                            strategy_name,
                                            pairs,
                                            timeframe,
                                            is_local_upload)
            return output_plots
        else:
            return "Please select a Strategy"

    # On Click export-dfs-button
    @app.callback(
                Output('export-details', 'children'),
                [Input('export-dfs-button', 'n_clicks')],
                [State('dropdown-pairs', 'value'),
                    State('radio-timeframes', 'value'),
                    State('dropdown-strategy', 'value')])
    def update_export_dfs(n_clicks, pairs, timeframe, strategy_name):
        self.selected_dataframes = {}
        # Pair From To
        if self.dataframes is not None:
            for pair, data in self.dataframes.items():
                pair_name = pair.replace("/", "_")

                # Open or create dataframes folder
                Path("user_data/dataframes").mkdir(parents=True, exist_ok=True)

                # If Specific interval selection, get interval
                if pair in self.selections:
                    data.set_index('date', inplace=True)
                    from_date = pd.Timestamp(self.selections[pair][0])
                    to_date = pd.Timestamp(self.selections[pair][1])
                    from_date_ts = from_date.value
                    to_date_ts = to_date.value
                    filename = f'''{pair_name}-
                                {timeframe}-
                                {strategy_name}-
                                {from_date_ts}_{to_date_ts}.json'''
                    file_path = Path("user_data/dataframes").joinpath(filename)

                    df_to_export = data[from_date:to_date]
                else:
                    filename = f'{pair_name}-{timeframe}-{strategy_name}.json'
                    file_path = Path("user_data/dataframes").joinpath(filename)
                    df_to_export = data

                # Dump data to file
                df_to_export.to_json(path_or_buf=file_path)


# CallBack Plots Interval Selection
def get_dynamic_callbacks(self):
    app = self.app

    def plot_cb_declaration():
        print("plot_cb_declaration")
        for i in range(1, 3):
            @app.callback(Output(f'plot_placeholder_{i}', 'children'),
                        [Input(f'plot_id_{i}', 'selectedData')],
                        [State(f'plot_id_{i}', 'id')])
            def graph_update(layoutData, plot_id):
                if layoutData is not None:
                    print("layoutData")
                    pp = pprint.PrettyPrinter(indent=2)
                    pp.pprint(layoutData)
                    print("plot_id")
                    print(plot_id)

                    current_pair = get_pair(self, plot_id)

                    self.selections.update({current_pair: layoutData['range']['x']})

                    return f'graph_update CB for plot: {i}'

    plot_cb_declaration()


# tools
def get_pair(self, plot_id):
    for pair, cplot_id in self.plot_ids.items():
        if plot_id == cplot_id:
            return pair


# WEB UI PART ( using Dash from plotly)
def get_ui(self):
    # To replace with custom css
    external_stylesheets = ['https://stackpath.bootstrapcdn.com/bootstrap/4.2.1/css/bootstrap.min.css']
    app = dash.Dash(__name__, external_stylesheets=external_stylesheets)

    app.layout = html.Div([
        html.Div([
            html.H3("Freqtrade Plot Input"),
            html.H5("From Live Data"),
            dcc.Dropdown(
                options=self.exchanges_opts,
                value='binance',
                id='dropdown-exchange',
                className='mt-2'
            ),
            dcc.Dropdown(
                placeholder='Enter a Pair, ex: XRP/BTC',
                options=[],
                id='dropdown-pairs',
                multi=True,
                className='mt-2'
            ),
            html.Span("Time frame"),
            dcc.RadioItems(
                options=self.timeframes_opts,
                value='1m',
                id='radio-timeframes',
                className='mt-2'
            ),
            html.Div(className='row'),
            html.H5("From OHLC Exchange Data"),
            dcc.Upload(
                id='upload-data',
                children=html.Div([
                    'Drag and Drop or ',
                    html.A('Select Files')
                ]),
                style={
                    'width': '100%',
                    'height': '60px',
                    'lineHeight': '60px',
                    'borderWidth': '1px',
                    'borderStyle': 'dashed',
                    'borderRadius': '5px',
                    'textAlign': 'center',
                    'margin': '10px'
                },
                # Allow multiple files to be uploaded
                multiple=True
            ),
            html.Div(id='output-data-upload'),
            html.Hr(),
            dcc.Dropdown(
                options=self.strategies_opts,
                id='dropdown-strategy',
                className=''
            ),
            html.Div([
                html.Button('Run Backtest', id='run-backtest-button', className='btn btn-primary'),
                html.Button('Export Dataframes', id='export-dfs-button', className='btn btn-dark float-right')
            ], className='mt-2'),
        ], className='p-3'),
        html.Div([
            html.Div(id='pairs-plot-details', children='Select Pair informations'),
            html.Div(id='output-plots'),
            html.Div(id='backtest-details', children='Select Pair informations', className='mt-2'),
            html.Div(id='export-details', children='', className='mt-2'),

            #  Refactor this whole part with a loop
            html.Div([
                dcc.Graph(id='plot_id_1', className='d-none'),
                dcc.Graph(id='plot_id_2', className='d-none'),
                dcc.Graph(id='plot_id_3', className='d-none'),
                dcc.Graph(id='plot_id_4', className='d-none'),
                dcc.Graph(id='plot_id_5', className='d-none'),
            ], id='plots_placeholders', className='mt-2'),
            html.Div([
                html.Div(id='plot_placeholder_1'),
                html.Div(id='plot_placeholder_2'),
                html.Div(id='plot_placeholder_3'),
                html.Div(id='plot_placeholder_4'),
                html.Div(id='plot_placeholder_5'),
            ], id='plots_callbacks_output_placeholders', className='mt-2'),
            html.Div([
                html.Div(id='bt_placeholder_plot_id_1'),
                html.Div(id='bt_placeholder_plot_id_2'),
                html.Div(id='bt_placeholder_plot_id_3'),
                html.Div(id='bt_placeholder_plot_id_4'),
                html.Div(id='bt_placeholder_plot_id_5'),
            ], id='backtests_callbacks_output_placeholders', className='mt-2'),
        ], className='')
    ], className='')

    return app


class Webui(object):
    """
    This Class contain web UI variables and methods
    """
    def __init__(self) -> None:
        self.exchange = None
        self.pairs = []
        self.timeframes = []
        self.strategy = None
        self.tickers = {}
        self.dataframes = None
        self.selections = {}
        self.plot_ids = {}
        self.prepare_form_datas()

    # Fields Data Preparation
    def prepare_form_datas(self):
        print("prepare_form_datas")
        exchanges = ccxt.exchanges
        self.exchanges_opts = [{'label': ex, 'value': ex} for ex in exchanges]

        strategies = get_strategies()
        self.strategies_opts = [{'label': s, 'value': s} for s in strategies]

        pairs = []
        self.pairs_opts = [{'label': ex, 'value': ex} for ex in pairs]

        timeframes = self.get_exchange_tf()  # '1m', '5m', '15m', '1h', '1d'
        self.timeframes_opts = [{'label': tf, 'value': tf} for tf in timeframes]

    def get_exchange_tf(self):
        if self.exchange is not None:
            try:
                timeframes = self.exchange.timeframes
            except IndexError:
                timeframes = []
            return timeframes
        else:
            return []

    def load_ui(self):
        self.app = get_ui(self)  # TO DO : Refactor

    def run_backtest(self, exchange, strategy_name, pairs, ticker_interval, is_local_upload):
        print("run_backtest")
        asyncio.set_event_loop(asyncio.new_event_loop())
        pairs_str = ','.join(pairs)

        # To Do: remove unused parameters
        args = [
            '--config', 'config.json',
            '--strategy', strategy_name,
            '--datadir', 'freqtrade/tests/testdata',
            '--pairs', pairs_str,
            '--ticker-interval', ticker_interval,
            '--indicators1', 'ema50',
            '--indicators2', 'macd,macdsignal',
            '--live',
            '--timerange', '-2000',
            '--enable-position-stacking',
            '--disable-max-market-positions'
        ]
        args = plot_parse_args(args)
        pp = pprint.PrettyPrinter(indent=4)
        pp.pprint(args)

        strategy, exchange, pairs = get_trading_env(args)
        timerange = Arguments.parse_timerange(args.timerange)

        if is_local_upload:
            tickers = self.tickers
        else:
            tickers = get_tickers_data(strategy, exchange, pairs, args)

        dataframes = {}
        plots = []
        pair_counter = 0
        for pair, data in tickers.items():
            pair_counter += 1
            logger.info("analyse pair %s", pair)
            tickers = {}
            tickers[pair] = data
            dataframe = generate_dataframe(strategy, tickers, pair)
            dataframes[pair] = dataframe.reset_index()

            trades = load_trades(args, pair, timerange)  # ungly, to refactor
            if not trades.empty:
                trades = extract_trades_of_period(dataframe, trades)

            figure = generate_graph(
                pair=pair,
                trades=trades,
                data=dataframe.reset_index(),
                indicators1=args.indicators1,
                indicators2=args.indicators2
            )

            #  Get plot ID corresponding to this pair
            plot_id = self.plot_ids[pair]
            print(f'plot id for {pair}: {plot_id} ')
            plots.append(get_plot_component(dataframe, figure, plot_id))

        # Save dataframes in memory
        self.dataframes = dataframes
        logger.info('End of ploting process %s plots generated', pair_counter)

        return html.Div(plots)


if __name__ == '__main__':
    Webui = Webui()
    Webui.load_ui()
    get_callbacks(Webui)
    get_dynamic_callbacks(Webui)
    app = Webui.app
    app.run_server(debug=True)
