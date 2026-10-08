"""Bounded, resumable ClimateSERV client. No widgets are used by workers."""
import datetime as dt
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal


class ClimateServChirpsTask(QgsTask):
    status = pyqtSignal(str)
    BASE = 'https://climateserv.servirglobal.net/api/'
    BLOCK_TIMEOUT = 600
    TOTAL_TIMEOUT = 1200

    def __init__(self, y0, y1, geometry, callback, cache_dir):
        super().__init__('CHIRPS ClimateSERV', QgsTask.CanCancel)
        self.y0, self.y1, self.geometry = y0, y1, geometry
        self.callback, self.cache_dir = callback, cache_dir
        self.data, self.error = [], ''
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()
        super().cancel()

    def check(self):
        if self.stop.is_set() or self.isCanceled():
            raise InterruptedError('Descarga cancelada. Los años completos se conservaron en caché.')
        if time.monotonic()>getattr(self,'deadline',float('inf')):
            raise TimeoutError('Se alcanzaron 20 minutos de descarga. Vuelva a iniciar para reanudar con los años ya guardados.')

    def request(self, method, params, post=False):
        # Submission is deliberately not retried: an uncertain POST may create a job.
        payload = urllib.parse.urlencode(params).encode('utf-8')
        url = self.BASE + method + '/'
        if not post:
            url += '?' + payload.decode('utf-8')
        for attempt in range(1 if post else 3):
            self.check()
            req = urllib.request.Request(url, data=payload if post else None,
                headers={'User-Agent': 'DROPS-Hydro-MX/0.4.10', 'Accept': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    result = json.loads(response.read().decode('utf-8'))
                self.check()
                return result
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if post or attempt == 2 or (isinstance(exc, urllib.error.HTTPError) and exc.code not in (429,500,502,503,504)):
                    raise RuntimeError(f'{method}: {exc}') from exc
                self.status.emit(f'Error temporal de conexión; reintento {attempt+1}/2…')
                self.stop.wait(2 ** (attempt+1))

    @staticmethod
    def parse_rows(response, year):
        if not isinstance(response, dict) or not isinstance(response.get('data'), list):
            raise ValueError('ClimateSERV no devolvió una serie diaria válida.')
        rows = {}
        for item in response['data']:
            try:
                if item.get('epochTime') is not None:
                    date = dt.datetime.fromtimestamp(float(item['epochTime']), dt.timezone.utc).date()
                else:
                    # ClimateSERV examples use month/day/year.
                    date = dt.datetime.strptime(item['date'], '%m/%d/%Y').date()
                value = item['value']
                value = float(value['avg'] if isinstance(value, dict) else value)
                if date.year == year and math.isfinite(value) and value >= 0:
                    if date in rows and rows[date] != value:
                        raise ValueError('Valores distintos para una misma fecha.')
                    rows[date] = value
            except (KeyError, TypeError, OverflowError):
                continue
        expected = (dt.date(year+1,1,1)-dt.date(year,1,1)).days
        if len(rows) != expected:
            raise ValueError(f'{year}: recibidos {len(rows)}/{expected} días válidos. No se usarán años incompletos para extremos.')
        return sorted(rows.items())

    def run(self):
        try:
            self.deadline=time.monotonic()+self.TOTAL_TIMEOUT
            os.makedirs(self.cache_dir, exist_ok=True)
            for index, year in enumerate(range(self.y0, self.y1+1)):
                self.check()
                params = dict(datatype=0, begintime=f'01/01/{year}', endtime=f'12/31/{year}',
                    intervaltype=0, operationtype=5, geometry=json.dumps(self.geometry,sort_keys=True,separators=(',',':')))
                key = hashlib.sha256(json.dumps([self.BASE,params],sort_keys=True).encode()).hexdigest()
                path = os.path.join(self.cache_dir,key+'.json')
                job_path = os.path.join(self.cache_dir,key+'.job.json')
                rows = None
                if os.path.exists(path):
                    try:
                        with open(path,encoding='utf-8') as file: rows=self.parse_rows(json.load(file),year)
                        self.status.emit(f'{year}: recuperado de caché')
                    except (ValueError, OSError): rows=None
                if rows is None:
                    jid = None
                    if os.path.exists(job_path):
                        try:
                            with open(job_path,encoding='utf-8') as file: saved=json.load(file)
                            if time.time()-saved['created'] < 86400: jid=str(uuid.UUID(saved['id']))
                        except (ValueError, KeyError, TypeError, OSError): pass
                    if jid is None:
                        self.status.emit(f'{year}: enviando solicitud anual…')
                        job = self.request('submitDataRequest',params,True)
                        try:
                            jid = str(uuid.UUID(str(job[0] if isinstance(job,list) and job else job)))
                        except (ValueError, TypeError) as exc:
                            raise RuntimeError(f'ClimateSERV no aceptó la solicitud de {year}: {str(job)[:300]}') from exc
                        with open(job_path,'w',encoding='utf-8') as file: json.dump({'id':jid,'created':time.time()},file)
                    started = time.monotonic()
                    while True:
                        self.check()
                        if time.monotonic()-started > self.BLOCK_TIMEOUT:
                            raise TimeoutError(f'{year}: ClimateSERV excedió 10 minutos. ID {jid}. Puede reanudar la descarga.')
                        progress = self.request('getDataRequestProgress',{'id':jid})
                        value = float(progress[0] if isinstance(progress,list) and progress else progress)
                        if not math.isfinite(value) or value < 0:
                            if os.path.exists(job_path): os.remove(job_path)
                            raise RuntimeError(f'ClimateSERV rechazó el trabajo {jid} ({year}).')
                        self.status.emit(f'{year}: {value:.0f}% | {int(time.monotonic()-started)} s | ID {jid}')
                        self.setProgress(100*(index+min(value,99)/100)/(self.y1-self.y0+1))
                        if value >= 100: break
                        self.stop.wait(3)
                    response = self.request('getDataFromRequest',{'id':jid})
                    rows = self.parse_rows(response,year)
                    temp=path+'.tmp'
                    with open(temp,'w',encoding='utf-8') as file: json.dump(response,file)
                    os.replace(temp,path)
                    if os.path.exists(job_path): os.remove(job_path)
                self.data.extend(rows)
                self.setProgress(100*(index+1)/(self.y1-self.y0+1))
            return bool(self.data)
        except Exception as exc:
            self.error=str(exc)
            return False

    def finished(self, result):
        if self.callback:
            self.callback(bool(result),self.data,self.error)
