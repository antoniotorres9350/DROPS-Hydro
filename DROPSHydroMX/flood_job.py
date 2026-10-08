"""SIMWE preprocessing and execution. No elevation-to-hydraulic-radius proxy."""
import json
import math
import os
from .spatial_jobs import grid_for_basin,write_raster
from .hydro_models import runoff_mm,export_csv


# Initial scenarios by broad WorldCover class, not measured or calibrated n.
MANNING_TABLE={10:('Arbolado',.12),20:('Matorral',.06),30:('Pastizal',.035),
    40:('Cultivos',.05),50:('Construido',.12),60:('Suelo desnudo',.025),
    70:('Nieve/hielo',.025),80:('Agua',.03),90:('Humedal herbaceo',.07),
    95:('Manglar',.12),100:('Musgos/liquenes',.04)}


def prepare_manning(task,mask,valid,params,output):
    """Align n to the solver grid; never fill missing roughness silently."""
    from osgeo import gdal
    import numpy as np
    mode=params.get('manning_mode','constant')
    table=dict(MANNING_TABLE); records=[]
    if mode not in ('constant','cover','raster'): raise ValueError('Modo Manning no reconocido.')
    if mode=='constant':
        values=np.full(valid.shape,float(params['manning']))
        origin='Valor uniforme del usuario'
    else:
        source_path=params.get('manning_raster' if mode=='raster' else 'manning_cover','')
        source=gdal.Open(source_path) if source_path else None
        if source is None or not source.GetProjection(): raise ValueError('Manning requiere un raster válido con CRS: '+source_path)
        gt=mask.GetGeoTransform(); bounds=(gt[0],gt[3]+mask.RasterYSize*gt[5],gt[0]+mask.RasterXSize*gt[1],gt[3])
        aligned=gdal.Warp('',source,format='MEM',dstSRS=mask.GetProjection(),outputBounds=bounds,
            width=mask.RasterXSize,height=mask.RasterYSize,resampleAlg='near',outputType=gdal.GDT_Float32,
            dstNodata=-9999,callback=lambda *args:not task.isCanceled())
        if aligned is None: task.check(); raise ValueError('No se pudo alinear el raster de Manning/cobertura.')
        data=aligned.ReadAsArray(); aligned=None; source=None
        origin=source_path
        if mode=='raster': values=data
        else:
            custom=params.get('manning_table','')
            if custom:
                import csv
                with open(custom,encoding='utf-8-sig') as file:
                    table={int(row['class']):(row.get('description',''),float(row['n'])) for row in csv.DictReader(file)}
            if not table or any(not math.isfinite(n) or not 0<n<=1 for _,n in table.values()):
                raise ValueError('La tabla Manning requiere valores finitos 0 < n ≤ 1.')
            values=np.full(valid.shape,-9999.,dtype=float)
            for code,(description,n) in table.items():
                selected=valid & (data==code); values[selected]=n
                records.append(dict(class_code=code,description=description,n=n,cells=int(selected.sum())))
            export_csv(os.path.join(output,'tabla_manning_aplicada.csv'),records)
    task.check()
    good=np.isfinite(values) & (values>0) & (values<=1)
    missing=int((valid & ~good).sum())
    if missing:
        raise ValueError(f'Manning sin valor válido en {missing} celdas del dominio. Complete la cobertura o tabla; no se sustituirán por un n arbitrario.')
    path=os.path.join(output,'manning_cuenca.tif')
    write_raster(path,np.where(valid,values,-9999),mask)
    ds=gdal.Open(path,gdal.GA_Update); ds.GetRasterBand(1).SetUnitType('s/m^(1/3)')
    ds.GetRasterBand(1).SetDescription('Coeficiente de rugosidad de Manning n'); ds=None
    return dict(path=path,mode=mode,source=origin,min=float(values[valid].min()),
        max=float(values[valid].max()),mean=float(values[valid].mean()),units='s/m^(1/3)')


def simulate_flood(task,dem_path,basin_wkt,epsg,output,resolution,params,algorithm):
    from osgeo import gdal
    import numpy as np
    import processing
    from qgis.core import QgsProcessingContext,QgsProcessingFeedback
    os.makedirs(output,exist_ok=True)
    context=QgsProcessingContext()
    class Feedback(QgsProcessingFeedback):
        def isCanceled(self): return task.isCanceled() or super().isCanceled()
        def setProgress(self,value): super().setProgress(value); task.setProgress(25+0.7*value)
    feedback=Feedback(); task.processing_feedback=feedback; log_path=os.path.join(output,'processing.log')
    try:
        task.check(); task.status.emit('Preparando MDE métrico y derivadas dx/dy…')
        mask,bounds,res=grid_for_basin(basin_wkt,epsg,resolution,max_cells=250000)
        # Buffer one cell so derivatives do not use artificial polygon-edge values.
        expanded=(bounds[0]-res,bounds[1]-res,bounds[2]+res,bounds[3]+res)
        source=gdal.Warp('',dem_path,format='MEM',dstSRS=f'EPSG:{epsg}',outputBounds=expanded,
            width=mask.RasterXSize+2,height=mask.RasterYSize+2,resampleAlg='bilinear',
            outputType=gdal.GDT_Float32,dstNodata=-9999,callback=lambda *args:not task.isCanceled())
        if source is None: raise ValueError('No se pudo preparar el MDE para SIMWE.')
        z=source.ReadAsArray().astype(float); z[z==-9999]=np.nan
        dy,dx=np.gradient(z,-res,res)
        elevation=z[1:-1,1:-1]; dx=dx[1:-1,1:-1]; dy=dy[1:-1,1:-1]
        inside=mask.ReadAsArray()>0
        valid=inside & np.isfinite(elevation) & np.isfinite(dx) & np.isfinite(dy)
        coverage=float(valid.sum()/max(inside.sum(),1))
        if coverage<0.95: raise ValueError(f'Cobertura válida de MDE/derivadas: {coverage:.1%}; se requiere al menos 95 %.')
        paths={}
        for name,array in [('elevation',elevation),('dx',dx),('dy',dy)]:
            paths[name]=os.path.join(output,name+'.tif')
            write_raster(paths[name],np.where(valid,array,-9999),mask)
        task.status.emit('Preparando rugosidad Manning en la grilla de simulación…')
        roughness=prepare_manning(task,mask,valid,params,output)
        excess=runoff_mm(params['rain_mm'],params['CN_mean'])
        minutes=max(1,int(round(params['duration_h']*60)))
        # Make integrated rainfall excess equal to the hydrologic runoff depth.
        intensity=excess/(minutes/60)
        outputs={name:os.path.join(output,name+'.tif') for name in ('depth','discharge','error')}
        task.check(); task.setProgress(25)
        if excess==0:
            for path in outputs.values(): write_raster(path,np.where(valid,0,-9999),mask)
            result=outputs
        else:
            task.status.emit('SIMWE: lluvia excedente uniforme; cálculo en segundo plano…')
            parameters=dict(paths,**outputs,rain_value=intensity,infil_value=0.0,
                niterations=minutes,output_step=minutes,nwalkers=max(1000,2*int(valid.sum())),
                GRASS_REGION_CELLSIZE_PARAMETER=res,
                GRASS_REGION_PARAMETER=f'{bounds[0]},{bounds[2]},{bounds[1]},{bounds[3]} [EPSG:{epsg}]')
            if roughness['mode']=='constant': parameters['man_value']=params['manning']
            else: parameters['man']=roughness['path']
            result=processing.run(algorithm,parameters,context=context,feedback=feedback)
        task.check()
        summary={}
        for key,path in outputs.items():
            actual=result.get(key,path); ds=gdal.Open(actual)
            if ds is None: raise ValueError(f'SIMWE no produjo {key}; consulte {log_path}')
            if ds.RasterXSize!=mask.RasterXSize or ds.RasterYSize!=mask.RasterYSize:
                raise ValueError('La cuadrícula de salida SIMWE no coincide con la de entrada.')
            if not np.allclose(ds.GetGeoTransform(),mask.GetGeoTransform(),rtol=0,atol=res*1e-6):
                raise ValueError('La georreferencia de SIMWE no coincide con la máscara de cuenca.')
            arr=ds.ReadAsArray(); nodata=ds.GetRasterBand(1).GetNoDataValue()
            usable=valid & np.isfinite(arr)
            if nodata is not None: usable &= arr!=nodata
            if not usable.any(): raise ValueError(f'La salida {key} no contiene valores válidos.')
            if key=='depth':
                summary=dict(max_depth_m=float(arr[usable].max()),wet_area_km2=float((usable & (arr>0.01)).sum()*res*res/1e6),
                    surface_water_volume_m3=float(np.maximum(arr[usable],0).sum(dtype=np.float64)*res*res))
            ds=None
            # Keep dry zero distinct from NoData outside the basin.
            final=os.path.join(output,key+'_cuenca.tif')
            write_raster(final,np.where(usable,arr,-9999),mask); outputs[key]=final
            ds=gdal.Open(final,gdal.GA_Update)
            ds.GetRasterBand(1).SetUnitType('m3/s' if key=='discharge' else 'm')
            ds.GetRasterBand(1).SetDescription({'depth':'Profundidad de agua al final de la simulación',
                'discharge':'Descarga superficial SIMWE','error':'Error numérico de simulación SIMWE'}[key]); ds=None
        outputs['manning']=roughness['path']
        input_volume=excess/1000*int(valid.sum())*res*res
        ratio=summary['surface_water_volume_m3']/input_volume if input_volume>0 else None
        qa='REVISAR: almacenamiento supera aporte de lluvia (>10%); resultado no validado' if ratio is not None and ratio>1.1 else 'Control de cota superior de almacenamiento aprobado; no sustituye balance completo'
        summary.update(rainfall_input_volume_m3=input_volume,storage_to_input_ratio=ratio,volume_QA=qa)
        reported_params=dict(params)
        if roughness['mode']!='constant': reported_params['manning']=None
        manifest=dict(reported_params,algorithm=algorithm,filled_dem=dem_path,epsg=epsg,resolution_m=res,
            coverage=coverage,rainfall_excess_mm=excess,rainfall_excess_mmh=intensity,simulation_minutes=minutes,
            model='SIMWE overland flow; uniform rainfall excess; not river-stage inundation',outputs=outputs,
            roughness=roughness,units={'depth':'m','discharge':'m3/s','error':'m','manning':'s/m^(1/3)'},
            depth_time='end of simulation; not maximum over time',**summary)
        with open(os.path.join(output,'escenario.json'),'w',encoding='utf-8') as file: json.dump(manifest,file,ensure_ascii=False,indent=2)
        export_csv(os.path.join(output,'resumen_inundacion.csv'),[dict(reported_params,**summary,resolution_m=res,simulation_minutes=minutes,rainfall_excess_mm=excess,
            manning_min=roughness['min'],manning_max=roughness['max'],manning_mean=roughness['mean'],depth_units='m')])
        write_flood_explanation(output,manifest)
        task.setProgress(100)
        return manifest
    except Exception as exc:
        feedback.reportError(str(exc),True)
        raise
    finally:
        with open(log_path,'w',encoding='utf-8') as file: file.write(feedback.textLog())


def write_flood_explanation(output,manifest):
    import html,shutil
    guide=os.path.join(os.path.dirname(__file__),'Guia_inundacion.html')
    if os.path.isfile(guide): shutil.copyfile(guide,os.path.join(output,'Guia_inundacion.html'))
    r=manifest['roughness']
    rows={'Lluvia de diseño (mm)':manifest['rain_mm'],'CN medio':manifest['CN_mean'],
        'Lluvia excedente SCS-CN (mm)':manifest['rainfall_excess_mm'],
        'Intensidad excedente constante (mm/h)':manifest['rainfall_excess_mmh'],
        'Duración simulada (min)':manifest['simulation_minutes'],'Celda de cálculo (m)':manifest['resolution_m'],
        'Manning: modo':r['mode'],'Manning: origen':r['source'],'Manning mínimo':r['min'],
        'Manning máximo':r['max'],'Profundidad máxima espacial final (m)':manifest['max_depth_m'],
        'Control de volumen':manifest['volume_QA']}
    table=''.join('<tr><th>'+html.escape(str(k))+'</th><td>'+html.escape(str(v))+'</td></tr>' for k,v in rows.items())
    text='''<!doctype html><html lang="es"><meta charset="utf-8"><title>Escenario de inundación</title>
<style>body{font:17px/1.6 system-ui;max-width:950px;margin:40px auto;padding:24px}td,th{border:1px solid #bbb;padding:10px;text-align:left}table{border-collapse:collapse}</style>
<h1>Cómo se calculó este escenario</h1><p>SIMWE distribuye la lluvia excedente sobre el terreno y calcula el escurrimiento superficial con la rugosidad indicada.</p>
<p><strong>La profundidad está en metros y corresponde al final de la simulación.</strong> Un valor 0.25 significa 25 cm. NoData indica ausencia de resultado. El raster de Manning es una entrada del cálculo; no es una corrección aplicada después a la profundidad.</p>
<table>'''+table+'''</table><p><a href="Guia_inundacion.html">Guía del método, archivos, unidades y limitaciones</a></p>
<p>Los parámetros completos están en escenario.json. El control de volumen no sustituye una calibración ni un balance hidrológico completo.</p></html>'''
    with open(os.path.join(output,'Como_se_calculo.html'),'w',encoding='utf-8') as file:file.write(text)
