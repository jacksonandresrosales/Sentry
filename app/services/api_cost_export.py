"""Excel del consumo de API, con importes y tarifas registrados por solicitud."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import tempfile

import xlsxwriter

from app.services.api_costs import (
    ASSEMBLYAI_KEYTERM_RATE, DEFAULT_PRICES, DEEPGRAM_KEYTERM_RATE, load_cost_report,
)


SOURCES = {
    'deepgram': 'https://deepgram.com/pricing',
    'assemblyai': 'https://www.assemblyai.com/pricing',
    'gemini': 'https://ai.google.dev/gemini-api/docs/pricing',
    'deepseek': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'groq': 'https://console.groq.com/docs/models',
}
SERVICE_NAMES = {'transcription': 'Transcripción', 'analysis': 'Análisis'}
PROVIDER_NAMES = {'deepgram': 'Deepgram', 'assemblyai': 'AssemblyAI', 'gemini': 'Gemini', 'deepseek': 'DeepSeek',
                  'groq': 'Groq',
                  'openai': 'OpenAI'}


def _label(value: str, labels: dict[str, str]) -> str:
    return labels.get(value, value or 'Todos')


def _tariff_name(event: dict) -> str:
    if event['cost_usd'] is None:
        return 'Sin estimación'
    key = event['service'], event['provider'], event['model']
    if key == ('transcription', 'deepgram', 'nova-3'):
        base = DEFAULT_PRICES[key][1]
        return 'Nova-3 + Keyterm' if abs((event['input_rate'] or 0) - base - DEEPGRAM_KEYTERM_RATE) < 1e-8 else 'Nova-3 base'
    if key[:2] == ('transcription', 'assemblyai') and key in DEFAULT_PRICES:
        base = DEFAULT_PRICES[key][1]
        keyterm = abs((event['input_rate'] or 0) - base - ASSEMBLYAI_KEYTERM_RATE) < 1e-8
        return 'Diarización + Keyterms' if keyterm else 'Diarización'
    if key == ('analysis', 'deepseek', 'deepseek-flash'):
        peak = DEFAULT_PRICES[key][1]
        return 'Pico' if abs((event['input_rate'] or 0) - peak) < 1e-8 else 'Fuera de pico'
    if key == ('analysis', 'gemini', 'gemini-3.5-flash'):
        return 'Estándar'
    if key == ('analysis', 'groq', 'openai/gpt-oss-120b'):
        return 'Referencia de pago'
    return 'Histórica'


def _number(sheet, row: int, column: int, value, cell_format=None):
    if value is not None:
        sheet.write_number(row, column, value, cell_format)


def export_api_costs_excel(database, output: Path, start: date, end: date,
                           service: str = '', provider: str = '') -> Path:
    """Exporta todas las solicitudes del filtro con subtotales y tarifas aplicadas."""
    report = load_cost_report(database, start, end, service, provider)
    output = Path(output).resolve()
    if output.suffix.casefold() != '.xlsx':
        output = output.with_suffix('.xlsx')
    if len(report['events']) >= 1_048_575:
        raise ValueError('Hay demasiadas solicitudes para una sola hoja de Excel.')
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.sentry-costes-', suffix='.xlsx', dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with xlsxwriter.Workbook(str(temporary), {'strings_to_formulas': False,
                                                  'strings_to_urls': False,
                                                  'constant_memory': True}) as book:
            book.set_properties({'title': 'Costes de API de Sentry', 'subject': 'Consumo y tarifas estimadas'})
            body = book.add_format({'font_name': 'Aptos', 'font_size': 11, 'font_color': '#202220',
                                    'valign': 'vcenter'})
            title = book.add_format({'font_name': 'Aptos', 'font_size': 16, 'bold': True,
                                     'font_color': '#1F7830'})
            header = book.add_format({'font_name': 'Aptos', 'bold': True, 'font_color': '#FFFFFF',
                                      'bg_color': '#202220', 'text_wrap': True, 'valign': 'vcenter',
                                      'align': 'center', 'right': 1, 'right_color': '#FFFFFF'})
            label = book.add_format({'font_name': 'Aptos', 'bold': True, 'font_color': '#202220',
                                     'bg_color': '#EEF7EF'})
            currency = book.add_format({'font_name': 'Aptos', 'num_format': '$#,##0.000000;[Red]($#,##0.000000)'})
            total_currency = book.add_format({'font_name': 'Aptos', 'bold': True,
                                              'bg_color': '#EEF7EF',
                                              'num_format': '$#,##0.000000;[Red]($#,##0.000000)'})
            total_label = book.add_format({'font_name': 'Aptos', 'bold': True,
                                           'bg_color': '#EEF7EF'})
            number = book.add_format({'font_name': 'Aptos', 'num_format': '#,##0.00'})
            integer = book.add_format({'font_name': 'Aptos', 'num_format': '#,##0'})
            percentage = book.add_format({'font_name': 'Aptos', 'num_format': '0.0%'})
            day_format = book.add_format({'font_name': 'Aptos', 'num_format': 'dd/mm/yyyy'})
            timestamp = book.add_format({'font_name': 'Aptos', 'num_format': 'dd/mm/yyyy hh:mm:ss'})
            note = book.add_format({'font_name': 'Aptos', 'font_color': '#656263', 'font_size': 10})
            wrapped = book.add_format({'font_name': 'Aptos', 'text_wrap': True, 'valign': 'vcenter'})
            warning = book.add_format({'font_color': '#9A5A00', 'bg_color': '#FFF4D6'})
            stripe = book.add_format({'bg_color': '#F5F8F5'})
            link = book.add_format({'font_name': 'Aptos', 'font_color': '#1F7830', 'underline': True})
            for cell_format in (title, label, currency, total_currency, total_label, number, integer,
                                percentage, day_format, timestamp, note, link):
                cell_format.set_valign('vcenter')

            period = f'{start:%d/%m/%Y} al {end - timedelta(days=1):%d/%m/%Y}'

            summary = book.add_worksheet('Resumen')
            summary.write_string(0, 0, 'Costes de API · Sentry', title)
            summary.set_row(0, 30)
            summary.write_string(2, 0, 'Desde', label)
            summary.write_datetime(2, 1, start, day_format)
            summary.write_string(3, 0, 'Hasta', label)
            summary.write_datetime(3, 1, end - timedelta(days=1), day_format)
            summary.write_string(4, 0, 'Servicio', label)
            summary.write_string(4, 1, _label(service, SERVICE_NAMES))
            summary.write_string(5, 0, 'Proveedor', label)
            summary.write_string(5, 1, _label(provider, PROVIDER_NAMES))
            for row, caption, value, fmt in (
                (7, 'Solicitudes registradas', report['requests'], integer),
                (8, 'Sin estimación', report['unpriced'], integer),
                (9, 'Transcripción', report['transcription_usd'], currency),
                (10, 'Análisis', report['analysis_usd'], currency),
                (11, 'TOTAL ESTIMADO', report['total_usd'], total_currency),
            ):
                summary.write_string(row, 0, caption, total_label if row == 11 else label)
                summary.write_number(row, 1, value, fmt)
            summary.write_string(13, 0, 'Importes en USD estimados; la factura del proveedor puede diferir.', note)
            summary.write_string(14, 0, 'Las solicitudes sin estimación conservan su consumo, pero no se suman al coste.', note)
            summary.write_string(15, 0, 'Incluye todas las solicitudes del filtro. El audio se expresa en minutos y los tokens en unidades.', note)
            model_headers = ('Servicio', 'Proveedor', 'Modelo', 'Solicitudes', 'Coste USD', 'Sin estimación',
                             '% del coste', 'Audio (min)', 'Tokens entrada', 'Tokens salida')
            summary.write_row(16, 0, model_headers, header)
            summary.set_row(16, 36)
            for row, item in enumerate(report['by_model'], 17):
                summary.write_string(row, 0, _label(item['service'], SERVICE_NAMES))
                summary.write_string(row, 1, _label(item['provider'], PROVIDER_NAMES))
                summary.write_string(row, 2, item['model'])
                summary.write_number(row, 3, item['requests'], integer)
                summary.write_number(row, 4, item['cost_usd'], currency)
                summary.write_number(row, 5, item['unpriced'], integer)
                summary.write_number(row, 6, item['share'], percentage)
                _number(summary, row, 7, item['audio_seconds'] / 60 if item['service'] == 'transcription' else None, number)
                summary.write_number(row, 8, item['input_tokens'], integer)
                summary.write_number(row, 9, item['output_tokens'], integer)
            model_total_row = 17 + len(report['by_model'])
            summary.write_string(model_total_row, 2, 'TOTAL', total_label)
            summary.write_number(model_total_row, 3, report['requests'])
            if report['by_model']:
                summary.write_formula(model_total_row, 4, f'=SUM(E18:E{model_total_row})',
                                      total_currency, report['total_usd'])
            else:
                summary.write_number(model_total_row, 4, 0, total_currency)
            summary.write_number(model_total_row, 5, report['unpriced'])
            summary.write_number(model_total_row, 6, 1 if report['total_usd'] else 0, percentage)
            summary.write_number(model_total_row, 7, report['audio_seconds'] / 60, number)
            summary.write_number(model_total_row, 8, report['input_tokens'], integer)
            summary.write_number(model_total_row, 9, report['output_tokens'], integer)
            summary.set_column(0, 0, 27, body)
            summary.set_column(1, 1, 22, body)
            summary.set_column(2, 2, 29, body)
            summary.set_column(3, 3, 15, integer)
            summary.set_column(4, 4, 19, currency)
            summary.set_column(5, 5, 17, integer)
            summary.set_column(6, 6, 13, percentage)
            summary.set_column(7, 7, 17, number)
            summary.set_column(8, 9, 17, integer)
            if report['by_model']:
                summary.autofilter(16, 0, model_total_row - 1, 9)
                summary.conditional_format(17, 5, model_total_row - 1, 5,
                                           {'type': 'cell', 'criteria': '>', 'value': 0, 'format': warning})
            summary.conditional_format('B9', {'type': 'cell', 'criteria': '>', 'value': 0, 'format': warning})

            daily = book.add_worksheet('Diario')
            daily_headers = ('Fecha local', 'Transcripción USD', 'Análisis USD', 'Total USD',
                             'Solicitudes transcripción', 'Solicitudes análisis', 'Sin estimación')
            daily.write_row(0, 0, daily_headers, header)
            daily.set_row(0, 36)
            for row, item in enumerate(report['daily'], 1):
                daily.write_datetime(row, 0, date.fromisoformat(item['date']), day_format)
                for column, key in enumerate(('transcription_usd', 'analysis_usd', 'total_usd'), 1):
                    daily.write_number(row, column, item[key], currency)
                for column, key in enumerate(('transcription_requests', 'analysis_requests', 'unpriced'), 4):
                    daily.write_number(row, column, item[key], integer)
            total_row = len(report['daily']) + 1
            daily.write_string(total_row, 0, 'TOTAL', total_label)
            for column, key in enumerate(('transcription_usd', 'analysis_usd', 'total_usd'), 1):
                if report['daily']:
                    daily.write_formula(total_row, column, f'=SUM({xlsxwriter.utility.xl_col_to_name(column)}2:'
                                        f'{xlsxwriter.utility.xl_col_to_name(column)}{total_row})',
                                        total_currency, report[key])
                else:
                    daily.write_number(total_row, column, 0, total_currency)
            daily.write_number(total_row, 4, sum(item['transcription_requests'] for item in report['daily']))
            daily.write_number(total_row, 5, sum(item['analysis_requests'] for item in report['daily']))
            daily.write_number(total_row, 6, report['unpriced'])
            daily.set_column(0, 0, 18, day_format)
            daily.set_column(1, 3, 23, currency)
            daily.set_column(4, 6, 23, integer)
            daily.freeze_panes(1, 0)
            if report['daily']:
                daily.autofilter(0, 0, len(report['daily']), len(daily_headers) - 1)
                trend = book.add_chart({'type': 'line'})
                # constant_memory no conserva las celdas para la caché inicial del gráfico.
                days = [date.fromisoformat(item['date']) for item in report['daily']]
                categories = [(day - date(1899, 12, 31)).days + (day >= date(1900, 3, 1)) for day in days]
                for column, caption, key, color in (
                    (1, 'Transcripción', 'transcription_usd', '#27953C'),
                    (2, 'Análisis', 'analysis_usd', '#527A9F'),
                ):
                    trend.add_series({'name': caption, 'categories': ['Diario', 1, 0, len(report['daily']), 0],
                                      'values': ['Diario', 1, column, len(report['daily']), column],
                                      'categories_data': categories,
                                      'values_data': [item[key] for item in report['daily']],
                                      'marker': {'type': 'circle' if len(days) == 1 else 'none', 'size': 4},
                                      'line': {'color': color, 'width': 2}, 'smooth': False})
                trend.set_title({'name': 'Evolución diaria (USD)', 'name_font': {'name': 'Aptos', 'size': 12}})
                trend.set_x_axis({'date_axis': True, 'num_format': 'dd/mm',
                                  'num_font': {'name': 'Aptos', 'size': 10}})
                trend.set_y_axis({'min': 0, 'num_format': '$0.0000',
                                  'num_font': {'name': 'Aptos', 'size': 10},
                                  'major_gridlines': {'visible': True, 'line': {'color': '#E5E9E5'}}})
                trend.set_legend({'position': 'bottom', 'font': {'name': 'Aptos', 'size': 10}})
                trend.set_chartarea({'border': {'none': True}})
                trend.set_plotarea({'border': {'none': True}})
                summary.insert_chart('D3', trend, {'x_offset': 8, 'width': 650, 'height': 235})

            requests = book.add_worksheet('Solicitudes')
            request_headers = ('Fecha local', 'Servicio', 'Proveedor', 'Modelo', 'Coste USD', 'Estado',
                               'Grabación', 'Audio (s)', 'Tokens entrada', 'Tokens en caché', 'Tokens salida',
                               'Tipo de tarifa', 'Unidad tarifa', 'Tarifa entrada USD', 'Tarifa caché USD',
                               'Tarifa salida USD', 'ID solicitud')
            requests.write_row(0, 0, request_headers, header)
            requests.set_row(0, 36)
            for row, event in enumerate(report['events'], 1):
                requests.write_datetime(row, 0, datetime.fromisoformat(event['local_at']), timestamp)
                for column, value in enumerate((_label(event['service'], SERVICE_NAMES),
                        _label(event['provider'], PROVIDER_NAMES), event['model']), 1):
                    requests.write_string(row, column, str(value))
                _number(requests, row, 4, event['cost_usd'], currency)
                requests.write_string(row, 5, 'Sin estimación' if event['cost_usd'] is None else 'Estimado')
                requests.write_string(row, 6, event['filename'] or 'Llamada eliminada')
                for column, key in ((7, 'audio_seconds'), (8, 'input_tokens'), (9, 'cached_input_tokens'),
                                    (10, 'output_tokens')):
                    _number(requests, row, column, event[key], number if column == 7 else integer)
                requests.write_string(row, 11, _tariff_name(event))
                requests.write_string(row, 12, 'USD/min' if event['price_unit'] == 'minute' else
                                      'USD/1M tokens' if event['price_unit'] == 'million_tokens' else '')
                for column, key in ((13, 'input_rate'), (14, 'cached_input_rate'), (15, 'output_rate')):
                    if event['price_unit'] != 'minute' or column == 13:
                        _number(requests, row, column, event[key], currency)
                requests.write_string(row, 16, event['request_id'] or '')
            total_row = len(report['events']) + 1
            requests.write_string(total_row, 3, 'TOTAL ESTIMADO', total_label)
            if report['events']:
                requests.write_formula(total_row, 4, f'=SUM(E2:E{total_row})',
                                       total_currency, report['total_usd'])
            else:
                requests.write_number(total_row, 4, 0, total_currency)
            requests.set_column(0, 0, 25, timestamp)
            requests.set_column(1, 2, 18, body)
            requests.set_column(3, 3, 30, body)
            requests.set_column(4, 4, 19, currency)
            requests.set_column(5, 5, 19, body)
            requests.set_column(6, 6, 38, body)
            requests.set_column(7, 7, 15, number)
            requests.set_column(8, 10, 18, integer)
            requests.set_column(11, 15, 22, body, {'level': 1, 'hidden': True})
            requests.set_column(16, 16, 38, body, {'level': 1, 'hidden': True})
            requests.set_column(17, 17, None, None, {'collapsed': True})
            requests.freeze_panes(1, 4)
            if report['events']:
                requests.autofilter(0, 0, len(report['events']), len(request_headers) - 1)
                requests.conditional_format(1, 4, total_row - 1, 5,
                                            {'type': 'formula', 'criteria': '$F2="Sin estimación"',
                                             'format': warning, 'stop_if_true': True})

            tariffs = book.add_worksheet('Tarifas')
            tariffs.write_string(0, 0, 'Referencias automáticas de pago por uso', title)
            tariffs.set_row(0, 30)
            tariffs.write_string(1, 0, 'Referencias incluidas en Sentry; pueden cambiar. '
                                 'AssemblyAI verificado el 16/10/2026.', note)
            tariffs.write_string(2, 0, 'Las tarifas históricas del periodo se detallan debajo; cada solicitud conserva su tarifa original.', note)
            tariff_headers = ('Proveedor', 'Modelo / concepto', 'Franja o condición', 'Unidad',
                              'Entrada / minuto USD', 'Caché USD', 'Salida USD', 'Fuente')
            tariffs.write_row(3, 0, tariff_headers, header)
            tariffs.set_row(3, 38)
            references = (
                ('deepgram', 'Nova-3', 'Audio pregrabado monolingüe', 'USD/min', .0043, None, None),
                ('deepgram', 'Keyterm Prompting', 'Adicional si se envían términos', 'USD/min',
                 DEEPGRAM_KEYTERM_RATE, None, None),
                ('assemblyai', 'Universal-2', 'Pregrabado con diarización estándar', 'USD/min',
                 DEFAULT_PRICES[('transcription', 'assemblyai', 'universal-2')][1], None, None),
                ('assemblyai', 'Universal-3.5 Pro', 'Pregrabado con diarización estándar', 'USD/min',
                 DEFAULT_PRICES[('transcription', 'assemblyai', 'universal-3-5-pro')][1], None, None),
                ('assemblyai', 'Keyterms Prompting', 'Universal-3.5 Pro; incluido en Universal-2', 'USD/min',
                 ASSEMBLYAI_KEYTERM_RATE, None, None),
                ('gemini', 'Gemini 3.5 Flash', 'Estándar, nivel de pago', 'USD/1M tokens', 1.50, .15, 9.00),
                ('deepseek', 'DeepSeek V4.1 Flash', 'Pico UTC, lunes a viernes 01–04 y 06–10',
                 'USD/1M tokens', .30, .006, 1.20),
                ('deepseek', 'DeepSeek V4.1 Flash', 'Fuera de pico', 'USD/1M tokens', .15, .003, .60),
                ('groq', 'GPT-OSS 120B', 'Referencia de pago; plan gratis sujeto a límites',
                 'USD/1M tokens', .15, .075, .60),
            )
            for row, (source, model, condition, unit, input_rate, cached_rate, output_rate) in enumerate(references, 4):
                for column, value in enumerate((PROVIDER_NAMES[source], model, condition, unit)):
                    tariffs.write_string(row, column, value, wrapped if column == 2 else body)
                tariffs.set_row(row, 34)
                for column, value in ((4, input_rate), (5, cached_rate), (6, output_rate)):
                    _number(tariffs, row, column, value, currency)
                tariffs.write_url(row, 7, SOURCES[source], link, string='Fuente oficial')
            applied_title_row = 4 + len(references) + 1
            tariffs.write_string(applied_title_row, 0, 'Tarifas efectivamente guardadas en este período', title)
            tariffs.set_row(applied_title_row, 30)
            applied_headers = ('Servicio', 'Proveedor', 'Modelo', 'Unidad', 'Entrada / minuto USD',
                               'Caché USD', 'Salida USD', 'Solicitudes', 'Coste USD', 'Sin estimación')
            tariffs.write_row(applied_title_row + 2, 0, applied_headers, header)
            tariffs.set_row(applied_title_row + 2, 38)
            applied = defaultdict(lambda: {'requests': 0, 'cost': 0.0, 'unpriced': 0})
            for event in report['events']:
                key = (event['service'], event['provider'], event['model'], event['price_unit'],
                       event['input_rate'], event['cached_input_rate'], event['output_rate'])
                applied[key]['requests'] += 1
                applied[key]['cost'] += event['cost_usd'] or 0
                applied[key]['unpriced'] += event['cost_usd'] is None
            for row, (key, values) in enumerate(sorted(applied.items(), key=lambda item: str(item[0])), applied_title_row + 3):
                service_id, provider_id, model, unit, input_rate, cached_rate, output_rate = key
                for column, value in enumerate((_label(service_id, SERVICE_NAMES),
                        _label(provider_id, PROVIDER_NAMES), model,
                        'USD/min' if unit == 'minute' else 'USD/1M tokens' if unit == 'million_tokens' else unit or '')):
                    tariffs.write_string(row, column, value)
                for column, value in ((4, input_rate), (5, cached_rate), (6, output_rate)):
                    if unit != 'minute' or column == 4:
                        _number(tariffs, row, column, value, currency)
                tariffs.write_number(row, 7, values['requests'])
                tariffs.write_number(row, 8, values['cost'], currency)
                tariffs.write_number(row, 9, values['unpriced'])
            tariff_total_row = applied_title_row + 3 + len(applied)
            tariffs.write_string(tariff_total_row, 6, 'TOTAL', total_label)
            tariffs.write_number(tariff_total_row, 7, report['requests'])
            if applied:
                tariffs.write_formula(tariff_total_row, 8, f'=SUM(I{applied_title_row + 4}:I{tariff_total_row})',
                                      total_currency, report['total_usd'])
            else:
                tariffs.write_number(tariff_total_row, 8, 0, total_currency)
            tariffs.write_number(tariff_total_row, 9, report['unpriced'])
            explanations = (
                'Audio: segundos / 60 × tarifa por minuto. Keyterms añade un cargo en Nova-3 y Universal-3.5 Pro; está incluido en Universal-2.',
                'Texto: (tokens de entrada sin caché × tarifa de entrada + tokens en caché × tarifa '
                'de caché + tokens de salida × tarifa de salida) / 1 000 000.',
                'DeepSeek: pico de lunes a viernes, 01:00–04:00 y 06:00–10:00 UTC. '
                'Fuera de pico se aplica la mitad de cada tarifa.',
                'Los precios son referencias del nivel de pago. Créditos, capas gratuitas, planes, '
                'impuestos y cambios posteriores de tarifa pueden modificar la factura.',
            )
            tariffs.write_string(tariff_total_row + 2, 0, 'Cómo se calcula', title)
            tariffs.set_row(tariff_total_row + 2, 30)
            for row, explanation in enumerate(explanations, tariff_total_row + 3):
                tariffs.write_string(row, 0, explanation, note)
            tariffs.set_column(0, 0, 20, body)
            tariffs.set_column(1, 1, 29, body)
            tariffs.set_column(2, 2, 46, body)
            tariffs.set_column(3, 3, 22, body)
            tariffs.set_column(4, 6, 22, currency)
            tariffs.set_column(7, 9, 22, body)
            tariffs.freeze_panes(4, 0)
            for sheet, first, last, last_column, total in (
                (summary, 17, model_total_row - 1, 9, model_total_row),
                (daily, 1, len(report['daily']), 6, len(report['daily']) + 1),
                (requests, 1, len(report['events']), 16, len(report['events']) + 1),
                (tariffs, applied_title_row + 3, tariff_total_row - 1, 9, tariff_total_row),
            ):
                sheet.hide_gridlines(2)
                sheet.set_default_row(22)
                sheet.set_zoom(90)
                sheet.set_tab_color('#1F7830' if sheet is summary else '#A7C9AA')
                if last >= first:
                    sheet.conditional_format(first, 0, last, last_column,
                                             {'type': 'formula', 'criteria': '=MOD(ROW(),2)=0', 'format': stripe})
                sheet.conditional_format(total, 0, total, last_column,
                                         {'type': 'formula', 'criteria': '=TRUE', 'format': total_label})
                sheet.set_row(total, 28)
                sheet.set_landscape()
                sheet.set_paper(9)
                sheet.fit_to_pages(1, 0)
                sheet.set_margins(.3, .3, .4, .4)
                sheet.set_header(f'&LSentry · Costes API&R{period}')
                sheet.set_footer('&LImportes estimados en USD&R&P / &N')
                sheet.print_area(0, 0, total if sheet is not tariffs else tariff_total_row + 6,
                                 10 if sheet is requests else last_column)
                if sheet in (daily, requests):
                    sheet.repeat_rows(0)
                elif sheet is summary:
                    sheet.fit_to_pages(1, 1)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output
