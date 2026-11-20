"""Portable shared runtime. No credentials, API calls or writes on import."""
import json, os, re, ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen as _urlopen
WORK = Path(__file__).resolve().parent
ROOT = WORK.parents[1]
def load_runtime_ids():
    return json.loads((WORK/'configs/runtime_ids.json').read_text(encoding='utf-8'))['ids']
ACCOUNT_ID = json.loads((WORK/'configs/runtime_ids.json').read_text(encoding='utf-8'))['account_id']
def load_token():
    names = ('YANDEX_TASKS_TOKEN', 'TASKS_TOKEN', 'TOKEN')
    for name in names:
        if os.environ.get(name, '').strip():
            return os.environ[name].strip()
    for path in (ROOT/'.env', WORK/'.env'):
        if not path.exists(): continue
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            key, value = line.split('=', 1)
            if key.strip() in names:
                value = value.strip()
                if len(value)>1 and value[0]==value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if value: return value
    raise RuntimeError('Set YANDEX_TASKS_TOKEN in the environment or an untracked .env file.')
def urlopen(request, timeout=60):
    try:
        import certifi
    except ImportError:
        context = ssl.create_default_context()
    else:
        context = ssl.create_default_context(cafile=certifi.where())
    return _urlopen(request, timeout=timeout, context=context)

class CheckError(RuntimeError):
    """Проверка не пройдена: дальнейшие действия не выполняются."""

class APIError(CheckError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status

def require(condition, message):
    if not condition:
        raise CheckError(message)

def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)

class API:
    def __init__(self, token):
        require(bool(token) and '\n' not in token and '\r' not in token, 'Укажите TOKEN в .env или переменной окружения.')
        self.token = token

    def request(self, method, path, body=None, params=None):
        require(path.startswith(('/api/v1/', '/api/new/requester/')), 'Неожиданный путь API.')
        url = 'https://tasks.yandex.ru' + path
        if params:
            url += '?' + urlencode(params)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        request = Request(url, data=data, method=method, headers={
            'Authorization': 'OAuth ' + self.token,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read().decode('utf-8')
            return json.loads(raw) if raw else None
        except HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace').replace(self.token, '[TOKEN]')
            raise APIError('API {} {}: HTTP {}\n{}'.format(method, path, error.code, detail[:6000]), error.code) from None
        except (URLError, TimeoutError, OSError) as error:
            raise CheckError('Связь с API прервалась: {}. Повторите эту ячейку: сначала она проверит уже созданные объекты.'.format(type(error).__name__)) from None

    def get(self, path, **params):
        return self.request('GET', '/api/v1/' + path.lstrip('/'), params=params)

    def post(self, path, body=None, **params):
        return self.request('POST', '/api/v1/' + path.lstrip('/'), body=body, params=params)

    def all(self, path, **params):
        """Полная выгрузка с проверкой has_more, а не только первая страница."""
        result, seen = [], set()
        params = dict(params, sort='id', limit=50)
        while True:
            response = self.get(path, **params)
            require(isinstance(response, dict) and isinstance(response.get('items'), list), 'Неожиданный формат списка ' + path)
            items = response['items']
            for item in items:
                key = str(item['id'])
                require(key not in seen, 'API повторил запись при пагинации: ' + path)
                seen.add(key)
                result.append(item)
            if response.get('has_more') is False:
                return result
            require(response.get('has_more') is True and bool(items), 'В списке API отсутствует корректный has_more: ' + path)
            params['id_gt'] = str(items[-1]['id'])

def instruction_html(source):
    """В API передаётся фрагмент инструкции, без оболочки отдельного HTML-файла."""
    body=re.search(r'<body\b[^>]*>(.*?)</body\s*>',source,re.S|re.I)
    source=body.group(1) if body else source
    return re.sub(r'</?section\b[^>]*>','',source,flags=re.I).strip()
