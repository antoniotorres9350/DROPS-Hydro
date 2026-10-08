import json
import math
import os
import hashlib
from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal
from .hydro_models import export_csv


class FunctionTask(QgsTask):
    status=pyqtSignal(str)
    def __init__(self,name,function,callback):
        super().__init__(name,QgsTask.CanCancel)
        self.function,self.callback=function,callback; self.result=None; self.error=''
    def check(self):
        if self.isCanceled(): raise InterruptedError('Proceso cancelado.')
    def cancel(self):
        feedback=getattr(self,'processing_feedback',None)
        if feedback is not None: feedback.cancel()
        super().cancel()
    def run(self):
        try: self.result=self.function(self); self.check(); return True
        except Exception as exc: self.error=str(exc); return False
    def finished(self,ok):
        if self.callback: self.callback(bool(ok),self.result,self.error)


def grid_for_basin(wkt,epsg,resolution,max_cells=4000000):
    from osgeo import ogr,osr,gdal
    polygon=ogr.CreateGeometryFromWkt(wkt); xmin,xmax,ymin,ymax=polygon.GetEnvelope()
    res=float(max(resolution,math.sqrt((xmax-xmin)*(ymax-ymin)/max_cells)))
    nx=max(1,math.ceil((xmax-xmin)/res)); ny=max(1,math.ceil((ymax-ymin)/res))
    bounds=(xmin,ymax-ny*res,xmin+nx*res,ymax)
    srs=osr.SpatialReference(); srs.ImportFromEPSG(epsg)
    mask=gdal.GetDriverByName('MEM').Create('',nx,ny,1,gdal.GDT_Byte)
    mask.SetGeoTransform((xmin,res,0,ymax,0,-res)); mask.SetProjection(srs.ExportToWkt())
    ds=ogr.GetDriverByName('Memory').CreateDataSource(''); layer=ds.CreateLayer('mask',srs,ogr.wkbUnknown)
    ft=ogr.Feature(layer.GetLayerDefn()); ft.SetGeometry(polygon); layer.CreateFeature(ft)
    gdal.RasterizeLayer(mask,[1],layer,burn_values=[1])
    return mask,bounds,res


def write_raster(path,array,reference,nodata=-9999):
    from osgeo import gdal
    output=gdal.GetDriverByName('GTiff').Create(path,reference.RasterXSize,reference.RasterYSize,1,gdal.GDT_Float32,
        options=['COMPRESS=DEFLATE','TILED=YES'])
    output.SetProjection(reference.GetProjection()); output.SetGeoTransform(reference.GetGeoTransform())
    output.GetRasterBand(1).SetNoDataValue(nodata); output.GetRasterBand(1).WriteArray(array); output.FlushCache(); output=None


# Explicit AMC-II scenario crosswalk, not a direct observation of CN by satellite.
# Values follow the named TR-55 classes; wetlands/water use a saturated scenario.
CN_TABLE={10:('Woods good',30,55,70,77),20:('Brush good',30,48,65,73),
    30:('Grassland good',39,61,74,80),40:('Row crops straight good',67,78,85,89),
    50:('Built-up assumed impervious',98,98,98,98),60:('Bare fallow',77,86,91,94),
    80:('Open water',100,100,100,100),90:('Wetland assumed saturated',100,100,100,100),
    95:('Mangrove assumed saturated',100,100,100,100)}

# NatCap's documented COG reclassifies undrained dual HYSOGs groups to D.
HYSOGS_URL='https://data.naturalcapitalalliance.stanford.edu/download/global/HYSOGs250m/HYSOGs250m_Soil_Groups_reclassified.tif'


def cn_job(task,basin_wkt,epsg,soil_path,cover_path,worldcover,bbox_lonlat,output,resolution=30,table_path=''):
    from osgeo import gdal
    import numpy as np
    os.makedirs(output,exist_ok=True); task.check()
    automatic_soil=not soil_path
    if automatic_soil: soil_path='/vsicurl/'+HYSOGS_URL
    table=dict(CN_TABLE)
    if table_path:
        import csv
        with open(table_path,encoding='utf-8-sig') as file:
            table={int(row['class']):(row.get('description',''),*[float(row[c]) for c in 'ABCD']) for row in csv.DictReader(file)}
        if any(not 0<v<=100 for row in table.values() for v in row[1:]): raise ValueError('CN de la tabla fuera de (0,100].')
    sources=cover_path
    if worldcover:
        west,south,east,north=bbox_lonlat; sources=[]
        for lat in range(int(math.floor(south/3)*3),int(math.floor(north/3)*3)+1,3):
            for lon in range(int(math.floor(west/3)*3),int(math.floor(east/3)*3)+1,3):
                tile=f'{"N" if lat>=0 else "S"}{abs(lat):02d}{"E" if lon>=0 else "W"}{abs(lon):03d}'
                sources.append('/vsicurl/https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_'+tile+'_Map.tif')
    if not sources: raise ValueError('Seleccione una cobertura WorldCover o active WorldCover remoto.')
    mask,bounds,res=grid_for_basin(basin_wkt,epsg,resolution)
    inside=mask.GetRasterBand(1).ReadAsArray()>0
    if not inside.any(): raise ValueError('La cuadrícula no contiene celdas de cuenca.')
    old={k:gdal.GetThreadLocalConfigOption(k) for k in ('GDAL_HTTP_TIMEOUT','GDAL_HTTP_MAX_RETRY','GDAL_DISABLE_READDIR_ON_OPEN','CURL_CA_BUNDLE')}
    ca_file=None
    try:
        # Windows Python loads the OS trust store; QGIS's bundled curl CA file
        # may miss a locally trusted HTTPS inspection certificate. Keep TLS
        # verification enabled and honor any explicitly configured CA bundle.
        if os.name=='nt' and not gdal.GetConfigOption('CURL_CA_BUNDLE') and not gdal.GetConfigOption('SSL_CERT_FILE'):
            import ssl,tempfile
            roots=[]
            for store in ('ROOT','CA'):
                for certificate,encoding,trust in ssl.enum_certificates(store):
                    if encoding=='x509_asn' and (trust is True or '1.3.6.1.5.5.7.3.1' in trust):
                        roots.append(certificate)
            if roots:
                with tempfile.NamedTemporaryFile(mode='w',suffix='.pem',delete=False,encoding='ascii') as file:
                    ca_file=file.name
                    file.write(''.join(ssl.DER_cert_to_PEM_cert(cert) for cert in roots))
                gdal.SetThreadLocalConfigOption('CURL_CA_BUNDLE',ca_file)
        gdal.SetThreadLocalConfigOption('GDAL_HTTP_TIMEOUT','30'); gdal.SetThreadLocalConfigOption('GDAL_HTTP_MAX_RETRY','2')
        gdal.SetThreadLocalConfigOption('GDAL_DISABLE_READDIR_ON_OPEN','EMPTY_DIR')
        arrays=[]
        for label,source in [('Cobertura',sources),('Grupo de suelo',soil_path)]:
            task.check(); task.status.emit('Recortando '+label+'…')
            cache_dir=os.path.join(os.path.dirname(output),'_cn_cache'); os.makedirs(cache_dir,exist_ok=True)
            paths=source if isinstance(source,list) else [source]
            signature=[(p,os.path.getmtime(p),os.path.getsize(p)) if os.path.isfile(p) else (p,) for p in paths]
            key=hashlib.sha256(json.dumps([basin_wkt,epsg,bounds,res,signature]).encode()).hexdigest()
            cache_path=os.path.join(cache_dir,key+'.tif')
            cached=gdal.Open(cache_path) if os.path.isfile(cache_path) else None
            # The hash includes EPSG/grid; GeoTIFF may normalize WKT on disk.
            if cached is not None and cached.RasterXSize==mask.RasterXSize and cached.RasterYSize==mask.RasterYSize and cached.GetGeoTransform()==mask.GetGeoTransform() and cached.GetSpatialRef().IsSame(mask.GetSpatialRef()):
                arrays.append(cached.ReadAsArray()); cached=None; continue
            cached=None
            try:
                ds=gdal.Warp('',source,format='MEM',dstSRS=f'EPSG:{epsg}',outputBounds=bounds,
                    width=mask.RasterXSize,height=mask.RasterYSize,resampleAlg='near',dstNodata=255,outputType=gdal.GDT_Int16,
                    callback=lambda *args:not task.isCanceled())
            except Exception as exc:
                task.check()
                raise ValueError('No se pudo consultar '+label+'. Revise conexión y certificados, o reintente. Detalle: '+str(exc)) from exc
            if ds is None: raise ValueError('No se pudo leer '+label+'. Revise la conexión o reintente; puede usar un raster local opcional. No se inventará un grupo de suelo.')
            array=ds.ReadAsArray(); ds=None; task.check()
            array=np.where(inside,array,255)
            if not np.any(inside & (array!=255)): raise ValueError(label+' no contiene datos para esta cuenca.')
            pending=cache_path+'.part.tif'; write_raster(pending,array,mask,255)
            task.check(); os.replace(pending,cache_path)
            arrays.append(array)
        cover,soil=arrays
        soil=np.where(np.isin(soil,[11,12,13,14]),4,soil) # undrained dual groups
        soil_output=os.path.join(output,'grupo_hidrologico_cuenca.tif')
        cover_output=os.path.join(output,'cobertura_cuenca.tif')
        write_raster(soil_output,np.where(inside,soil,255),mask,255)
        write_raster(cover_output,np.where(inside,cover,255),mask,255)
        cn=np.full(inside.shape,-9999.,dtype=np.float32); records=[]
        for code,values in table.items():
            for group in range(1,5):
                selected=inside & (cover==code) & (soil==group); n=int(selected.sum())
                if n:
                    value=values[group]; cn[selected]=value
                    records.append(dict(class_code=code,description=values[0],HSG='ABCD'[group-1],CN=value,
                        area_km2=n*res*res/1e6,cells=n))
        valid=inside & (cn>0); coverage=float(valid.sum()/inside.sum())
        write_raster(os.path.join(output,'cn_cuenca.tif'),cn,mask)
        if coverage<0.95:
            raise ValueError(f'Solo {coverage:.1%} de la cuenca tiene CN válido. Revise cobertura, suelos y tabla de clases; no se asignará un CN medio.')
        mean=float(np.mean(cn[valid],dtype=np.float64))
        for row in records:
            row.update(CN_basin_mean=mean,coverage_fraction=coverage,resolution_m=res,AMC='II',dual_HSG='undrained D',
                cover_source='ESA WorldCover 2021 v200 remoto' if worldcover else cover_path,soil_source=soil_path)
        export_csv(os.path.join(output,'cn_areas.csv'),records)
        export_csv(os.path.join(output,'cn_crosswalk.csv'),[{'class':code,'description':row[0],'A':row[1],'B':row[2],'C':row[3],'D':row[4]} for code,row in table.items()])
        provenance=dict(soil_source=soil_path,soil_automatic=automatic_soil,
            soil_original_resolution_m=250 if automatic_soil else None,
            soil_citation='Ross et al. 2018, doi:10.3334/ORNLDAAC/1566; COG reclasificado por Natural Capital Alliance (CC BY 4.0)' if automatic_soil else 'Raster local',
            soil_dual_groups='D (no drenados)',cover_source=sources,analysis_resolution_m=res,
            mean_CN=mean,valid_coverage=coverage,AMC='II')
        with open(os.path.join(output,'fuentes_CN.json'),'w',encoding='utf-8') as file: json.dump(provenance,file,ensure_ascii=False,indent=2)
        return dict(mean=mean,coverage=coverage,resolution=res,raster=os.path.join(output,'cn_cuenca.tif'),rows=records,
            soil_raster=soil_output,cover_raster=cover_output,soil_automatic=automatic_soil)
    finally:
        for k,v in old.items(): gdal.SetThreadLocalConfigOption(k,v)
        if ca_file and os.path.exists(ca_file): os.remove(ca_file)
