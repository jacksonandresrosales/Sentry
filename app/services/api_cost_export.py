"""Excel del consumo de API, con importes y tarifas registrados por solicitud."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
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
            title = book.add_format({'font_name': 'Aptos', 'font_size': 17, 'bold': True,
                                     'font_color': '#1F7830'})
            header = book.add_format({'font_name': 'Aptos', 'bold': True, 'font_color': '#FFFFFF',
                                      'bg_color': '#202220', 'text_wrap': True, 'valign': 'vcenter'})
            label = book.add_format({'font_name': 'Aptos', 'bold': True, 'font_color': '#202220',
                                     'bg_color': '#EEF7EF'})
            currency = book.add_format({'font_name': 'Aptos', 'num_format': '$#,##0.000000;[Red]($#,##0.000000)'})
            total_currency = book.add_format({'font_name': 'Aptos', 'bold': True,
                                              'bg_color': '#EEF7EF',
                                              'num_format': '$#,##0.000000;[Red]($#,##0.000000)'})
            total_label = book.add_format({'font_name': 'Aptos', 'bold': True,
                                           'bg_color': '#EEF7EF'})
            number = book.add_format({'font_name': 'Aptos', 'num_format': '#,##0.##'})
            note = book.add_format({'font_name': 'Aptos', 'font_color': '#656263', 'text_wrap': True})

            summary = book.add_worksheet('Resumen')
            summary.write_string(0, 0, 'Costes de API · Sentry', title)
            summary.write_string(2, 0, 'Desde', label)
            summary.write_string(2, 1, start.isoformat())
            summary.write_string(3, 0, 'Hasta', label)
            summary.write_string(3, 1, (end - timedelta(days=1)).isoformat())
            summary.write_string(4, 0, 'Servicio', label)
            summary.write_string(4, 1, _label(service, SERVICE_NAMES))
            summary.write_string(5, 0, 'Proveedor', label)
            summary.write_string(5, 1, _label(provider, PROVIDER_NAMES))
            for row, caption, value, fmt in (
                (7, 'Solicitudes registradas', report['requests'], number),
                (8, 'Sin estimación', report['unpriced'], number),
                (9, 'Transcripción', report['transcription_usd'], currency),
                (10, 'Análisis', report['analysis_usd'], currency),
                (11, 'TOTAL ESTIMADO', report['total_usd'], total_currency),
            ):
                summary.write_string(row, 0, caption, total_label if row == 11 else label)
                summary.write_number(row, 1, value, fmt)
            summary.write_string(13, 0, 'Importes estimados; la factura puede diferir.', note)
            summary.write_string(14, 0, 'Las filas sin estimación no se suman.', note)
            summary.write_string(15, 0, 'Incluye todas las solicitudes del filtro.', note)
            for row in (13, 14, 15):
                summary.set_row(row, 30)
            model_headers = ('Servicio', 'Proveedor', 'Modelo', 'Solicitudes', 'Costo USD', 'Sin estimación')
            summary.write_row(16, 0, model_headers, header)
            for row, item in enumerate(report['by_model'], 17):
                summary.write_string(row, 0, _label(item['service'], SERVICE_NAMES))
                summary.write_string(row, 1, _label(item['provider'], PROVIDER_NAMES))
                summary.write_string(row, 2, item['model'])
                summary.write_number(row, 3, item['requests'])
                summary.write_number(row, 4, item['cost_usd'], currency)
                summary.write_number(row, 5, item['unpriced'])
            model_total_row = 17 + len(report['by_model'])
            summary.write_string(model_total_row, 2, 'TOTAL', total_label)
            summary.write_number(model_total_row, 3, report['requests'])
            if report['by_model']:
                summary.write_formula(model_total_row, 4, f'=SUM(E18:E{model_total_row})',
                                      total_currency, report['total_usd'])
            else:
                summary.write_number(model_total_row, 4, 0, total_currency)
            summary.write_number(model_total_row, 5, report['unpriced'])
            summary.set_column(0, 0, 31)
            summary.set_column(1, 1, 25)
            summary.set_column(2, 2, 31)
            summary.set_column(3, 5, 20)
            summary.freeze_panes(17, 0)

            daily = book.add_worksheet('Diario')
            daily_headers = ('Fecha local', 'Transcripción USD', 'Análisis USD', 'Total USD',
                             'Solicitudes transcripción', 'Solicitudes análisis', 'Sin estimación')
            daily.write_row(0, 0, daily_headers, header)
            for row, item in enumerate(report['daily'], 1):
                daily.write_string(row, 0, item['date'])
                for column, key in enumerate(('transcription_usd', 'analysis_usd', 'total_usd'), 1):
                    daily.write_number(row, column, item[key], currency)
                for column, key in enumerate(('transcription_requests', 'analysis_requests', 'unpriced'), 4):
                    daily.write_number(row, column, item[key])
            total_row = len(report['daily']) + 1
            daily.write_string(total_row, 0, 'TOTAL', total_label)
            for column, key in enumerate(('transcription_usd', 'analysis_usd', 'total_usd'), 1):
                daily.write_formula(total_row, column, f'=SUM({xlsxwriter.utility.xl_col_to_name(column)}2:'
                                    f'{xlsxwriter.utility.xl_col_to_name(column)}{total_row})',
                                    total_currency, report[key])
            daily.write_number(total_row, 4, sum(item['transcription_requests'] for item in report['daily']))
            daily.write_number(total_row, 5, sum(item['analysis_requests'] for item in report['daily']))
            daily.write_number(total_row, 6, report['unpriced'])
            daily.set_column(0, 0, 18)
            daily.set_column(1, 6, 23)
            daily.freeze_panes(1, 0)
            daily.autofilter(0, 0, len(report['daily']), len(daily_headers) - 1)

            requests = book.add_worksheet('Solicitudes')
            request_headers = ('Fecha local', 'Servicio', 'Proveedor', 'Modelo', 'Grabación', 'ID solicitud',
                               'Audio s', 'Entrada tokens', 'Entrada caché', 'Salida tokens', 'Unidad tarifa',
                               'Tarifa entrada USD', 'Tarifa caché USD', 'Tarifa salida USD',
                               'Tipo de tarifa', 'Costo USD', 'Estado')
            requests.write_row(0, 0, request_headers, header)
            for row, event in enumerate(report['events'], 1):
                for column, value in enumerate((event['local_at'], _label(event['service'], SERVICE_NAMES),
                        _label(event['provider'], PROVIDER_NAMES), event['model'],
                        event['filename'] or 'Llamada eliminada', event['request_id'] or '')):
                    requests.write_string(row, column, str(value))
                for column, key in ((6, 'audio_seconds'), (7, 'input_tokens'), (8, 'cached_input_tokens'),
                                    (9, 'output_tokens')):
                    _number(requests, row, column, event[key], number)
                requests.write_string(row, 10, 'USD/min' if event['price_unit'] == 'minute' else
                                      'USD/1M tokens' if event['price_unit'] == 'million_tokens' else '')
                for column, key in ((11, 'input_rate'), (12, 'cached_input_rate'), (13, 'output_rate')):
                    if event['price_unit'] != 'minute' or column == 11:
                        _number(requests, row, column, event[key], currency)
                requests.write_string(row, 14, _tariff_name(event))
                _number(requests, row, 15, event['cost_usd'], currency)
                requests.write_string(row, 16, 'Sin estimación' if event['cost_usd'] is None else 'Estimado')
            total_row = len(report['events']) + 1
            requests.write_string(total_row, 14, 'TOTAL ESTIMADO', total_label)
            if report['events']:
                requests.write_formula(total_row, 15, f'=SUM(P2:P{total_row})',
                                       total_currency, report['total_usd'])
            else:
                requests.write_number(total_row, 15, 0, total_currency)
            requests.set_column(0, 0, 21)
            requests.set_column(1, 3, 19)
            requests.set_column(4, 4, 40)
            requests.set_column(5, 5, 38)
            requests.set_column(6, 16, 20)
            requests.freeze_panes(1, 0)
            if report['events']:
                requests.autofilter(0, 0, len(report['events']), len(request_headers) - 1)

            tariffs = book.add_worksheet('Tarifas')
            tariffs.write_string(0, 0, 'Referencias automáticas de pago por uso', title)
            tariffs.write_string(1, 0, 'Referencias incluidas en Sentry; pueden cambiar. '
                                 'AssemblyAI verificado el 16/10/2026.', note)
            tariffs.write_string(2, 0, 'Tarifa usada: hoja Solicitudes.', note)
            tariffs.set_row(1, 42)
            tariffs.set_row(2, 42)
            tariff_headers = ('Proveedor', 'Modelo / concepto', 'Franja o condición', 'Unidad',
                              'Entrada / minuto USD', 'Caché USD', 'Salida USD', 'Fuente')
            tariffs.write_row(3, 0, tariff_headers, header)
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
                    tariffs.write_string(row, column, value)
                for column, value in ((4, input_rate), (5, cached_rate), (6, output_rate)):
                    _number(tariffs, row, column, value, currency)
                tariffs.write_url(row, 7, SOURCES[source], string='Fuente oficial')
            applied_title_row = 4 + len(references) + 1
            tariffs.write_string(applied_title_row, 0, 'Tarifas efectivamente guardadas en este período', title)
            applied_headers = ('Servicio', 'Proveedor', 'Modelo', 'Unidad', 'Entrada / minuto USD',
                               'Caché USD', 'Salida USD', 'Solicitudes', 'Costo USD', 'Sin estimación')
            tariffs.write_row(applied_title_row + 2, 0, applied_headers, header)
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
                        _label(provider_id, PROVIDER_NAMES), model, unit or '')):
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
                'Audio: segundos / 60 × tarifa por minuto. Keyterm suma su tarifa al minuto de Nova-3 o de AssemblyAI.',
                'Texto: (tokens de entrada sin caché × tarifa de entrada + tokens en caché × tarifa '
                'de caché + tokens de salida × tarifa de salida) / 1 000 000.',
                'DeepSeek: pico de lunes a viernes, 01:00–04:00 y 06:00–10:00 UTC. '
                'Fuera de pico se aplica la mitad de cada tarifa.',
                'Los precios son referencias del nivel de pago. Créditos, capas gratuitas, planes, '
                'impuestos y cambios posteriores de tarifa pueden modificar la factura.',
            )
            for row, explanation in enumerate(explanations, tariff_total_row + 2):
                tariffs.write_string(row, 2, explanation, note)
                tariffs.set_row(row, 56)
            tariffs.set_column(0, 0, 20)
            tariffs.set_column(1, 1, 28)
            tariffs.set_column(2, 2, 46)
            tariffs.set_column(3, 3, 22)
            tariffs.set_column(4, 9, 22)
            tariffs.freeze_panes(4, 0)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output
