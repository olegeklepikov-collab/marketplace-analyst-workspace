"""Portable analyst folder: standard library only, no project/server imports.

Canonical passports and an append-preserving index own data. Generated HTML is
replaceable only while its last generated hash matches. One publishing curator.
"""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, unquote
from uuid import uuid4

FORMAT_ID = 'ANALYST_WORKSPACE'
START = 'ОТКРЫТЬ.html'
INDEX = Path('00_Каталоги/Снимки.jsonl')
STATE = Path('00_Каталоги/Рабочие представления.json')
CAT = Path('00_Каталоги')
EDITION = Path('00_Методика/Документы и правила.json')
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
TEXT_EXT = {'.txt', '.md'}
FACT_STATES = {'KNOWN', 'UNKNOWN', 'NOT_OBSERVED', 'UNAVAILABLE', 'NOT_APPLICABLE', 'CONFLICTING'}
STATUS_LABELS = {'ACCEPTED':'Принят в заявленной области', 'ACCEPTED_WITH_LIMIT':'Принят с ограничением', 'RECEIVED':'Получен', 'IDENTITY_PENDING':'Идентичность уточняется'}
SURFACE_LABELS = {'GALLERY':'Галерея', 'RICH':'Подробный блок', 'DESCRIPTION':'Описание', 'CHARACTERISTICS':'Характеристики', 'TITLE':'Заголовок', 'OTHER':'Другой материал'}
PRESETS = {'ANALYSIS': 'Аналитическое заключение', 'RECOMMENDATION': 'Рекомендация', 'IMPLEMENTATION_PLAN': 'План реализации'}
FIELDS = ['platform', 'external_card_id', 'brand', 'seller_display_name', 'observed_at', 'title', 'collection_region', 'mission', 'offer_model']


class FolderError(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).exists() else default


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.writing')
    temp.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
    os.replace(temp, path)


def inside(root, relative):
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts:
        raise FolderError('Ожидается относительный путь внутри папки: ' + str(relative))
    candidate = root / p
    if any((root / Path(*p.parts[:i])).is_symlink() for i in range(1, len(p.parts) + 1)):
        raise FolderError('Символическая ссылка не является независимым файлом: ' + str(relative))
    target = candidate.resolve()
    if not target.is_relative_to(root.resolve()):
        raise FolderError('Путь выходит за папку: ' + str(relative))
    return target


def safe(value, limit=85):
    value = unicodedata.normalize('NFC', str(value or 'Не установлено'))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value)
    value = re.sub(r'\s+', ' ', value).strip(' .')
    return value.encode('utf-8')[:limit].decode('utf-8', 'ignore').rstrip(' .') or 'Не установлено'


def href(target, document):
    return quote(os.path.relpath(str(target), str(document.parent)).replace(os.sep, '/'), safe='/._-')


def link(label, target, document):
    return '<a href="' + href(target, document) + '">' + escape(str(label)) + '</a>'


def fact(value, name, locator='Приём.json', basis='SOURCE_CLAIM'):
    if value is None or value == '':
        return dict(state='UNKNOWN', value=None, basis=basis, scope=name, source_locator=None, reason='Не предоставлено или не установлено.')
    return dict(state='KNOWN', value=value, basis=basis, scope=name, source_locator=locator + ':' + name)


def val(record, key, default=None):
    v = record.get(key)
    if isinstance(v, dict) and 'state' in v:
        return v.get('value') if v['state'] == 'KNOWN' else default
    return default if v is None else v


def shown(record, key):
    v = val(record, key)
    return 'Не установлено' if v is None or v == '' else str(v)


def entries(root):
    path = root / INDEX
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []
    for row in rows:
        if not isinstance(row,dict) or row.get('record_type')!='CATALOG_ENTRY' or row.get('passport_format') not in ['CAPTURE'] or any(not isinstance(row.get(k),str) or not row[k] for k in ['card_id','capture_id','passport_path']):
            raise FolderError('Неверная строка указателя снимков.')
        inside(root,row['passport_path'])
    ids = [x['capture_id'] for x in rows]
    if len(ids) != len(set(ids)):
        raise FolderError('Повтор capture_id в указателе.')
    return rows


def save_entries(root, rows):
    ids = [x['capture_id'] for x in rows]
    if len(ids) != len(set(ids)):
        raise FolderError('Повтор capture_id; указатель не изменён.')
    atomic(root / INDEX, ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows))


def bootstrap(root):
    root = Path(root).resolve()
    for name in ['00_Приём', '00_Каталоги', '01_Карточки', '02_Подборки', '03_Работа аналитиков']:
        (root / name).mkdir(parents=True, exist_ok=True)
    if not (root / INDEX).exists():
        save_entries(root, [])
    return entries(root)


def surface(path):
    parts = [s.lower() for s in path.parts]
    stem = path.stem.lower()
    pos = re.search(r'(\d+)$', stem)
    if 'gallery' in parts:
        return 'GALLERY', int(pos.group()) if pos else None
    if 'rich' in parts:
        return 'RICH', int(pos.group()) if pos else None
    if 'description' in stem or 'описан' in stem:
        return 'DESCRIPTION', None
    if 'characteristic' in stem or 'характерист' in stem:
        return 'CHARACTERISTICS', None
    if stem in ['title', 'заголовок']:
        return 'TITLE', None
    return 'OTHER', None


def read_capture(root, row, unused=None):
    record = read_json(inside(root, row['passport_path']))
    if not record:
        raise FolderError('Нет паспорта: ' + row['passport_path'])
    if (record['card_id'], record['capture_id']) != (row['card_id'], row['capture_id']):
        raise FolderError('Идентификаторы указателя и паспорта различаются.')
    validate_capture(root, record, check_files=False)
    return record


def captures(root):
    return [(row, read_capture(root, row)) for row in entries(root)]


def validate_fact(value):
    if not isinstance(value, dict) or value.get('state') not in FACT_STATES:
        raise FolderError('Неверное состояние значения.')
    if value.get('basis') not in ['OBSERVED','SOURCE_CLAIM','RULE_CANDIDATE','EXPERT_INTERPRETATION','DOCUMENT_VERIFIED'] or not isinstance(value.get('scope'),str) or not value['scope']:
        raise FolderError('Значение требует типа основания и области.')
    if value['state'] == 'KNOWN':
        if value.get('value') is None or not isinstance(value.get('source_locator'),str) or not value['source_locator']:
            raise FolderError('Известное значение требует опоры.')
    else:
        if value.get('value') is not None or not isinstance(value.get('reason'),str) or not value['reason']:
            raise FolderError('Неизвестность требует пустого значения и причины.')
        if value['state'] == 'CONFLICTING':
            alternatives=value.get('alternatives',[])
            if not isinstance(alternatives,list) or len(alternatives)<2 or any(not isinstance(x,dict) or x.get('value') is None or not isinstance(x.get('source_locator'),str) or not x['source_locator'] for x in alternatives):
                raise FolderError('Конфликт требует двух значений с опорами.')


SUBMISSION_FIELDS = FIELDS + ['source_origin','purpose','card_id','scope','scope_complete','attributes','context']


def submission(root, form, materials):
    payload=dict(intake_id=form['intake_id'],registered_at=form['registered_at'],metadata={k:form[k] for k in SUBMISSION_FIELDS if k in form},
                 materials=[dict(file=m['relative_file'].split('01_Материалы/',1)[-1],sha256=m['sha256'],bytes=m['bytes'],surface=m['surface'],position=m['position']) for m in materials])
    data=json_text(payload);sha=hashlib.sha256(data.encode('utf-8')).hexdigest()
    path=Path('00_Приём')/form['intake_id']/('Исходные сведения.'+sha[:16]+'.json')
    return dict(file=path.as_posix(),sha256=sha),data


def write_immutable(root, ref, data):
    path=inside(root,ref['file'])
    if path.exists() and path.read_bytes()!=data.encode('utf-8'):
        raise FolderError('Неизменяемый источник отличается; файл сохранён: '+ref['file'])
    if not path.exists():atomic(path,data)


def check_reference(root, ref):
    if not isinstance(ref,dict) or not isinstance(ref.get('file'),str) or not re.fullmatch(r'[0-9a-f]{64}',str(ref.get('sha256',''))):
        raise FolderError('Не задана контрольная опора источника.')
    path=inside(root,ref['file'])
    if not path.is_file() or digest(path)!=ref['sha256']:
        raise FolderError('Сохранённая опора не совпадает с контрольной суммой: '+ref['file'])
    return path


def validate_provenance(root, record):
    if 'source_submission' in record:check_reference(root,record['source_submission'])
    for revision in record.get('attribution_changes',[]):
        if not isinstance(revision,dict):raise FolderError('Неверная запись уточнения сведений.')
        check_reference(root,dict(file=revision.get('previous'),sha256=revision.get('previous_sha256')))


def changed_submission(root, record, form):
    ref=record.get('source_submission')
    if not ref:return []
    payload=read_json(check_reference(root,ref))
    original=payload['metadata']
    return [k for k in SUBMISSION_FIELDS if original.get(k)!=form.get(k)]


def validate_capture(root, record, check_files=True):
    for key in ['card_id', 'capture_id', 'intake_id', 'source_origin', 'actual_relative_path']:
        if not isinstance(record.get(key), str) or not record[key]:
            raise FolderError('Не заполнено поле паспорта: ' + key)
    if not isinstance(record.get('change_number'),int) or isinstance(record['change_number'],bool) or record['change_number'] < 1:
        raise FolderError('Неверный номер уточнения сведений.')
    if record.get('platform') is not None and not isinstance(record['platform'],str):
        raise FolderError('Площадка должна быть строкой или null.')
    for key in ['registered_at','platform','external_card_id','observed_at','seller_display_name','seller_label_code','brand','materials']:
        if key not in record:raise FolderError('Нет обязательного поля: '+key)
    if record.get('seller_label_code') is not None and not isinstance(record['seller_label_code'],str):raise FolderError('Код продавца должен быть строкой или null.')
    if record.get('record_type') != 'CAPTURE' or record.get('data_format') != 'CAPTURE':
        raise FolderError('Неверная форма паспорта.')
    if record.get('intake_status') not in ['RECEIVED', 'IDENTITY_PENDING', 'ACCEPTED_WITH_LIMIT', 'ACCEPTED']:
        raise FolderError('Неверное состояние приёма.')
    for key in ['external_card_id', 'observed_at', 'brand', 'seller_display_name']:
        validate_fact(record[key])
    inside(root, record['actual_relative_path'])
    if not record.get('materials'):
        raise FolderError('Нет полученных материалов.')
    ids = set()
    for m in record['materials']:
        if m['placement_id'] in ids:
            raise FolderError('Повтор размещения.')
        ids.add(m['placement_id'])
        path = inside(root, m['relative_file'])
        if m.get('surface') not in ['TITLE', 'DESCRIPTION', 'CHARACTERISTICS', 'GALLERY', 'RICH', 'OTHER']:
            raise FolderError('Неизвестная поверхность.')
        if m.get('position') is not None and (not isinstance(m['position'], int) or isinstance(m['position'], bool) or m['position'] < 0):
            raise FolderError('Неверная позиция.')
        if m.get('integrity_state') not in ['VERIFIED', 'NOT_VERIFIED', 'UNREADABLE_OR_DAMAGED']:
            raise FolderError('Неизвестное состояние сохранности.')
        if check_files and (not path.is_file() or path.is_symlink()):
            raise FolderError('Нет локального файла: ' + m['relative_file'])
        if m['integrity_state'] == 'VERIFIED':
            if not re.fullmatch(r'[0-9a-f]{64}', str(m.get('sha256', ''))) or not isinstance(m.get('bytes'), int):
                raise FolderError('VERIFIED требует хеша и размера.')
            if check_files and (path.stat().st_size != m['bytes'] or digest(path) != m['sha256']):
                raise FolderError('Изменён исходный материал: ' + m['relative_file'])
    if check_files:validate_provenance(root,record)
    return True


def validate_metadata(metadata, allowed):
    if not isinstance(metadata, dict):
        raise FolderError('Форма должна быть объектом JSON.')
    try:json.dumps(metadata,allow_nan=False)
    except (ValueError,TypeError) as e:raise FolderError('Недопустимое значение JSON; неизвестное обозначается null.') from e
    if set(metadata)-set(allowed):
        raise FolderError('Неизвестные/служебные поля: '+', '.join(sorted(set(metadata)-set(allowed))))
    for k in FIELDS+['source_origin','scope','card_id','purpose','note']:
        if k in metadata and metadata[k] is not None and not isinstance(metadata[k],str) and not(k=='external_card_id' and isinstance(metadata[k],int) and not isinstance(metadata[k],bool)):
            raise FolderError('Поле '+k+' должно быть строкой или null.')
    if 'scope_complete' in metadata and not isinstance(metadata['scope_complete'],bool):
        raise FolderError('scope_complete должен быть true или false.')
    for k in ['attributes','context']:
        if k in metadata and not isinstance(metadata[k],dict):
            raise FolderError(k+' должен быть объектом JSON.')
    if 'materials' in metadata:
        if not isinstance(metadata['materials'],list) or any(not isinstance(m,dict) or not isinstance(m.get('file'),str) for m in metadata['materials']):
            raise FolderError('materials должен быть списком объектов с относительным file.')


def prepare(root, source, metadata=None):
    root = Path(root).resolve()
    source = Path(source).resolve()
    if not source.is_dir() or root.is_relative_to(source):
        raise FolderError('Укажите отдельную папку поступления, не содержащую хранилище.')
    files = sorted(x for x in source.rglob('*') if x.is_file())
    if not files:
        raise FolderError('В папке поступления нет файлов.')
    if any(x.is_symlink() for x in source.rglob('*')):
        raise FolderError('Для независимого комплекта нужны файлы, а не символические ссылки.')
    metadata = metadata or {}
    allowed = set(FIELDS) | {'source_origin', 'purpose', 'card_id', 'materials', 'attributes', 'context', 'scope', 'scope_complete'}
    validate_metadata(metadata,allowed)
    intake_id = 'IN-' + datetime.now(timezone.utc).strftime('%Y%m%d') + '-' + uuid4().hex[:10]
    dest = root / '00_Приём' / intake_id
    shutil.copytree(source, dest / 'Полученные материалы')
    form = {k: None for k in FIELDS}
    form.update(metadata)
    form.update(intake_id=intake_id, registered_at=stamp(), intake_status='RECEIVED')
    if not form.get('source_origin'):form['source_origin'] = 'Поступление: ' + source.name
    form.setdefault('purpose', 'WORKING_MATERIAL')
    form.setdefault('scope', 'Полученный комплект; полнота живой карточки не установлена.')
    form.setdefault('scope_complete', False)
    atomic(dest / 'Приём.json', json_text(form))
    atomic(dest / 'Приём.md', '# Поступление ' + intake_id + '\n\nМатериалы получены для описания и приёма. Если сведения нужно уточнить, сообщите это агенту обычными словами: он сам внесёт уточнение и сохранит основание. Неизвестное остаётся неизвестным; заполнять техническую форму руководителю не требуется.\n\nДо включения в базу копия находится в Полученные материалы; основной каталог ещё не изменён. После включения эта техническая копия может быть перемещена в единый основной комплект. Агент покажет принятые материалы и их описание; точная ссылка сохраняется в Принято.md. Первоначальная папка переданных файлов сохраняется.\n')
    return dict(form, form_path=(dest / 'Приём.json').relative_to(root).as_posix(), received_materials_path=(dest / 'Полученные материалы').relative_to(root).as_posix(), receipt_path=(dest / 'Приём.md').relative_to(root).as_posix())


def native_key(record):
    platform = record.get('platform')
    external = val(record, 'external_card_id')
    return (str(platform).casefold(), str(external)) if platform and external is not None else None


def seller_labels(items, root=None):
    by_name = {}
    by_code = {}
    records = [c for _, c in items]
    if root is not None:
        dirs = {c['actual_relative_path'] for c in records}
        for directory in dirs:
            for path in (inside(root, directory) / 'Уточнения сведений').glob('*.json'):
                prior = read_json(path, {})
                if 'seller_display_name' in prior:records.append(prior)
    for c in records:
        name, code = val(c, 'seller_display_name'), c.get('seller_label_code')
        if name and code:
            if name in by_name and by_name[name] != code or code in by_code and by_code[code] != name:
                raise FolderError('Конфликт закреплённых кодов продавца.')
            by_name[name] = code
            by_code[code] = name
    return by_name


def observation_key(record):
    observed = val(record, 'observed_at')
    if not isinstance(observed, str) or 'T' not in observed:
        return None
    try:
        moment = datetime.fromisoformat(observed.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None or not native_key(record) or any(val(record, k) is None for k in ['brand','seller_display_name']):
        return None
    if any(m['integrity_state'] != 'VERIFIED' for m in record['materials']):
        return None
    files = [(m['relative_file'].split('01_Материалы/',1)[-1], m['surface'], m['position'], m['sha256']) for m in record['materials']]
    payload = dict(card_id=record['card_id'], native=native_key(record), at=moment.astimezone(timezone.utc).isoformat(),
                   brand=val(record,'brand'),seller=val(record,'seller_display_name'),title=val(record,'title'),
                   region=record.get('collection_region'),context=record.get('context',{}),attributes=record.get('attributes',{}),files=sorted(files,key=lambda x:x[0]))
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


def finish_publication(root, directory, form, rows, row, record, rebuild, status):
    validate_capture(root,record,check_files=True)
    if record['intake_id']!=form['intake_id']:
        raise FolderError('Квитанция указывает чужой снимок.')
    changes=changed_submission(root,record,form)
    if not any(x['capture_id']==row['capture_id'] for x in rows):save_entries(root,rows+[row])
    form.update(intake_status=record['intake_status'],published_capture_id=record['capture_id'],published_card_id=record['card_id'],passport_path=row['passport_path'],published_materials_path=(Path(record['actual_relative_path'])/'01_Материалы').as_posix())
    if record.get('source_submission'):form['source_submission']=record['source_submission']
    atomic(directory/'Приём.json',json_text(form))
    if rebuild:
        try:refresh(root)
        except FolderError as e:raise FolderError('Снимок '+record['capture_id']+' сохранён и включён; представления не обновлены: '+str(e)) from e
    return dict(status=status,changed_input_fields_not_applied=changes,**row)


def publish(root, intake_id, verify_bytes=True, rebuild=True):
    root = Path(root).resolve()
    directory = inside(root, str(Path('00_Приём') / intake_id))
    form_path = directory / 'Приём.json'
    form = read_json(form_path)
    if not form:
        raise FolderError('Нет формы поступления.')
    fields=set(FIELDS)|{'source_origin','purpose','card_id','materials','attributes','context','scope','scope_complete','intake_id','registered_at','intake_status','reserved_card_id','reserved_capture_id','published_capture_id','published_card_id','passport_path','pending_materials_path','published_materials_path','duplicate_observation','duplicate_fingerprint','source_submission','identity_reason'}
    validate_metadata(form,fields)
    if form.get('intake_id')!=intake_id:
        raise FolderError('Номер формы не соответствует папке поступления.')
    rows = bootstrap(root)
    reserved=form.get('reserved_capture_id')
    committed=next((x for x in rows if x['capture_id']==(form.get('published_capture_id') or reserved)),None)
    if form.get('published_capture_id') and committed is None:raise FolderError('Квитанция есть, но указатель не содержит снимок.')
    if committed:
        record=read_capture(root,committed)
        if record['intake_id']!=intake_id:
            if not form.get('duplicate_observation') or form.get('duplicate_fingerprint')!=observation_key(record):
                raise FolderError('Квитанция указывает чужой снимок.')
            payload=read_json(check_reference(root,form.get('source_submission')))
            if payload.get('intake_id')!=intake_id:raise FolderError('Не совпал владелец исходных сведений.')
            if any(payload['metadata'].get(k)!=form.get(k) for k in SUBMISSION_FIELDS):raise FolderError('Сведения повторного поступления изменены; создайте новое поступление.')
            for item in payload['materials']:
                path=inside(root,(Path('00_Приём')/intake_id/'Полученные материалы'/item['file']).as_posix())
                if not path.is_file() or item['sha256'] and digest(path)!=item['sha256']:raise FolderError('Изменена полученная повторная копия.')
            validate_capture(root,record,check_files=True)
            if rebuild:refresh(root)
            return dict(status='ALREADY_ACCEPTED',**committed)
        return finish_publication(root,directory,form,rows,committed,record,rebuild,'ALREADY_ACCEPTED' if form.get('published_capture_id') else 'RECOVERED_ACCEPTANCE')
    items = captures(root)
    key = (str(form['platform']).casefold(), str(form['external_card_id'])) if form.get('platform') and form.get('external_card_id') is not None else None
    matched = {c['card_id'] for _, c in items if key and native_key(c) == key}
    chosen = form.get('card_id')
    known_ids = {c['card_id'] for _, c in items}
    old_keys = {native_key(c) for _, c in items if c['card_id'] == chosen and native_key(c)}
    context_conflict = bool(chosen and key and old_keys and key not in old_keys)
    if context_conflict or len(matched) > 1 or chosen and (chosen not in known_ids or matched and chosen not in matched):
        form.update(intake_status='IDENTITY_PENDING', identity_reason='Неоднозначное соответствие ID; автоматическое объединение не выполнено.')
        atomic(form_path, json_text(form))
        return dict(status='IDENTITY_PENDING', intake_id=intake_id, reason=form['identity_reason'])
    card_id = chosen or (next(iter(matched)) if matched else form.get('reserved_card_id') or 'CARD-' + uuid4().hex[:16])
    capture_id = form.get('reserved_capture_id') or 'CS_' + uuid4().hex
    form.update(reserved_card_id=card_id, reserved_capture_id=capture_id)
    atomic(form_path, json_text(form))
    labels = seller_labels(items, root)
    seller = form.get('seller_display_name')
    code = labels.get(seller)
    if seller and not code:
        n = max([int(re.search(r'(\d+)$', x).group()) for x in labels.values() if re.search(r'(\d+)$', x)] or [0]) + 1
        code = 'П' + str(n).zfill(2)
    # Reuse the existing human card folder only for the same observed seller/brand context.
    same_context = [c for _, c in items if c['card_id'] == card_id and c.get('platform') == form.get('platform')
                    and val(c, 'brand') == form.get('brand') and val(c, 'seller_display_name') == seller]
    if same_context:
        c = same_context[0]
        card_dir = Path(c['actual_relative_path']) if c.get('legacy_passport_path') else Path(c['actual_relative_path']).parent
    else:
        card_dir = Path('01_Карточки') / safe(form.get('platform')) / safe(form.get('brand')) / safe((code + ' ' if code else '') + (seller or 'Не установлено')) / safe(card_id)
    dest = card_dir / capture_id
    expected_materials=(dest/'01_Материалы').as_posix()
    if form.get('pending_materials_path') and form['pending_materials_path']!=expected_materials:
        raise FolderError('Незавершённое поступление закреплено за другим путём; источники не перемещены.')
    pending_passport=root/dest/'Паспорт.json'
    if pending_passport.exists():
        prepared=read_json(pending_passport)
        if prepared.get('intake_id')!=intake_id or prepared.get('capture_id')!=capture_id or prepared.get('card_id')!=card_id:
            raise FolderError('Место паспорта занято другой записью.')
        if changed_submission(root,prepared,form):raise FolderError('Сведения изменены после фиксации паспорта; завершите исходное поступление и используйте amend.')
        name=val(prepared,'seller_display_name');current_labels=seller_labels(items,root)
        if name:
            assigned=current_labels.get(name)
            if not assigned:
                used={v:k for k,v in current_labels.items()}
                assigned=prepared.get('seller_label_code')
                if assigned in used and used[assigned]!=name:
                    assigned='П'+str(max([int(re.search(r'(\d+)$',x).group()) for x in used if re.search(r'(\d+)$',x)] or [0])+1).zfill(2)
            prepared['seller_label_code']=assigned
        validate_capture(root,prepared,check_files=True)
        atomic(pending_passport,json_text(prepared))
        recovered=dict(record_type='CATALOG_ENTRY',card_id=card_id,capture_id=capture_id,passport_path=(dest/'Паспорт.json').as_posix(),passport_format='CAPTURE')
        return finish_publication(root,directory,form,rows,recovered,prepared,rebuild,'RECOVERED_ACCEPTANCE')
    source = directory / 'Полученные материалы'
    if not source.exists() and form.get('pending_materials_path'):
        source = inside(root, form['pending_materials_path'])
    inputs = sorted(x for x in source.rglob('*') if x.is_file())
    if not inputs or any(x.is_symlink() for x in source.rglob('*')):
        raise FolderError('Нужны локальные файлы поступления.')
    materials = []
    overrides = {x['file']: x for x in form.get('materials', [])}
    if not set(overrides)<={p.relative_to(source).as_posix() for p in inputs}:
        raise FolderError('materials содержит файл, которого нет в комплекте.')
    for i, file in enumerate(inputs, 1):
        rel = file.relative_to(source)
        kind, pos = surface(rel)
        extra = overrides.get(str(rel), {})
        materials.append(dict(placement_id=capture_id + ':' + str(i), surface=extra.get('surface', kind), position=extra.get('position', pos),
            relative_file=(dest / '01_Материалы' / rel).as_posix(), integrity_state='VERIFIED' if verify_bytes else 'NOT_VERIFIED',
            sha256=digest(file) if verify_bytes else None, bytes=file.stat().st_size if verify_bytes else None))
    source_ref,source_data=submission(root,form,materials)
    locator=source_ref['file']+':metadata'
    record = dict(record_type='CAPTURE', data_format='CAPTURE', change_number=1, intake_id=intake_id, card_id=card_id,
        capture_id=capture_id, intake_status='ACCEPTED' if all(form.get(k) not in (None, '') for k in ['platform', 'external_card_id', 'observed_at', 'seller_display_name', 'brand']) and verify_bytes and form.get('scope_complete') is True else 'ACCEPTED_WITH_LIMIT',
        registered_at=form['registered_at'], platform=form.get('platform'), source_origin=form['source_origin'], actual_relative_path=dest.as_posix(),
        seller_label_code=code, materials=materials, purpose=form.get('purpose', 'WORKING_MATERIAL'), collection_region=form.get('collection_region'),
        mission=form.get('mission'), offer_model=form.get('offer_model'))
    for k in ['external_card_id', 'observed_at', 'seller_display_name', 'brand', 'title']:
        record[k] = fact(form.get(k), k, locator)
    record['source_submission']=source_ref
    record['attributes'] = form.get('attributes', {})
    record['context'] = form.get('context', {})
    record['coverage_scope'] = form.get('scope')
    record['scope_complete_declared'] = form.get('scope_complete', False)
    record['source_note'] = 'Метаданные сообщены передавшим; юридические отношения и истинность продукта отдельно не проверены.'
    # Validate structure before copying. A failed copy remains unindexed and is resumable.
    validate_capture(root, record, check_files=False)
    write_immutable(root,source_ref,source_data)
    form['source_submission']=source_ref
    atomic(form_path,json_text(form))
    fingerprint = observation_key(record)
    duplicate = next(((row,c) for row,c in items if fingerprint and observation_key(c)==fingerprint),None)
    if duplicate:
        row,c=duplicate
        form.update(intake_status=c['intake_status'],published_capture_id=c['capture_id'],published_card_id=c['card_id'],passport_path=row['passport_path'],duplicate_observation=True,duplicate_fingerprint=fingerprint)
        atomic(form_path,json_text(form))
        writer=Writer(root);receipt=Path('00_Приём')/intake_id/'Принято.md'
        writer.put(receipt,'# Повтор поступления\n\nОсновной снимок уже имеется; новая аналитическая единица не создана. [Открыть]('+href(capture_page(c),receipt)+'). Полученная повторная копия остаётся в приёме; автоматически не удаляется.\n')
        writer.apply(writer.state.get('passport_checks',{}))
        return dict(status='DUPLICATE_OBSERVATION',**row)
    passport = root / dest / 'Паспорт.json'
    material_dir = inside(root, (dest / '01_Материалы').as_posix())
    if source.resolve() != material_dir.resolve():
        if material_dir.exists():
            raise FolderError('Место материалов уже занято; поступление сохранено для уточнения.')
        form['pending_materials_path'] = (dest / '01_Материалы').as_posix()
        atomic(form_path, json_text(form))
        material_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, material_dir)
    for m in materials:
        target = inside(root, m['relative_file'])
        if verify_bytes and (target.stat().st_size != m['bytes'] or digest(target) != m['sha256']):
            raise FolderError('Не совпали материалы: ' + m['relative_file'])
    if passport.exists() and read_json(passport) != record:
        raise FolderError('Паспорт уже существует и отличается.')
    atomic(passport, json_text(record))
    row = dict(record_type='CATALOG_ENTRY', card_id=card_id, capture_id=capture_id, passport_path=(dest / 'Паспорт.json').as_posix(), passport_format='CAPTURE')
    return finish_publication(root,directory,form,rows,row,record,rebuild,record['intake_status'])


class Writer:
    def __init__(self, root):
        self.root = root
        self.state = read_json(root / STATE, {})
        self.old = self.state.get('files', {})
        self.plan = {}

    def put(self, path, value):
        path = Path(path).as_posix()
        inside(self.root, path)
        self.plan[path] = value.encode('utf-8') if isinstance(value, str) else value

    def apply(self, passport_checks):
        for relative, data in self.plan.items():
            path = inside(self.root, relative)
            if path.exists() and digest(path) not in {hashlib.sha256(data).hexdigest(), self.old.get(relative)}:
                raise FolderError('Создаваемый программой файл изменён вручную и сохранён: ' + relative)
        tracked = dict(self.old)
        for relative, data in self.plan.items():
            path = inside(self.root, relative)
            expected = hashlib.sha256(data).hexdigest()
            if not path.exists() or digest(path) != expected:
                atomic(path, data)
            tracked[relative] = expected
        atomic(self.root / STATE, json_text(dict(files=tracked, passport_checks=passport_checks)))


CSS = 'body{font:17px/1.55 -apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#20252a;max-width:1200px;margin:auto;padding:26px}a{color:#064c94}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dfe5;padding:9px;text-align:left;vertical-align:top}th{background:#edf1f4}input,button{font:inherit;padding:9px}input{width:min(95%,700px)}details{margin:16px 0;padding:12px;border:1px solid #d9dfe5}pre{white-space:pre-wrap;overflow-wrap:anywhere}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:18px}.gallery img{width:100%;height:280px;object-fit:contain}.muted{color:#58636c}.scroll{overflow:auto}.notice{padding:12px;background:#fff2d9}'
SEARCH = "const q=document.getElementById('q');if(q)q.addEventListener('input',()=>{const v=q.value.toLocaleLowerCase().trim();let n=0;document.querySelectorAll('[data-search]').forEach(r=>{r.hidden=!r.dataset.search.includes(v);if(!r.hidden)n++});document.getElementById('count').textContent=n;document.querySelectorAll('details').forEach(d=>{const rows=d.querySelectorAll('[data-search]');if(rows.length)d.open=Boolean(v)&&Array.from(rows).some(r=>!r.hidden)})})"
CSS += '.reading{max-width:860px;margin:auto}.doc-image{max-width:100%;height:auto}nav a{display:inline-block;margin:3px 6px 3px 0}a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #275da8;outline-offset:3px}.doc-toc li{margin:5px 0}'


def inline_md(text, document=None):
    text=escape(text)
    text=re.sub(r'!\[([^\]]*)\]\(([^)]+)\)',lambda m:'<img class="doc-image" src="'+m.group(2)+'" alt="'+m.group(1)+'">',text)
    def reference(match):
        target=match.group(2)
        if target.startswith('&lt;')and target.endswith('&gt;'):target=target[4:-4]
        converted=re.sub(r'\.md(?=#|$)','.html',target)
        if document is not None and converted!=target and not re.match(r'^[a-zA-Z][\w+.-]*:',target):
            file=Path(document).parent/unquote(converted.split('#',1)[0])
            if not file.is_file():converted=target
        return '<a href="'+converted+'">'+match.group(1)+'</a>'
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',reference,text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',text)
    return re.sub(r'`([^`]+)`',r'<code>\1</code>',text)


def markdown_html(text, anchor_prefix='', document=None):
    """Small local renderer shared by the runtime and documentation builder."""
    out=[];toc=[];used={};table=False;listing=None;code=False;code_marker=None
    inline=lambda value:inline_md(value,document)
    def close_table():
        nonlocal table
        if table:out.append('</tbody></table></div>');table=False
    def close_list():
        nonlocal listing
        if listing:out.append('</'+listing+'>');listing=None
    for line in text.splitlines():
        marker = fence_marker(line)
        if code:
            if marker and marker.group(1)[0] == code_marker[0] and len(marker.group(1)) >= len(code_marker) and not marker.group(2).strip():
                out.append('</code></pre>');code=False;code_marker=None
            else:out.append(escape(line)+'\n')
            continue
        if line.startswith('<!-- ANALYST_WORK '):continue
        if marker:
            close_table();close_list();out.append('<pre><code>');code=True;code_marker=marker.group(1);continue
        if line.startswith('|'):
            close_list();cells=[c.strip().replace('\\|','|') for c in re.split(r'(?<!\\)\|',line.strip().strip('|'))]
            if all(re.fullmatch(r':?-+:?',c.replace(' ','')) for c in cells):continue
            if not table:
                out.append('<div class="scroll"><table><thead><tr>'+''.join('<th scope="col">'+inline(c)+'</th>' for c in cells)+'</tr></thead><tbody>');table=True
            else:out.append('<tr>'+''.join('<td>'+inline(c)+'</td>' for c in cells)+'</tr>')
            continue
        close_table()
        item=re.match(r'^(?:- |(\d+)\.\s+)(.*)',line)
        if item:
            kind='ol' if item.group(1) else 'ul'
            if listing!=kind:close_list();out.append('<'+kind+'>');listing=kind
            out.append('<li>'+inline(item.group(2))+'</li>');continue
        close_list();heading=re.match(r'^(#{1,4})\s+(.*)',line)
        if heading:
            level=len(heading.group(1));title=heading.group(2);ea=re.match(r'(EA-\d{2})\.',title)
            base=anchor_prefix+(ea.group(1).lower() if ea else re.sub(r'\s+','-',re.sub(r'[^\w\s-]','',title.lower())).strip('-'))
            used[base]=used.get(base,0)+1;anchor=base+('-'+str(used[base]) if used[base]>1 else '')
            out.append('<h'+str(level)+' id="'+escape(anchor,quote=True)+'">'+inline(title)+'</h'+str(level)+'>')
            if level in [2,3]:toc.append('<li><a href="#'+escape(anchor,quote=True)+'">'+escape(title)+'</a></li>')
        elif line.strip():out.append('<p>'+inline(line)+'</p>')
    close_table();close_list()
    if code:out.append('</code></pre>')
    contents='<details class="doc-toc"><summary>Содержание документа</summary><nav aria-label="Содержание"><ol>'+''.join(toc)+'</ol></nav></details>' if len(toc)>2 else ''
    return contents+''.join(out)


def analyst_actions(document):
    return '<section aria-label="Рабочий маршрут"><h2>Начать глубокий анализ</h2><p>Выберите карточки и вопрос, изучите весь предоставленный содержательный состав, выполните предметный разбор и синтез. Поверхностный скрининг не является результатом.</p><p>'+link('Порядок работы',Path('00_Методика/Начать анализ.html'),document)+' · '+link('Действующая методика',Path('00_Методика')/START,document)+' · '+link('Открыть работы',Path('03_Работа аналитиков')/START,document)+'</p></section>'


def workspace_readme():
    return '''# Работы аналитиков

Здесь создаются задания и результаты. Начните с [обучающего руководства](../Обучение%20аналитика.html).

Все операции и анализ выполняет ИИ-агент; аналитик руководит и контролирует обычным языком. Попросите: «Создай работу по этой карточке и полностью разбери её для выбранной цели продажи» или «Продолжи прежнюю работу с этими новыми сведениями». Агент сам находит точные материалы, организует файлы и готовит результат. Автоматическая заготовка не является выполненным анализом.

При контроле попросите объяснить связь изменения с действием покупателя и нужной продажей, показать опоры, сохранить предмет платы, единицы, условия и момент действия в самой передаваемой формулировке. Наблюдение, трактовка, оценка и предполагаемый эффект различаются.

Попросите: «Подготовь читаемую выдачу и покажи, где открыть результат и исходники». Агент сам создаёт и обновляет документы. Техническая проверка не принимает глубину или коммерческий эффект автоматически. [Примеры запросов](../Запросы%20аналитика.html). [Открыть работы](ОТКРЫТЬ.html).
'''


def page(title, body, document):
    nav = ' · '.join(link(label,target,document) for label,target in [('Карточки',Path(START)),('Методика',Path('00_Методика')/START),('Работы',Path('03_Работа аналитиков')/START),('Как работать',Path('README.html'))])
    return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + CSS + '</style><body><nav>' + nav + '</nav><h1>' + escape(title) + '</h1>' + body + '<script>' + SEARCH + '</script></body></html>'


def capture_page(c):
    return CAT / 'Снимки' / (safe(c['capture_id'], 120) + '.html')


def card_page(card_id):
    return CAT / 'Карточки' / (safe(card_id, 120) + '.html')


def catalogue(items, document, heading='', include_source_text=False):
    body=heading+'<p>Это список для просмотра сохранённых материалов. Чтобы найти нужное предложение или составить подборку, можно попросить агента обычными словами. Время наблюдения относится сохранённому комплекту, а состояние приёма не является оценкой качества карточки.</p><label for="q">Быстрый поиск в этом списке по названию, продавцу или номеру</label><br><input id="q" placeholder="Например: Билайн Много Сим"><p>Сохранённых наблюдений: <span id="count">'+str(len(items))+'</span></p><div class="scroll"><table><tr><th>Карточка и наблюдение</th><th>Предложение</th><th>Площадка, бренд, продавец</th><th>Когда и для какой задачи</th></tr>'
    for row,c in items:
        title=shown(c,'title');number=shown(c,'external_card_id');date=shown(c,'observed_at')
        search=' '.join([c['card_id'],c['capture_id'],title,shown(c,'brand'),shown(c,'seller_display_name'),str(c.get('platform') or ''),str(c.get('mission') or ''),c.get('_search_text','') if include_source_text else '']).lower()
        first_image=next((Path(m['relative_file']) for m in c['materials'] if Path(m['relative_file']).suffix.lower() in IMAGE_EXT),None)
        thumb='<img loading="lazy" style="width:76px;height:95px;object-fit:contain" src="'+href(first_image,document)+'" alt="Первое изображение сохранённого комплекта"><br>' if first_image else ''
        badge='<strong>Проверочный материал, не новое предложение рынка</strong><br>' if c.get('purpose')=='ACCEPTANCE_FIXTURE' else ''
        source_links=link('Карточка № '+number,card_page(c['card_id']),document)+'<br>'+link('Открыть это наблюдение',capture_page(c),document)
        trace='<details><summary>Точные ссылки для сверки</summary><p>'+escape(c['capture_id'])+' · '+escape(row['passport_format'])+'</p>'+link('Служебная запись сведений',Path(row['passport_path']),document)+'</details>'
        body+='<tr data-search="'+escape(search,quote=True)+'"><td>'+source_links+trace+'</td><td>'+thumb+badge+escape(title)+'</td><td>'+escape(str(c.get('platform') or 'Не установлено'))+'<br>'+escape(shown(c,'brand'))+'<br>'+escape(shown(c,'seller_display_name'))+'</td><td>'+escape(date)+'<br>'+escape(str(c.get('mission') or 'Задача ещё не установлена'))+'<br>'+escape(STATUS_LABELS[c['intake_status']])+'</td></tr>'
    return body+'</table></div>'


def render_capture(root, row, c, document):
    body='<h2>'+escape(shown(c,'title'))+'</h2><p>Перед вами сохранённый комплект одной карточки. Он показывает материалы и сведения своего наблюдения; действительность услуги сегодня и результат продаж проверяются отдельно.</p>'
    if c.get('purpose')=='ACCEPTANCE_FIXTURE':body+='<p class="notice">Проверочный комплект. Он не является новым фактическим предложением рынка.</p>'
    body+='<table><tr><th>Что известно о комплекте</th><th>Сведения</th></tr>'
    for label,value in [('Площадка',str(c.get('platform') or 'Не установлено')),('Номер карточки на площадке',shown(c,'external_card_id')),('Бренд, как он заявлен',shown(c,'brand')),('Продавец, как он назван',shown(c,'seller_display_name')),('Время наблюдения, не дата приёма',shown(c,'observed_at')),('Состояние приёма материала',STATUS_LABELS[c['intake_status']]),('Предоставлено файлов',str(len(c['materials']))),('Область комплекта',str(c.get('coverage_scope') or 'Область ещё не установлена'))]:
        body+='<tr><th>'+escape(label)+'</th><td>'+escape(value)+'</td></tr>'
    body+='</table><p>Неизвестное время не заменяется датой другого комплекта. Название продавца и бренд сами по себе не подтверждают юридическую сторону или полномочия. Наличие всех файлов не доказывает глубину анализа; показатели другого наблюдения сюда автоматически не переносятся.</p><p>'+link('Другие наблюдения этой карточки',card_page(c['card_id']),document)+' · '+link('Как читать и оценивать сведения',Path('00_Методика/Как читать анализ.html'),document)+'</p>'
    names={'price.txt':'Цена покупки, как записана в источнике','description.txt':'Полное описание предложения','characteristics.txt':'Характеристики','title.txt':'Заголовок','seller.txt':'Название продавца','availability.txt':'Доступность в момент наблюдения','url.txt':'Адрес исходной страницы','rating.txt':'Числовая оценка','reviews_count.txt':'Количество отзывов','tags.txt':'Поисковые метки','content_type.txt':'Тип сохранённого материала','collection_status.txt':'Сведения о получении материала','analytics_rank.txt':'Служебный показатель источника','card_data.json':'Составные сведения исходной страницы','manifest.json':'Происхождение и состав источника','rich_ocr.json':'Автоматически распознанный текст изображения','rich_text.txt':'Текст подробного блока'}
    images=[];body+='<h2>Первичные материалы</h2><p>Исходные тексты и изображения сохраняются буквально. Рядом даны названия и объяснение их роли; это не переписывание рекламы и не подтверждение её обещаний.</p>'
    for m in c['materials']:
        path=Path(m['relative_file']);caption=SURFACE_LABELS[m['surface']]+' / '+str(m['position'] if m['position'] is not None else 'порядок не установлен')
        if path.suffix.lower() in IMAGE_EXT:
            url=href(path,document);images.append('<figure><a href="'+url+'"><img loading="lazy" src="'+url+'" alt="'+escape(caption)+'"></a><figcaption>'+escape(caption)+'</figcaption></figure>');continue
        label=names.get(path.name,'Исходный файл «'+path.name+'»')
        try:content=inside(root,str(path)).read_text(encoding='utf-8-sig')
        except (UnicodeError,OSError):content=None
        body+='<details><summary>'+escape(label)+'</summary><p>'+link('Открыть точный исходник',path,document)+'</p>'
        if path.name=='price.txt':body+='<p>Цена читается вместе с валютой, предметом платежа и моментом. Одно число без единицы не устанавливает рублёвую сумму.</p>'
        if path.name=='reviews_count.txt':body+='<p>Количество отзывов не раскрывает их содержание и не является количеством успешных подключений.</p>'
        if path.name=='analytics_rank.txt':body+='<p>Без собственного определения этот показатель не следует использовать как место по продажам или качеству.</p>'
        if path.name in {'rich_ocr.json','rich_text.txt'}:body+='<p>Это производное представление изображения. Оно не добавляет независимого подтверждения условий; оригинал рассматривается отдельно.</p>'
        if content is None:body+='<p>Текст здесь не прочитан. Исходный файл доступен по ссылке; недоступность не считается пустым содержимым.</p>'
        elif path.suffix.lower()=='.json':
            try:body+=reader_data(json.loads(content,parse_float=str))
            except ValueError:body+='<pre>'+escape(content)+'</pre>'
            body+='<details><summary>Точная запись источника</summary><pre>'+escape(content)+'</pre></details>'
        else:body+='<pre>'+escape(content)+'</pre>'
        body+='</details>'
    exact=read_json(inside(root,row['passport_path']))
    body+='<h2>Подробное описание наблюдения</h2><p>Здесь сохранены значения, происхождение, неизвестность и основания. «Значение указано» означает запись в источнике, а не независимую проверку обещания. Контрольная сумма подтверждает сохранность файла, а не истинность рекламы.</p><details><summary>Открыть все сведения с пояснёнными названиями</summary>'+reader_data(exact)+'<p>'+link('Точная служебная запись',Path(row['passport_path']),document)+'</p></details>'
    if c.get('metadata_files'):
        body+='<h2>Дополнительные сведения источника</h2>'
        for name in c['metadata_files']:
            file=inside(root,name);body+='<details><summary>Фрагмент «'+escape(file.name)+'»</summary><p>'+link('Точная запись фрагмента',Path(name),document)+'</p>'
            try:body+=reader_data(json.loads(file.read_text(encoding='utf-8-sig'),parse_float=str))
            except (ValueError,OSError,UnicodeError):body+='<p>Этот фрагмент здесь не раскрыт; доступен его исходный файл.</p>'
            body+='</details>'
    body+='<details><summary>Служебные ссылки для точной сверки</summary><p>Ссылка на наблюдение: '+escape(c['capture_id'])+'. Формат записи: '+escape(row['passport_format'])+'. Адрес описания: '+escape(row['passport_path'])+'. Эти обозначения нужны для поиска и сверки; вводить их руководителю не требуется.</p></details>'
    return body+('<h2>Оригинальные изображения</h2><div class="gallery">'+''.join(images)+'</div>' if images else '')


def refresh(root):
    root = Path(root).resolve()
    bootstrap(root)
    items = captures(root)
    writer = Writer(root)
    versions = {}
    for row, c in items:
        validate_provenance(root,c)
        texts=[]
        for material in c['materials']:
            file=inside(root,material['relative_file'])
            if file.suffix.lower() in TEXT_EXT:
                try:texts.append(file.read_text(encoding='utf-8-sig'))
                except (UnicodeError,OSError):pass
        c['_search_text']=' '.join(texts)
        path = inside(root, row['passport_path'])
        h = digest(path)
        old = writer.state.get('passport_checks', {}).get(c['capture_id'])
        if old and old['sha256'] != h:
            if c['change_number'] <= old['change_number']:
                raise FolderError('Сведения изменены без регистрации уточнения; используйте amend: ' + c['capture_id'])
            history = inside(root, c['actual_relative_path']) / 'Уточнения сведений' / ('Сведения_' + str(old['change_number']) + '.json')
            if not history.is_file() or digest(history) != old['sha256']:
                raise FolderError('Не сохранены сведения перед уточнением: ' + c['capture_id'])
        versions[c['capture_id']] = dict(sha256=h, change_number=c['change_number'])
        doc = capture_page(c)
        writer.put(doc, page('Материалы: ' + shown(c,'title'), render_capture(root, row, c, doc), doc))
        if c['intake_id'].startswith('IN-'):
            receipt=Path('00_Приём')/c['intake_id']/'Принято.md'
            writer.put(receipt, '# Комплект принят\n\nСнимок: '+c['capture_id']+'\n\nМатериалы находятся в едином основном комплекте. [Открыть снимок]('+href(capture_page(c),receipt)+').\n')
        if not c.get('legacy_passport_path'):
            local = Path(c['actual_relative_path']) / START
            writer.put(local, page('Снимок ' + c['capture_id'], render_capture(root, row, c, local), local))
            md = '# Паспорт снимка ' + c['capture_id'] + '\n\n' + shown(c, 'title') + '\n\n'
            md += '- Карточка: '+c['card_id']+'\n- Приём: '+c['intake_id']+'\n- Номер уточнения сведений: '+str(c['change_number'])+'\n'
            labels={'platform':'Площадка','brand':'Заявленный бренд','seller_display_name':'Точная строка продавца','observed_at':'Время наблюдения'}
            md += '\n'.join('- ' + labels[k] + ': ' + shown(c, k) for k in labels)
            md += '\n\nОбласть: '+str(c.get('coverage_scope') or 'Полученный комплект')+'\n\nИсточник: '+c['source_origin']+'\n\nСостояние: '+STATUS_LABELS[c['intake_status']]+'. Полномочия и истинность продукта отдельно не подтверждены.\n\n## Материалы\n\n'
            for material in c['materials']:
                md+='- ['+Path(material['relative_file']).name+']('+href(Path(material['relative_file']),Path(c['actual_relative_path'])/'Паспорт.md')+'): '+SURFACE_LABELS[material['surface']]+'; '+material['integrity_state']+'.\n'
            writer.put(Path(c['actual_relative_path']) / 'Паспорт.md', reader_passport(c))
    cards = {}
    for item in items:
        cards.setdefault(item[1]['card_id'], []).append(item)
    for card_id, group in cards.items():
        doc = card_page(card_id)
        writer.put(doc, page('Карточка ' + card_id, catalogue(group, doc, '<p>Выберите снимок по дате, продавцу и контексту. Новое состояние не заменяет прежнее.</p>'), doc))
    views = {}
    for item in items:
        c = item[1]
        for axis, value in [('Площадки', c.get('platform')), ('Бренды', val(c, 'brand')), ('Продавцы', val(c, 'seller_display_name')), ('Задачи', c.get('mission')), ('Модели', c.get('offer_model')), ('Готовность приёма', c['intake_status'])]:
            views.setdefault(axis, {}).setdefault(str(value or 'Не установлено'), []).append(item)
    view_home = Path('02_Подборки/Рабочие подборки') / START
    vb = '<p>Все принятые снимки. Подборка не является доказательством коммерческой сопоставимости.</p>'
    memberships = []
    for axis, groups in views.items():
        vb += '<h2>' + escape(axis) + '</h2><ul>'
        for value, group in sorted(groups.items()):
            leaf = view_home.parent / safe(axis) / (safe(value, 75) + '_' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:10]) / START
            writer.put(leaf, page(axis + ': ' + value, catalogue(group, leaf), leaf))
            vb += '<li>' + link(value + ' (' + str(len(group)) + ')', leaf, view_home) + '</li>'
            for _, c in group:
                memberships.append(dict(axis=axis, value=value, capture_id=c['capture_id'], card_id=c['card_id'], open_html=capture_page(c).as_posix()))
        vb += '</ul>'
    writer.put(view_home, page('Рабочие подборки', vb, view_home))
    writer.put(Path('02_Подборки') / START, page('Подборки', '<p>' + link('Открыть подборки по площадке, бренду, продавцу, задаче и предложению', view_home, Path('02_Подборки') / START) + '</p>', Path('02_Подборки') / START))
    intake_doc = Path('00_Приём') / START
    intake_body = '<p>' + link('Как принять материалы',Path('00_Методика/Данные и приём.html'),intake_doc) + '</p><ul>'
    for intake_path in sorted((root/'00_Приём').glob('IN-*')):
        if (intake_path/'Приём.json').is_file():
            intake_body += '<li>' + link(intake_path.name,(intake_path/'Приём.json').relative_to(root),intake_doc) + '</li>'
    writer.put(intake_doc,page('Поступления материалов',intake_body+'</ul>',intake_doc))
    intro = (analyst_actions(Path(START)) if (root/'00_Методика/Начать анализ.html').is_file() else '') + '<p>Поиск также использует сохранённые тексты; совпадение строки не является проверкой её истинности.</p><p>' + str(len(cards)) + ' карточек; ' + str(len(items)) + ' снимков. Снимок источника, экспертный вывод и измеренный эффект различаются.</p>'
    intro += '<p>' + link('Работы и записи аналитиков',Path('03_Работа аналитиков')/START,Path(START)) + ' · ' + link('Рабочие подборки всех снимков', view_home, Path(START)) + ' · ' + link('Приём', Path('00_Приём') / START, Path(START)) + ' · ' + link('Методы и формы', Path('00_Методика') / START, Path(START)) + ' · ' + link('Подборки по задаче и предложению', Path('02_Подборки') / START, Path(START)) + '</p>'
    writer.put(Path(START), page('Папка аналитика: карточки и снимки', catalogue(items, Path(START), intro, include_source_text=True), Path(START)))
    for name, data in [('Снимки.csv', [dict(card_id=c['card_id'], capture_id=c['capture_id'], external_card_id=val(c,'external_card_id'), platform=c.get('platform'), brand=val(c, 'brand'), seller_display_name=val(c, 'seller_display_name'), seller_label_code=c.get('seller_label_code'), observed_at=val(c, 'observed_at'), passport_path=row['passport_path'], open_html=capture_page(c).as_posix(), status=c['intake_status']) for row, c in items]), ('Рабочие подборки.csv', memberships)]:
        if name=='Снимки.csv':
            formats={row['capture_id']:row['passport_format']for row,c in items}
            for row in data:row['passport_format']=formats[row['capture_id']]
        out = io.StringIO(newline='')
        fields = list(data[0]) if data else ['card_id', 'capture_id']
        w = csv.DictWriter(out, fieldnames=fields)
        w.writeheader();w.writerows(data)
        writer.put(CAT / name, out.getvalue().encode('utf-8-sig'))
    stats = dict(cards=len(cards), captures=len(items), seller_labels=len(seller_labels(items, root)), generated_views=len(memberships), purpose='File workflow; not independent method or sales-effect validation.')
    writer.put(CAT / 'Рабочая база.json', json_text(stats))
    if (root/'00_Методика/Методы и разметка.md').is_file():put_method_index(root,writer)
    put_work_index(root,writer)
    writer.apply(versions)
    return stats


def work_record(text):
    match = re.search(r'^<!-- ANALYST_WORK (.+) -->$', text, re.M)
    if not match:
        return None
    record = json.loads(match.group(1))
    if not isinstance(record, dict) or record.get('form') != 'ANALYST_WORK':
        raise FolderError('Повреждён учёт задания.')
    return record


def capture_summary(row, c):
    """Existing facts and exact pointers, without material bodies or inference."""
    return dict(card_id=c['card_id'],capture_id=c['capture_id'],
                platform=c.get('platform'),external_card_id=val(c,'external_card_id'),
                title=val(c,'title'),brand=val(c,'brand'),seller_display_name=val(c,'seller_display_name'),
                observed_at=val(c,'observed_at'),passport_format=row['passport_format'],
                passport_path=row['passport_path'],change_number=c['change_number'],
                open_html=capture_page(c).as_posix(),intake_status=c['intake_status'])


def find_captures(root, query, limit=20):
    if not query.strip() or not isinstance(limit,int) or not 1<=limit<=100:
        raise FolderError('Укажите непустой запрос и предел от 1 до 100.')
    terms=query.casefold().split();found=[]
    for row,c in captures(Path(root).resolve()):
        item=capture_summary(row,c)
        text=' '.join(str(item.get(k)or'')for k in ['card_id','capture_id','external_card_id','title','platform','brand','seller_display_name','observed_at'])
        text+=' '+str(c.get('mission')or'')+' '+str(c.get('offer_model')or'')
        if all(term in text.casefold()for term in terms):found.append(item)
    return dict(status='FOUND'if found else'NO_MATCH',total=len(found),returned=min(limit,len(found)),
                results=found[:limit],scope='Поиск существующих сведений и точных путей; не объединение компаний или коммерческий ранг.')


def show_capture(root, ref):
    root=Path(root).resolve();id=resolve_capture_refs(root,[ref])[0]
    row=next(r for r in entries(root)if r['capture_id']==id);c=read_capture(root,row)
    return dict(status='CAPTURE_FOUND',**capture_summary(row,c),
                current_attribution=dict(brand=c['brand'],seller_display_name=c['seller_display_name']),
                physical_directory=c['actual_relative_path'],
                materials=[dict(file=m['relative_file'],surface=m['surface'],position=m['position'],integrity_state=m['integrity_state'])for m in c['materials']],
                scope='Точные сведения снимка. Имя папки не заменяет текущий паспорт; содержимое файлов не выводится и не оценивается.')


def protected_literals(text):
    units = []
    rest = []
    block = []
    marker = None
    for line in text.splitlines(keepends=True):
        current = fence_marker(line)
        if marker is not None:
            block.append(line)
            if current and current.group(1)[0] == marker[0] and len(current.group(1)) >= len(marker) and not current.group(2).strip():
                units.append(('CODE_BLOCK', ''.join(block)))
                marker = None
                block = []
                rest.append('\n')
        elif current:
            marker = current.group(1)
            block = [line]
        else:
            rest.append(line)
    if block:
        units.append(('CODE_BLOCK', ''.join(block)))
    def inline(match):
        units.append(('INLINE_CODE', match.group(2)))
        return ' '
    free = re.sub(r'(?<!`)(`+)([^\n]*?)\1(?!`)', inline, ''.join(rest))
    units.extend(('LINK_TARGET', match.group(1)) for match in re.finditer(r'!?\[[^\]\n]*\]\(([^)\n]+)\)', free))
    units.extend(('ID', match.group()) for match in re.finditer(r'\b(?:CS_[A-Za-z0-9_]+|RUN-[A-Za-z0-9_]+|ACT-\d+)\b', free))
    return Counter(units)


def check_edit(root, original, edited):
    root=Path(root).resolve();before=inside(root,str(original)).read_text();after=inside(root,str(edited)).read_text()
    old,new=protected_literals(before),protected_literals(after)
    def entries(counter):return [dict(kind=k,text=t,count=n)for(k,t),n in counter.items()]
    removed=entries(old-new);added=entries(new-old)
    return dict(status='LITERALS_CHANGED'if removed or added else'LITERALS_PRESERVED',removed=removed,added=added,
                scope='Только буквальные блоки, кодовые строки, адреса ссылок и обозначенные ID. Роли, деньги, условия и исполнимость проверяет редактор; смысловая приёмка автоматически не присвоена.')


def declared_work_state(text, record):
    head='\n'.join(text.splitlines()[:45])
    found=re.search(r'(?m)^\*\*(?:Состояние|Статус):\s*(.+?)\*\*',head)
    if found is None:found=re.search(r'(?m)^\*\*(?:Состояние|Статус)\*\*:\s*(.+)$',head)
    state=found.group(1).strip()if found else'Авторское состояние не указано; откройте отчёт'
    revision=(record or{}).get('ruleset')
    if not revision:
        m=re.search(r'Работа по стандарту\s+(\d+(?:\.\d+)+)',head)
        if m is None:m=re.search(r'(?m)^- Методическая редакция:\s*(\d+(?:\.\d+)+)',text)
        revision=m.group(1)if m else'Применённые правила внутри работы'
    return state,revision


def review_work(root, relative):
    path=inside(Path(root).resolve(),str(relative));text=path.read_text();record=work_record(text)
    if record is None:return dict(status='NO_REVIEW_RECORD',explanation='Нет служебного учёта задания. Глубина не проверяется автоматически.')
    files={};methods={};problems=[]
    for line in text.splitlines():
        cells=[x.strip() for x in line.split('|')]
        if len(cells)>4 and re.fullmatch(r'F\d{4}',cells[1]):
            id=cells[1]
            if id in files:problems.append('Повтор строки '+id)
            files[id]=(cells[2].casefold(),cells[4])
        if len(cells)>3 and re.fullmatch(r'EA-\d{2}',cells[1]):
            id=cells[1]
            if id in methods:problems.append('Повтор направления '+id)
            methods[id]=(cells[2].casefold(),cells[3])
    expected={'F'+str(n).zfill(4) for n in range(1,record['material_count']+1)}
    if set(files)!=expected:problems.append('Изменён состав строк материалов относительно задания.')
    pending=[]
    for id in sorted(expected):
        state,note=files.get(id,('',''))
        if not note or note.startswith(('Заполнить','Указать')):pending.append(id);continue
        if state=='изучено':continue
        refs=re.findall(r'F\d{4}',note)
        if state=='производное' and any(ref!=id and files.get(ref,('',))[0]=='изучено' for ref in refs):continue
        pending.append(id)
    unhandled=[]
    for id in record['methods']:
        state,note=methods.get(id,('',''))
        if state not in ['разобрано','ограничено','неприменимо'] or not note or note.startswith(('Заполнить','Указать')):unhandled.append(id)
    if set(methods)!=set(record['methods']):problems.append('Изменён состав направлений относительно задания.')
    if methods and not any(state in ['разобрано','ограничено'] for state,note in methods.values()):problems.append('Нет содержательно разобранного направления.')
    return dict(status='REVIEW_INCOMPLETE' if pending or unhandled or problems else 'REVIEW_ACCOUNTED',
                materials_total=len(expected),materials_accounted=len(expected)-len(pending),pending_materials=pending,
                pending_directions=unhandled,errors=problems,
                substantive_acceptance='Требуется экспертная приёмка глубины и синтеза. Учёт не подтверждает качество или эффект и не завершает работу автоматически.')


def work_html(root, relative):
    path=inside(root,str(relative));text=path.read_text();main,sep,extra=text.partition('## Справочная часть: состав и правила')
    summary=review_work(root,relative)
    counted=str(summary.get('materials_accounted',0));total=str(summary.get('materials_total',0))
    if summary.get('status')=='REVIEW_ACCOUNTED':
        notice='В учёте отмечены все предоставленные материалы: '+counted+' из '+total+'. Это проверка состава записи. Она не подтверждает глубину, истинность условий или эффект продажи; анализ и его результаты оцениваются по основаниям.'
    else:
        notice='Учёт записи пока не завершён: отмечено '+counted+' из '+total+' материалов. Страница показывает рабочее состояние. Программный учёт не устанавливает, насколько глубоко выполнен анализ, и не принимает заключение автоматически.'
    body='<p class="notice">'+notice+'</p><article class="reading">'+report_markdown('\n'.join(main.splitlines()[1:]),document=path.with_suffix('.html'))+'</article>'
    if sep:body+='<details><summary>Точные источники, обработка и применённые правила</summary>'+markdown_html(extra,'основания-',path.with_suffix('.html'))+'</details>'
    return page(text.splitlines()[0].removeprefix('# '),body,Path(relative).with_suffix('.html'))


def put_work_index(root, writer, render=None):
    document = Path('03_Работа аналитиков') / START
    catalog = root / CAT / 'Снимки.csv'
    with catalog.open(encoding='utf-8-sig', newline='') if catalog.exists() else io.StringIO('') as handle:
        inputs = {row['capture_id']: row for row in csv.DictReader(handle)}
    records = []
    html_rows = []
    for path in sorted((root / '03_Работа аналитиков').glob('*/Разбор.md')):
        relative = path.relative_to(root)
        text = path.read_text()
        record = work_record(text)
        if not record:
            continue
        if relative == render or not path.with_suffix('.html').is_file():
            writer.put(relative.with_suffix('.html'), work_html(root, relative))
        title = next((line[2:] for line in text.splitlines() if line.startswith('# ')), path.parent.name)
        state = declared_work_state(text, record)[0]
        selected = [inputs[x] for x in record['capture_ids'] if x in inputs]
        labels = [' · '.join(row.get(k) or 'Не установлено' for k in ['external_card_id', 'brand', 'seller_display_name']) for row in selected]
        links = '<br>'.join(link(label, Path(row['open_html']), document) for label, row in zip(labels, selected))
        search = ' '.join([title, state] + labels).casefold()
        html_rows.append('<tr data-search="' + escape(search, quote=True) + '"><td>' + escape(title) + '</td><td>' + escape(state) + '</td><td>' + links + '</td><td>' + link('Читать результат', relative.with_suffix('.html'), document) + '</td><td>' + link('Точный текст для сверки', relative, document) + '</td></tr>')
        records.append(dict(title=title, author_state=state, capture_ids=', '.join(record['capture_ids']), markdown=relative.as_posix(), html=relative.with_suffix('.html').as_posix()))
    body = '<p>Здесь результаты порученных работ. Состояние описывает готовность по заявлению исполнителя; правильность вывода оценивает аналитик по основаниям. Для продолжения или новой выдачи достаточно обычного запроса агенту: он сам обновит документы.</p><input id="q" placeholder="Название, карточка или состояние"><p>Работ: <span id="count">' + str(len(records)) + '</span></p>'
    body += '<div class="scroll"><table><tr><th>Работа</th><th>Состояние автора</th><th>Карточки</th><th>Читать</th><th>Основной файл</th></tr>' + ''.join(html_rows) + '</table></div>'
    if not records:
        body += '<p>Работ пока нет. Попросите агента: «Разбери эту карточку для моей задачи и подготовь понятное заключение».</p>'
    writer.put(document, page('Работы аналитиков', body, document))
    stream = io.StringIO(newline='')
    csv_writer = csv.DictWriter(stream, fieldnames=['title', 'author_state', 'capture_ids', 'markdown', 'html'])
    csv_writer.writeheader()
    csv_writer.writerows(records)
    writer.put(document.parent / 'Работы.csv', stream.getvalue().encode('utf-8-sig'))


def put_method_index(root, writer):
    document = Path('00_Методика') / START
    body = '<p>Аналитик выбирает вопрос и оценивает ответ, агент применяет необходимые методы и оформляет работу. Выбирайте документ по содержательному вопросу; перечитывать весь справочник перед каждой операцией не требуется.</p><p>' + link('Как читать и оценивать анализ',Path('00_Методика/Как читать анализ.html'),document) + ' · ' + link('Запросы аналитика',Path('Запросы аналитика.html'),document) + '</p><ol>'
    for item in edition(root)['active_documents']:
        target = Path(item['path'])
        if target.suffix == '.md':
            body += '<li>' + link(item['label'], target.with_suffix('.html'), document) + '</li>'
    body += '</ol><p>' + link('Обучение: от получения материала до передачи результата', Path('Обучение аналитика.html'), document) + '</p>'
    writer.put(document, page('Методы, формы и обучение', body, document))


def render_work(root, relative):
    root=Path(root).resolve();path=inside(root,str(relative))
    if work_record(path.read_text()) is None:raise FolderError('render-work создаёт HTML основного Разбор.md со служебным договором, созданным work. В этом файле договора нет. Отдельные V0/V1, задание и проект сохраняйте в Markdown; нужные разделы включите в основной разбор. Фиктивный договор для них создавать не нужно.')
    writer=Writer(root);put_work_index(root,writer,Path(relative));writer.apply(writer.state.get('passport_checks',{}))
    return dict(file=str(relative),html=Path(relative).with_suffix('.html').as_posix(),source_changed=False)


def resolve_capture_refs(root, refs, interactive=False):
    rows=entries(root);resolved=[]
    catalog=root/CAT/'Снимки.csv'
    with catalog.open(encoding='utf-8-sig',newline='') if catalog.is_file() else io.StringIO('') as handle:
        native=list(csv.DictReader(handle))
    for ref in refs:
        exact=[r for r in rows if r['capture_id']==ref]
        found=exact or [r for r in rows if ref in [r['card_id'],r['card_id'].partition(':')[2]]]
        if not found:
            candidates={r['capture_id'] for r in native if r.get('external_card_id')==ref}
            found=[r for r in rows if r['capture_id'] in candidates]
            for row in found:
                if val(read_capture(root,row),'external_card_id')!=ref:raise FolderError('Каталог номера изменён. Обновите представления либо укажите точный ID снимка.')
        if not found:raise FolderError('Не найден снимок или номер карточки: '+str(ref))
        if len(found)>1:
            if not interactive:raise FolderError('У карточки несколько снимков. Выберите точный ID: '+', '.join(r['capture_id'] for r in found))
            print('У карточки '+ref+' несколько снимков. Выберите по дате и контексту:')
            for number,row in enumerate(found,1):
                c=read_capture(root,row)
                print(str(number)+'. '+shown(c,'observed_at')+'; '+shown(c,'seller_display_name')+'; '+shown(c,'title')+'; '+c['capture_id'])
            choice=input('Номер нужного снимка: ').strip()
            if not choice.isdigit() or not 1<=int(choice)<=len(found):raise FolderError('Снимок не выбран; работа не создана.')
            found=[found[int(choice)-1]]
        resolved.append(found[0]['capture_id'])
    return resolved


def amend(root, capture_id, metadata, reason):
    root = Path(root).resolve()
    rows = entries(root)
    row = next((x for x in rows if x['capture_id'] == capture_id), None)
    if row is None or not reason.strip():
        raise FolderError('Нужны существующий снимок и причина уточнения.')
    validate_metadata(metadata,set(FIELDS)|{'note','attributes','context'})
    if not metadata:raise FolderError('Не заданы уточняемые поля.')
    c = read_capture(root, row)
    if c.get('attribution_changes') and c['attribution_changes'][-1].get('changes')==metadata and c['attribution_changes'][-1].get('reason')==reason:
        validate_capture(root,c,check_files=True)
        refresh(root)
        return c
    old_bytes = inside(root, row['passport_path']).read_bytes()
    for k in metadata:
        if k not in FIELDS and k not in ['note', 'attributes', 'context']:
            raise FolderError('Неизменяемое/неподдержанное поле: ' + k)
    proposal = dict(c)
    change_locator=(Path(c['actual_relative_path'])/'Паспорт.json').as_posix()+'#attribution_changes/'+str(len(c.get('attribution_changes',[])))+'/changes'
    for k, value in metadata.items():
        proposal[k] = fact(value, k, change_locator) if k in ['external_card_id', 'observed_at', 'seller_display_name', 'brand', 'title'] else value
    all_items = captures(root)
    proposed_key = native_key(proposal)
    if proposed_key and any(native_key(other) == proposed_key and other['card_id'] != c['card_id'] for _, other in all_items):
        raise FolderError('Уточнение совпало с другой карточкой; требуется разрешить идентичность, объединение не выполнено.')
    labels = seller_labels(all_items, root)
    seller = val(proposal, 'seller_display_name')
    code = labels.get(seller)
    if seller and not code:
        n = max([int(re.search(r'(\d+)$', x).group()) for x in labels.values() if re.search(r'(\d+)$', x)] or [0]) + 1
        code = 'П' + str(n).zfill(2)
    proposal['seller_label_code'] = code
    history = Path(c['actual_relative_path']) / 'Уточнения сведений' / ('Сведения_' + str(c['change_number']) + '.json')
    h = inside(root, str(history))
    if h.exists() and h.read_bytes() != old_bytes:
        raise FolderError('История версии уже занята отличающимся содержанием.')
    c = proposal
    c['change_number'] += 1
    if any(val(c,k) is None for k in ['external_card_id','observed_at','brand','seller_display_name']) or any(m['integrity_state']!='VERIFIED' for m in c['materials']):
        c['intake_status']='ACCEPTED_WITH_LIMIT'
    c.setdefault('attribution_changes', []).append(dict(at=stamp(), reason=reason, previous=history.as_posix(),previous_sha256=hashlib.sha256(old_bytes).hexdigest(),changes=metadata))
    path = Path(c['actual_relative_path']) / 'Паспорт.json'
    validate_capture(root, c, check_files=False)
    validate_provenance(root,read_capture(root,row))
    if not h.exists():atomic(h, old_bytes)
    atomic(inside(root, str(path)), json_text(c))
    row.update(passport_path=path.as_posix(), passport_format='CAPTURE')
    save_entries(root, rows)
    refresh(root)
    return c


def comparison(root, spec, *, capture_ids=None):
    if not isinstance(spec,dict):raise FolderError('Договор сравнения должен быть объектом JSON.')
    kind = spec.get('type', 'NONE')
    if kind not in ['NONE', 'CONTRAST', 'COMMERCIAL', 'VERSIONS', 'EXPERT_PRACTICE']:
        raise FolderError('Неизвестный тип сравнения.')
    if kind != 'COMMERCIAL':
        return dict(status='EXPERT_COMPARISON' if kind != 'NONE' else 'NO_COMPARISON', ranking=[], explanation='Экспертное сопоставление; победитель по продажам не назначается.')
    if 'higher_is_better' in spec and not isinstance(spec['higher_is_better'],bool):raise FolderError('higher_is_better должен быть true или false.')
    metric_kind=spec.get('metric_kind') or ('RATE' if str(spec.get('unit','')).upper() in ['PERCENT','PERCENTAGE','FRACTION','%'] else 'COUNT')
    if metric_kind not in ['COUNT','RATE']:raise FolderError('metric_kind должен быть COUNT или RATE.')
    if not isinstance(spec.get('rows',[]),list) or any(not isinstance(x,dict) for x in spec.get('rows',[])):raise FolderError('rows должен быть списком записей.')
    if not isinstance(spec.get('critical_conditions',[]),list) or any(not isinstance(x,dict) for x in spec.get('critical_conditions',[])):raise FolderError('critical_conditions должен быть списком условий.')
    problems = []
    rows = spec.get('rows', [])
    index_rows = {x['capture_id']: x for x in entries(root)}
    index = set(index_rows)
    for field in ['question', 'group_basis', 'event', 'metric', 'unit', 'period_scope', 'comparability_basis']:
        if not spec.get(field):problems.append('Не установлено: ' + field)
    if len(rows) < 2:problems.append('Нужно не менее двух сопоставляемых единиц; это не критерий статистической достаточности.')
    if not spec.get('critical_conditions'):
        problems.append('Не заданы критичные условия сравнения.')
    for condition in spec.get('critical_conditions', []):
        if condition.get('state') not in ['MATCH', 'STUDIED_DIFFERENCE'] or not condition.get('basis'):
            problems.append('Не разрешено условие: ' + str(condition.get('name')))
    for x in rows:
        if x.get('capture_id') not in index:problems.append('Неизвестный снимок.')
        if x.get('event') != spec.get('event') or x.get('unit') != spec.get('unit') or x.get('period_scope') != spec.get('period_scope'):
            problems.append('Разные события, единицы или периоды.')
        if not isinstance(x.get('value'), (int, float)) or isinstance(x.get('value'), bool) or not math.isfinite(x['value']) or not x.get('source'):
            problems.append('Нет числового результата с источником.')
        if metric_kind == 'RATE' and (not isinstance(x.get('denominator'), (int, float)) or isinstance(x['denominator'], bool) or not math.isfinite(x['denominator']) or x['denominator'] <= 0):
            problems.append('Не установлен знаменатель доли.')
    if len({x.get('capture_id') for x in rows}) != len(rows):problems.append('Повтор единицы результата.')
    cards = [index_rows[x['capture_id']]['card_id'] for x in rows if x.get('capture_id') in index_rows]
    if len(cards) != len(set(cards)):problems.append('Одна карточка повторена снимками; нужен отдельный план сравнения версий.')
    selected=set(capture_ids) if capture_ids is not None else {x.get('capture_id') for x in rows}
    represented={x.get('capture_id') for x in rows}
    if represented-selected:problems.append('Строка результата не входит в выбранный состав работы.')
    missing=sorted(selected-represented)
    primary=spec.get('requested_primary')
    if primary is not None and not isinstance(primary,dict):raise FolderError('requested_primary должен быть объектом JSON.')
    fields=['event','metric','unit','period_scope']
    same=primary is not None and all(primary.get(k) and primary[k]==spec.get(k) for k in fields)
    primary_status='NOT_DECLARED' if primary is None else ('COMPATIBLE_INPUTS_NOT_EFFECT' if same and not missing and not problems else 'NOT_ESTABLISHED')
    permission=spec.get('descriptive_question')
    if permission is not None and (not isinstance(permission,str) or not permission.strip()):raise FolderError('descriptive_question должен называть отдельный описательный вопрос.')
    if primary is not None and primary_status!='COMPATIBLE_INPUTS_NOT_EFFECT' and not permission:
        problems.append('Первичный результат недоступен; дополнительный описательный вопрос не задан.')
    ranking=[] if problems else sorted(rows,key=lambda x:x['value'],reverse=spec.get('higher_is_better',True))
    return dict(status='INSUFFICIENT_FOR_SALES_RANK' if problems else 'DESCRIPTIVE_RANK_IN_DECLARED_SCOPE',
                ranking=ranking,reasons=sorted(set(problems)),
                primary_calculation=dict(status=primary_status,requested=primary,observed={k:spec.get(k) for k in fields}),
                descriptive_result=dict(status='NOT_CALCULATED' if problems else 'CALCULATED_IN_DECLARED_SCOPE',question=permission or spec.get('question')),
                coverage=dict(selected_capture_ids=sorted(selected),missing_result_capture_ids=missing,missing_is_zero=False),
                explanation='Показан только заявленный описательный контур. Родные события/единицы сохранены; отсутствующие строки не нули. Структурная совместимость не устанавливает экспертную сопоставимость или причинный эффект.')


def edition(root, check_hashes=False):
    root = Path(root).resolve()
    record = read_json(root / EDITION)
    if not isinstance(record, dict) or not isinstance(record.get('active_documents'), list):
        raise FolderError('Нет указателя рабочих документов: ' + EDITION.as_posix())
    if check_hashes:
        for item in record['active_documents']:
            path = inside(root, item['path'])
            if not path.is_file() or digest(path) != item['sha256']:
                raise FolderError('Рабочий документ изменён относительно указателя: ' + item['path'])
    return dict(record, status='DOCUMENTS_CHECKED' if check_hashes else 'DOCUMENTS_LISTED')


def freeze_rules(root, additional_rules=None):
    edition(root, check_hashes=True)
    paths = ['00_Методика/Методы и разметка.md', '00_Методика/Качество и приёмка.md', '00_Методика/Данные и приём.md', '00_Методика/Глубокий разбор сообщений.md', '00_Методика/Как писать развернутый отчёт.md', '00_Методика/Как принимать выводы и выбирать методы.md'] + list(additional_rules or [])
    result = []
    for name in dict.fromkeys(paths):
        source = inside(root, name)
        raw = source.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        target = Path('03_Работа аналитиков/Основания заданий') / (checksum + '.txt')
        if (root / target).exists() and digest(root / target) != checksum:
            raise FolderError('Изменена сохранённая опора работы.')
        if not (root / target).exists():
            atomic(root / target, raw)
        result.append(dict(name=source.name, path=target.as_posix(), sha256=checksum, source_path=name, object_kind='WORKING_RULES'))
    return result


def task_lines(contract, result, legacy_access):
    """The visible task is the only editable semantic record; no hidden copy."""
    contract={} if contract is None else contract
    allowed={'scope','analysis_basis','result_party','target_result','outcome_access','prior_familiarity','additional_rules','effect_project'}
    if not isinstance(contract,dict) or set(contract)-allowed:raise FolderError('Неизвестное поле постановки задачи.')
    for key in allowed-{'additional_rules','effect_project'}:
        if key in contract and contract[key] is not None and not isinstance(contract[key],str):raise FolderError('Поле постановки должно быть текстом или null: '+key)
    if 'additional_rules' in contract and (not isinstance(contract['additional_rules'],list) or any(not isinstance(x,str) for x in contract['additional_rules'])):raise FolderError('additional_rules: список относительных путей.')
    if 'effect_project' in contract and not isinstance(contract['effect_project'],dict):raise FolderError('effect_project: объект с определениями проекта, неизвестные входы null.')
    try:json_text(contract)
    except (TypeError,ValueError) as e:raise FolderError('Постановка должна быть корректным JSON без неустановленных чисел.') from e
    access=contract.get('outcome_access') or legacy_access
    if access not in ['OPEN','MASKED','NOT_RECORDED']:raise FolderError('Доступ к исходам: OPEN, MASKED или NOT_RECORDED.')
    labels={'OPEN':'Открытый: исходы доступны','MASKED':'Исходы скрыты; техническая изоляция не заявлена','NOT_RECORDED':'Не зафиксирован'}
    rows=[('Область вопроса',contract.get('scope') or 'Весь предоставленный состав; углубление по вопросу'),
          ('Основание',contract.get('analysis_basis') or 'Указать выбранный экспертный подход / контраст / коммерческое сравнение'),
          ('Доступ к исходам',labels[access]),('Знакомость с материалом и прежними ответами',contract.get('prior_familiarity') or 'Не зафиксирована')]
    if result!='ANALYSIS' or contract.get('result_party') or contract.get('target_result'):
        rows += [('Сторона результата',contract.get('result_party') or 'Не установлена; ограничить соответствующий вывод'),('Нужная продажа или использование',contract.get('target_result') or 'Указать по вопросу; эффект не измерен')]
    lines=['## Постановка задачи','','| Решение | Заданная область |','|---|---|']
    for label,value in rows:lines.append('| '+label+' | '+value.replace('|','\\|').replace('\n','; ')+' |')
    if 'effect_project' in contract:
        project=contract['effect_project']
        fields={
            'assignment':'Кому и когда назначают: пригодность до назначения, единица, повторный контакт',
            'exposure':'Фактический показ: запись версии, непоказ и предельный срок',
            'result':'Основное событие и формула: числитель, все назначенные в знаменателе; условные диагностики отдельно',
            'windows':'Окна заказа/оплаты/возврата/использования и порядок событий',
            'closure':'Конец наблюдения последней группы: неполучение, поздний исход, недоступный журнал',
            'decision':'Правило решения: полезное различие и его основание, неопределённость, затраты/защита, объём/остановка',
            'readiness':'Состояние проекта: доступный механизм, недостающие входы и ответственные до запуска'}
        lines+=['','### Договор проекта эффекта','','| Решение до запуска | Единственная текущая запись |','|---|---|']
        for key in list(fields)+[k for k in project if k not in fields]:
            value=project.get(key)
            rendered='Не установлено; указать выполненное и недостающий вход'if value is None else(value if isinstance(value,str)else json.dumps(value,ensure_ascii=False))
            lines.append('| '+fields.get(key,key)+' | '+rendered.replace('|','\\|').replace('\n','; ')+' |')
        lines+=['','У каждого назначенного есть конечный срок определения результата, включая непоказ и неполучение. Отсутствующее событие при полном наблюдении и недоступный журнал различаются. Числа окна или порога сами по себе не доказывают пригодности. Наличие таблицы не означает готовность или запуск; проверка учёта не принимает её смысл.']
    lines+=['','Постановка редактируется здесь. Заявление карточки, внешнее условие, трактовка и эффект различаются. Дополнительные определения, изменившие критерий, укажите в применённых правилах.','']
    return lines


def new_work(root, question, capture_ids, result='ANALYSIS', methods=None, comparison_spec=None, name=None, *, task_contract=None):
    root = Path(root).resolve()
    if not question.strip() or result not in PRESETS or not capture_ids:
        raise FolderError('Нужны вопрос, снимки и корректный вид выхода.')
    capture_ids=resolve_capture_refs(root,capture_ids)
    rows = {row['capture_id']: row for row in entries(root)}
    if not set(capture_ids) <= rows.keys():
        raise FolderError('Неизвестный вход работы.')
    if len(capture_ids)!=len(set(capture_ids)):raise FolderError('Повтор ID снимка в составе работы.')
    # A local work needs only its declared inputs. Full-library validation
    # remains verify(); unrelated offline files must not block this task.
    manifest = {}
    available = {id: (rows[id], read_capture(root, rows[id], manifest)) for id in capture_ids}
    methods = methods or ['EA-'+str(n).zfill(2) for n in range(15)]
    if any(not re.fullmatch(r'EA-(?:0\d|1[0-4])', x) for x in methods):
        raise FolderError('Неизвестное направление.')
    run_id = 'RUN-' + uuid4().hex[:12]
    directory = Path('03_Работа аналитиков') / (safe(name or question) + '-' + run_id)
    doc = directory / 'Разбор.md'
    original_comparison=comparison_spec or {}
    try:json_text(original_comparison)
    except (ValueError,TypeError) as e:raise FolderError('Договор должен быть корректным JSON; неустановленные числа обозначаются null.') from e
    compare = comparison(root, original_comparison,capture_ids=capture_ids)
    task=task_lines(task_contract,result,original_comparison.get('outcome_access','NOT_RECORDED'))
    release=edition(root,check_hashes=True)
    frozen_rules=freeze_rules(root,(task_contract or {}).get('additional_rules'))
    intro = ['# ' + question, '', '**Состояние: задание подготовлено; экспертное заключение ещё не выполнено.**', '',
             '## Выбор аналитических средств', '',
             'Агент объясняет в постановке и поле «Основание», какие методы нужны и какой результат даст каждый. В конце сверяет их с фактическим выходом. Не требуется перечень всех средств, отдельный JSON или внешнее подтверждение каждого экспертного вывода. Ограничение относится своему утверждению и действию.', '',
             '## Ответ для бизнеса', '',
             'После полного глубокого разбора объясните предложение, полученный ответ и значение для продаж. '
             'Для рекомендации назовите действие и полезный результат. Различите установленное, экспертную оценку и предполагаемый эффект.', '',
             'Стандарт: весь предоставленный содержательный состав выбранных карточек изучается полностью. '
             'Явный узкий вопрос определяет углубление, а не разрешение на поверхностный просмотр. Незавершённая работа остаётся промежуточной.', '',
             '## Область фактического просмотра', '',
             '| Снимок | Заявленная область | Фактически изучено | Непрочитанное и последствие |', '|---|---|---|---|']
    for id in capture_ids:
        intro.append('| ' + id + ' | Весь предоставленный содержательный состав; предмет углубления по заданию | Не зафиксировано аналитиком | Указать после просмотра |')
    intro += ['', 'Не найденное в одном кадре не считается отсутствующим во всём комплекте. '
              'Заявление функции, показ механизма, работоспособность и полученный результат различаются.', '']
    lines = ['- Работа: ' + run_id, '- Дата подготовки: ' + stamp(), '- Стандарт: полный предметный разбор', '- Вид выхода: ' + PRESETS[result],
             '- Применённые правила: '+str(release.get('ruleset') or 'Рабочие правила'),
             '',
             '## Материалы и область', '', '| Снимок | Паспорт и контрольная сумма | Исходники |', '|---|---|---|']
    for id in capture_ids:
        row, c = available[id]
        p = Path(row['passport_path'])
        lines.append('| ' + id + ' | [' + str(c['change_number']) + '](' + href(p, doc) + '), SHA-256 ' + digest(root / p) + ' | [Открыть](' + href(capture_page(c), doc) + ') |')
    lines += ['', 'Просмотренную область и способ чтения фиксирует аналитик. Состав не означает, что все материалы уже прочитаны.', '', '## Методы']
    for method in methods:
        target = Path('00_Методика/Методы и разметка.html')
        lines.append('- [' + method + '](' + href(target, doc) + '#' + method.lower() + ').')
    lines += ['', '## Применённые правила']
    for rule in frozen_rules:lines.append('- ['+rule['name']+' на момент подготовки]('+href(Path(rule['path']),doc)+'), SHA-256 '+rule['sha256'])
    materials=[]
    for id in capture_ids:
        for material in sorted(available[id][1]['materials'],key=lambda m:m['relative_file']):materials.append((id,material))
    record=dict(form='ANALYST_WORK',capture_ids=capture_ids,methods=methods,material_count=len(materials),ruleset=release.get('ruleset'),tool_format=FORMAT_ID)
    evidence = lines + ['', '<!-- ANALYST_WORK '+json.dumps(record,ensure_ascii=False,separators=(',',':'))+' -->', '',
                        '## Учёт всего предоставленного состава', '',
                        'В столбце «Обработка» укажите ровно «Изучено» или «Производное». Для «Производное» ссылка F0000 на изученный оригинал и пояснение помещаются в столбец содержательной опоры. Нечитаемый материал сохраняется с ограничением и не закрывает полный разбор. '
                        'Неизученное не закрывает полный разбор. Учёт не заменяет содержательного анализа.', '',
                        '| Код | Обработка | Источник и снимок | Содержательная опора или ограничение |','|---|---|---|---|']
    for n,(id,material) in enumerate(materials,1):
        label=Path(material['relative_file']).name.replace('|','\\|')
        evidence.append('| F'+str(n).zfill(4)+' | Не изучено | ['+label+']('+href(Path(material['relative_file']),doc)+') '+id+' | Заполнить после чтения |')
    evidence += ['', '## Исходный договор сравнения', '', '```json', json_text(original_comparison).strip(), '```', '', '## Сравнение', '', json_text(compare).strip()]
    labels={m.group(1):m.group(2) for m in re.finditer(r'^### (EA-\d{2})\. (.+)$',(root/'00_Методика/Методы и разметка.md').read_text(),re.M)} if (root/'00_Методика/Методы и разметка.md').is_file() else {}
    lines = intro + task + ['## Карта предложения', '',
              'Разберите задачу и роли, товар и услугу, варианты, деньги, ресурсы, периоды, условия и путь после покупки. '
              'Сохраните существенные связи; неопределённость одного расчёта не отменяет доступный анализ.', '',
              '## Предметные направления', '',
              'Примените процедуру к конкретным единицам, отношениям и критериям. '
              'Состояния: Разобрано; Ограничено с указанием выполненного и недостающего; Неприменимо с предметным основанием. '
              'Объясните достаточность выбранных направлений для поручения.', '',
              '| Направление | Состояние | Что разобрано и где находятся основания |','|---|---|---|']
    for method in methods:lines.append('| '+method+' | Не разобрано | Заполнить после применения процедуры |')
    for method in methods:
        lines += ['', '### '+method+'. '+labels.get(method,'Предметный разбор'), '',
                  'Разверните объяснение по вопросу этого направления: предмет и контекст; конкретное наблюдение с источником; промежуточная связь с трактовкой; критерий и существенная альтернатива, если она меняет решение; обоснованный вывод и действие. Для образа исследуйте знаки, роли и отношения с текстом; для композиции и понимания покажите группировку и необходимые операции. Название подхода, краткая оценка и учёт файлов не заменяют разбора. Порядок: 00_Методика/Глубокий разбор сообщений.md; изложение: 00_Методика/Как писать развернутый отчёт.md.']
    lines += ['', '## Наблюдения и заключение', '',
              'ИИ-агент выполняет разбор и объясняет опоры, переходы, существенные альтернативы, вывод и ответ на вопрос. Аналитик руководит, оценивает основания и принимает решение. Автоматическая заготовка не является готовой экспертной оценкой. Основные объяснения пишутся связной прозой; таблица ниже служит точной сверке и не заменяет их.', '',
              '| Опора | Точное утверждение и область | Вид вывода | Сила основания и предел | Последствие для решения |', '|---|---|---|---|---|',
              '| Заполнить по вопросу | Сохранить нужные роль, ветвь, период, условие и единицу | Наблюдение / трактовка / оценка / гипотеза | Достаточно для названного вывода / ограничено / недостаточно; причина | Что сохранить, изменить или пока не утверждать |', '',
              'Критерий и существенная альтернатива раскрываются при оценке или интерпретации. '
              'Укажите, что изменит вывод; повтор источника в нескольких направлениях не даёт независимого подтверждения.']
    lines += ['', '## Целостный синтез', '',
              'Объясните взаимодействие предложения, текста, образов, композиции, последовательности и канала по вопросу работы. '
              'Свяжите сильные решения и возможности с продажей. Перечень наблюдений не заменяет синтеза.']
    if result in ['RECOMMENDATION', 'IMPLEMENTATION_PLAN']:
        lines += ['', '## Рекомендация и механизм продажи', '', 'Нужный результат → основание → изменение → предполагаемое действие → применимость → приоритет. Эффект и его размер имеют фактический статус.']
        lines += ['', 'Для нескольких действий: ACT-01, ACT-02; приоритет и предварительное условие отдельно. Свяжите изменение и версию с механизмом, событием, опорой и владельцем. Уточнение сохраняет ID, новое вмешательство имеет новый ID и связь. Новый ответ сохраняет прежний вывод и объясняет пересмотр.']
        lines += ['', '### Выпускная сверка ключевых рекомендаций', '',
                  'Для основных изменений сопоставьте итоговый краткий текст с источником. Условие остаётся у своего обещания; '
                  'денежный предмет не заменяется временем, отдельная ветвь другой ветвью. '
                  'Для шага укажите, что уже должно произойти и где действие доступно. Сохраните цельную постановку и доступ к исходам при повторном использовании разбора.', '',
                  '| Действие | Что именно обещаем, кому и при каком условии | Опора и точная короткая формулировка | Доступный шаг, момент и нужное событие |',
                  '|---|---|---|---|',
                  '| Заполнить для центральной рекомендации | Роль, ветвь, сумма/ресурс, период, отрицание и условие | Файл и короткая цитата; кодовые строки сохраняются буквально | Что уже доступно, что проверяют и когда; неизвестное ограничивает свой шаг |']
    if result == 'IMPLEMENTATION_PLAN':
        lines += ['', '## Реализация и приёмка', '', 'Владелец, подтверждённые факты, версия, зависимости и критерий приёмки. Тест и задание агентству выделяются по запросу; реальные запуски этой записью не разрешаются.']
    lines += ['', '## Приёмка глубины', '',
              'Эксперт проверяет весь состав, восстановленное предложение, предметность направлений, синтез, альтернативы, '
              'силу оснований и соответствие поручению. check-work проверяет только учёт и не завершает анализ.', '',
              '## Справочная часть: состав и правила', ''] + evidence
    path = inside(root, str(doc))
    if path.exists():raise FolderError('Рабочий файл уже существует.')
    atomic(path, '\n'.join(lines) + '\n')
    try:
        writer=Writer(root);put_work_index(root,writer);writer.apply(writer.state.get('passport_checks',{}))
    except FolderError as e:raise FolderError('Разбор сохранён '+doc.as_posix()+'; список работ не обновлён: '+str(e)) from e
    return dict(run_id=run_id, file=doc.as_posix(), html=doc.with_suffix('.html').as_posix(), result=result, comparison=compare)


def verify(root):
    root = Path(root).resolve()
    errors = []
    try:
        edition(root, check_hashes=True)
        items = captures(root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return dict(status='FAIL', errors=[str(error)])
    verified = unverified = 0
    for row, record in items:
        try:
            validate_capture(root, record, check_files=True)
            validate_provenance(root, record)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(str(error))
        verified += sum(x['integrity_state'] == 'VERIFIED' for x in record['materials'])
        unverified += sum(x['integrity_state'] != 'VERIFIED' for x in record['materials'])
    try:
        seller_labels(items, root)
    except FolderError as error:
        errors.append(str(error))
    state = read_json(root / STATE, {})
    for row, record in items:
        check = state.get('passport_checks', {}).get(record['capture_id'])
        if not check or check['sha256'] != digest(inside(root, row['passport_path'])) or check['change_number'] != record['change_number']:
            errors.append('Не обновлено представление паспорта: ' + record['capture_id'])
    for path in (root / '03_Работа аналитиков/Основания заданий').glob('*.txt'):
        if path.stem != digest(path):
            errors.append('Изменена опора задания: ' + path.name)
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
        def handle_starttag(self, tag, attrs):
            self.links.extend(value for key, value in attrs if key in ['href', 'src'] and value)
    count = 0
    for name, checksum in state.get('files', {}).items():
        path = inside(root, name)
        if not path.is_file() or digest(path) != checksum:
            errors.append('Изменён создаваемый файл: ' + name)
            continue
        if path.suffix == '.html':
            parser = Links()
            parser.feed(path.read_text())
            for url in parser.links:
                if url.startswith('#'):
                    continue
                count += 1
                if re.match(r'^[A-Za-z][\w+.-]*:', url):
                    errors.append('Внешний переход: ' + name)
                    continue
                target = (path.parent / unquote(url.split('#', 1)[0])).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    errors.append('Недоступный переход: ' + name + ':' + url)
    return dict(status='PASS' if not errors else 'FAIL', cards=len({x['card_id'] for _, x in items}), captures=len(items), verified_materials=verified, unverified_materials=unverified, checked_links=count, errors=errors, scope='Сохранность, учёт и локальные переходы. Экспертный смысл и эффект проверяются отдельно.')


def menu(root):
    while True:
        print('\nПапка аналитика: 1 принять материалы; 2 включить поступление; 3 подготовить глубокий разбор; 4 обновить; 5 проверить базу; 6 проверить учёт и обновить HTML работы; 0 выйти')
        choice = input('Действие: ').strip()
        if choice == '0':return
        try:
            if choice == '1':
                source = input('Путь к папке материалов: ').strip().strip('"')
                metadata = {k: input(label + ' (пусто = не установлено): ').strip() or None for k,label in [('platform','Площадка'),('external_card_id','Номер карточки'),('brand','Заявленный бренд'),('seller_display_name','Точное имя продавца'),('observed_at','Дата наблюдения'),('title','Заголовок')]}
                result = prepare(root, source, metadata)
                print('Создано поступление', result['intake_id'], '. Уточните Приём.json и включите командой 2.')
            elif choice == '2':print(json_text(publish(root, input('Номер поступления: ').strip())))
            elif choice == '3':
                ids = [s.strip() for s in input('Номера карточек или точные ID снимков через запятую: ').split(',') if s.strip()]
                ids = resolve_capture_refs(root,ids,interactive=True)
                question = input('Вопрос: ')
                result = input('Выход: 1 заключение, 2 рекомендация, 3 реализация; Enter = рекомендация для продаж: ').strip() or '2'
                book=root/'00_Методика/Методы и разметка.md'
                if book.is_file():
                    for code,label in re.findall(r'^### (EA-\d{2})\. (.+)$',book.read_text(),re.M):print(code[3:]+': '+label)
                selected=input('Направления: Enter = все 15; либо номера/коды через запятую по явному заданию: ')
                methods=[('EA-'+s.strip().zfill(2)) if s.strip().isdigit() else s.strip() for s in selected.split(',') if s.strip()]
                made=new_work(root,question,ids,{'1':'ANALYSIS','2':'RECOMMENDATION','3':'IMPLEMENTATION_PLAN'}.get(result,'ANALYSIS'),methods)
                print('Работа подготовлена, анализ ещё не выполнен.\nРедактировать: '+str(root/made['file'])+'\nЧитать: '+str(root/made['html'])+'\nИзучите весь состав, выполните предметные процедуры и синтез. Затем проверьте учёт работы.')
            elif choice == '4':print(json_text(refresh(root)))
            elif choice == '5':print(json_text(verify(root)))
            elif choice == '6':
                relative=input('Относительный путь к Разбор.md внутри папки: ').strip().strip('"')
                print(json_text(review_work(root,relative)));render_work(root,relative)
        except (FolderError, OSError, ValueError) as e:print('Не завершено:', e)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Локальная папка аналитика. Стандартный Python, без сервера и пакетов.')
    default_root = Path(__file__).resolve().parents[1]
    if Path(__file__).parent.name == 'scripts' and not argv and '--root' not in sys.argv:
        raise FolderError('Для исходного инструмента нужен явный --root; в переданной папке запустите 00_Сервис/analyst_folder.py.')
    parser.add_argument('--root', type=Path, default=default_root)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('refresh');sub.add_parser('check');sub.add_parser('init')
    prep = sub.add_parser('prepare');prep.add_argument('source', type=Path);prep.add_argument('--metadata', type=Path)
    pub = sub.add_parser('publish');pub.add_argument('intake_id');pub.add_argument('--defer-integrity', action='store_true')
    work = sub.add_parser('work');work.add_argument('--question', required=True);work.add_argument('--captures', required=True);work.add_argument('--methods', default='');work.add_argument('--result', choices=list(PRESETS), default='ANALYSIS');work.add_argument('--comparison', type=Path);work.add_argument('--name');work.add_argument('--task',type=Path,help='Необязательная постановка JSON; смысловая запись будет только в Разбор.md')
    inspect=sub.add_parser('check-work');inspect.add_argument('file')
    render=sub.add_parser('render-work', help='Создать HTML основного Разбор.md с договором работы', description='Создаёт HTML основного Разбор.md, подготовленного командой work. Отдельные V0/V1, задание и проект без договора работы сохраняются в Markdown; включите нужные разделы в основной разбор.');render.add_argument('file', metavar='Разбор.md', help='Путь к основному файлу со служебным договором work')
    find=sub.add_parser('find',help='Найти снимки и точные пути без вывода всего HTML');find.add_argument('query');find.add_argument('--limit',type=int,default=20)
    show=sub.add_parser('show',help='Показать один точный снимок, формат паспорта и первичные пути');show.add_argument('capture')
    editing=sub.add_parser('check-edit',help='Сверить буквальные строки до/после деловой редактуры; смысл проверяется отдельно');editing.add_argument('original');editing.add_argument('edited')
    edit = sub.add_parser('amend');edit.add_argument('capture_id');edit.add_argument('--metadata', type=Path, required=True);edit.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command is None:return menu(root)
        if args.command in ['init','refresh']:result=refresh(root)
        elif args.command=='check':result=verify(root)
        elif args.command=='prepare':result=prepare(root,args.source,read_json(args.metadata) if args.metadata else None)
        elif args.command=='publish':result=publish(root,args.intake_id,not args.defer_integrity)
        elif args.command=='work':result=new_work(root,args.question,[x.strip() for x in args.captures.split(',')],args.result,[x.strip() for x in args.methods.split(',') if x.strip()],read_json(args.comparison) if args.comparison else None,args.name,task_contract=read_json(args.task) if args.task else None)
        elif args.command=='check-work':result=review_work(root,args.file)
        elif args.command=='render-work':result=render_work(root,args.file)
        elif args.command=='find':result=find_captures(root,args.query,args.limit)
        elif args.command=='show':result=show_capture(root,args.capture)
        elif args.command=='check-edit':result=check_edit(root,args.original,args.edited)
        else:result=amend(root,args.capture_id,read_json(args.metadata),args.reason)
        print(json_text(result))
        return 1 if result.get('status') in ['FAIL','IDENTITY_PENDING','REVIEW_INCOMPLETE','NO_REVIEW_RECORD','LITERALS_CHANGED'] else 0
    except (FolderError, OSError, ValueError, KeyError) as e:
        print(json_text(dict(status='NOT_COMPLETED',reason=str(e))),file=sys.stderr)
        return 1


def reader_data(value, field=''):
    labels = {
        'state':'Известность значения', 'value':'Значение', 'basis':'Основание', 'scope':'К чему относится',
        'source_locator':'Где находится опора', 'reason':'Причина ограничения',
        'record_type':'Вид записи', 'intake_id':'Ссылка на поступление', 'card_id':'Ссылка на карточку',
        'capture_id':'Ссылка на наблюдение', 'intake_status':'Состояние приёма',
        'registered_at':'Когда материал принят в базу', 'observed_at':'Когда наблюдалась карточка',
        'platform':'Площадка', 'external_card_id':'Номер карточки на площадке',
        'seller_display_name':'Продавец, как он назван в источнике', 'seller_label_code':'Служебная ссылка на имя продавца',
        'brand':'Заявленный бренд', 'title':'Заголовок источника', 'source_origin':'Происхождение материала',
        'actual_relative_path':'Где хранится основной комплект', 'mission':'Задача покупателя в описании комплекта',
        'offer_model':'Как устроено предложение', 'collection_region':'Регион наблюдения',
        'materials':'Состав файлов', 'data_format':'Формат служебной записи', 'change_number':'Номер уточнения сведений',
        'coverage_scope':'Какую область охватывает комплект', 'source_note':'Пояснение к источнику',
        'attributes':'Признаки и контекст', 'metadata_files':'Дополнительные фрагменты источника',
        'placement_id':'Ссылка на размещение', 'surface':'Часть карточки', 'position':'Порядок',
        'relative_file':'Адрес файла', 'integrity_state':'Сверка сохранности', 'sha256':'Контрольная сумма файла',
        'bytes':'Размер в байтах', 'preliminary_classification':'Предварительное описание для подбора',
        'classification_status':'Статус предварительного описания', 'channel':'Площадка и участники продажи',
        'legal_party':'Юридическая сторона', 'shop_type':'Тип магазина', 'brand_owner':'Владелец бренда',
        'distributor_status':'Статус дистрибутора', 'budget':'Бюджет', 'source_url':'Адрес исходной страницы',
        'task_focus':'Предполагаемая задача', 'bundle_focus':'Тип комплекта', 'configuration_focus':'Конфигурация',
        'period_focus':'Период предложения', 'group_id':'Ссылка на группу', 'group_name':'Название группы',
        'device_claims':'Что заявлено об устройствах', 'device_claims_status':'Основание заявлений об устройствах',
        'money_roles':'К чему относятся денежные суммы', 'money_review_scope':'Область проверки денег',
        'porting_role':'Что сказано о переносе номера', 'geo_claims':'Территориальные заявления',
        'targeted_evidence':'Выбранные опоры', 'review_note':'Пояснение проверившего', 'flags':'Отмеченные особенности',
        'legal_party_id':'Ссылка на юридическую сторону', 'authority_status':'Подтверждение полномочий',
        'commercial_readiness':'Готовность коммерческого вывода', 'content_effect':'Основание эффекта материала',
        'market_row_present':'Есть ли строка коммерческого отчёта', 'promotion_row_present':'Есть ли сведения о продвижении',
        'routing_support':'Основания отнесения к группе', 'description':'Описание', 'name':'Название',
        'file':'Файл', 'source':'Источник', 'note':'Пояснение', 'currency':'Денежная единица',
        'amount':'Сумма', 'price':'Цена', 'rating':'Оценка', 'reviews_count':'Число отзывов',
        'collected_at':'Время сохранённого наблюдения', 'sku':'Номер товара в источнике', 'seller':'Продавец',
    }
    codes = {
        'KNOWN':'Значение указано', 'UNKNOWN':'Не установлено', 'NOT_OBSERVED':'В данной проверке не обнаружено',
        'NOT_APPLICABLE':'Не относится к этому вопросу', 'UNAVAILABLE':'Недоступно для этой проверки',
        'SOURCE_CLAIM':'Заявление источника', 'EXPERT_INTERPRETATION':'Экспертная трактовка',
        'ACCEPTED':'Принято', 'ACCEPTED_WITH_LIMITS':'Принято с указанными ограничениями',
        'RECEIVED':'Материалы получены', 'IDENTITY_PENDING':'Идентичность ещё не установлена',
        'VERIFIED_HASH':'Контрольная сумма совпала', 'VERIFIED':'Сверка выполнена',
        'CAPTURE':'Запись сохранённого наблюдения', 'GALLERY':'Галерея', 'RICH':'Подробный блок',
        'PRELIMINARY_CONTENT_ROUTE_NOT_SALES_RANK':'Предварительный подбор по содержанию, не место по продажам',
    }
    if isinstance(value,dict):
        result='<dl>'
        for key,item in value.items():
            label=labels.get(key, key if re.search('[А-Яа-яЁё]',str(key)) else 'Поле источника «'+str(key)+'»')
            result+='<dt><strong>'+escape(label)+'</strong></dt><dd>'+reader_data(item,str(key))+'</dd>'
        return result+'</dl>'
    if isinstance(value,list):
        return '<ol>'+''.join('<li>'+reader_data(item,field)+'</li>' for item in value)+'</ol>' if value else '<p>В записи приведён пустой список. Сам по себе он не доказывает отсутствия объекта вне этого источника.</p>'
    if value is None:return '<span>Значение не указано; это не ноль и не установленное отсутствие.</span>'
    if isinstance(value,bool):return 'Да' if value else 'Нет'
    if value=='':return '<span>В источнике задана пустая строка.</span>'
    text=str(value)
    if field=='surface' and text in SURFACE_LABELS:return escape(SURFACE_LABELS[text])+' <small>('+escape(text)+')</small>'
    if field in {'state','basis','intake_status','integrity_state','classification_status','record_type','data_format','legal_party','shop_type','brand_owner','distributor_status','budget','authority_status','commercial_readiness','content_effect','device_claims_status','porting_role'} and text in codes:
        return escape(codes[text])+' <small>('+escape(text)+')</small>'
    if field=='currency' and text=='RUB':return 'Российский рубль (RUB)'
    return '<span style="white-space:pre-wrap;overflow-wrap:anywhere">'+escape(text)+'</span>'

def reader_passport(c):
    document=Path(c['actual_relative_path'])/'Паспорт.md'
    text='# Описание сохранённого наблюдения\n\n'+shown(c,'title')+'\n\nЭто описание полученного материала. Оно не подтверждает действительность услуги сегодня и не является готовым анализом.\n\n'
    for label,value in [('Номер карточки на площадке',shown(c,'external_card_id')),('Площадка',str(c.get('platform') or 'Не установлено')),('Заявленный бренд',shown(c,'brand')),('Продавец, как назван в источнике',shown(c,'seller_display_name')),('Время наблюдения, не дата приёма',shown(c,'observed_at')),('Область комплекта',str(c.get('coverage_scope') or 'Не установлено')),('Происхождение',str(c.get('source_origin') or 'Не установлено')),('Состояние приёма',STATUS_LABELS[c['intake_status']])]:
        text+='- '+label+': '+value+'\n'
    text+='\nБренд и имя продавца сами по себе не устанавливают компанию и полномочия. Неизвестное время не заменяется датой другого наблюдения. Для уточнения или анализа дайте поручение агенту обычным языком.\n\n## Состав источников\n\n'
    for material in c['materials']:
        path=Path(material['relative_file']);status={'VERIFIED_HASH':'контрольная сумма совпала','VERIFIED':'сверка выполнена'}.get(material['integrity_state'],'точное состояние сверки: '+material['integrity_state'])
        text+='- ['+path.name+']('+href(path,document)+'): '+SURFACE_LABELS[material['surface']]+'; '+status+'.\n'
    text+='\nСовпадение файла подтверждает сохранность, а не качество анализа или истинность обещания.\n\n## Точные ссылки для сверки\n\n- Карточка: '+c['card_id']+'\n- Наблюдение: '+c['capture_id']+'\n- Поступление: '+c['intake_id']+'\n- Номер уточнения сведений: '+str(c['change_number'])+'\n\nЭти обозначения сохраняются для восстановления источника. Вводить или запоминать их руководителю не требуется.\n'
    return text

def report_markdown(text, document=None):
    """Keep source images beside the explanation, with access to full originals."""
    figures=[]
    def take(match):
        marker='REPORTFIGURE'+str(len(figures)).zfill(4)
        figures.append((marker,match.group(1),match.group(2)))
        return '\n\n'+marker+'\n\n'
    lines=[];code_marker=None
    for line in text.splitlines(keepends=True):
        fence=fence_marker(line)
        if code_marker:
            lines.append(line)
            if fence and fence.group(1)[0]==code_marker[0] and len(fence.group(1))>=len(code_marker) and not fence.group(2).strip():code_marker=None
            continue
        if fence:
            code_marker=fence.group(1);lines.append(line);continue
        lines.append(re.sub(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$',take,line))
    body=markdown_html(''.join(lines),document=document)
    for marker,label,url in figures:
        body=body.replace('<p>'+marker+'</p>','<figure><a href="'+escape(url)+'"><img loading="lazy" style="max-width:620px;width:100%;height:auto" src="'+escape(url)+'" alt="'+escape(label)+'"></a><figcaption>'+escape(label)+'; открыть оригинал в полном размере</figcaption></figure>')
    return body

def fence_marker(line):
    return re.fullmatch(r' {0,3}(`{3,}|~{3,})([^\r\n]*)', line.rstrip('\r\n'))

if __name__ == '__main__':
    raise SystemExit(main())
