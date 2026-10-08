"""Terrain and principal-channel calculations on private datasets/geometries."""
import heapq
import math
import os
import tempfile
from qgis.core import QgsTask, QgsGeometry, QgsPointXY


def principal_route(lines, outlet, tolerance, check=lambda: None):
    """Longest shortest route from an outlet on a noded network (metric units).

    Exact endpoint keys preserve topology; nearby unrelated channels are never
    joined. On cyclic graphs the deterministic shortest-path tree is explicit.
    """
    network=QgsGeometry.unaryUnion([QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in line])
                                  for line in lines if len(line)>1])
    if network.isNull() or network.isEmpty(): raise ValueError('Red vacía o inválida dentro de la cuenca.')
    parts=network.asMultiPolyline() if network.isMultipart() else [network.asPolyline()]
    edges=[]
    for part in parts:
        for a,b in zip(part,part[1:]):
            a=(a.x(),a.y()); b=(b.x(),b.y())
            if a!=b: edges.append((a,b))
    if not edges: raise ValueError('La red no contiene segmentos utilizables.')
    best=None
    for index,(a,b) in enumerate(edges):
        check()
        dx,dy=b[0]-a[0],b[1]-a[1]
        t=max(0.,min(1.,((outlet[0]-a[0])*dx+(outlet[1]-a[1])*dy)/(dx*dx+dy*dy)))
        p=(a[0]+t*dx,a[1]+t*dy)
        d=math.dist(outlet,p)
        if best is None or d<best[0]: best=(d,index,p)
    distance,index,root=best
    if distance>tolerance: raise ValueError(f'El outlet está a {distance:.1f} m de la red; vuelva a seleccionarlo sobre la red.')
    a,b=edges.pop(index)
    if math.dist(root,a)<1e-7: root=a
    elif math.dist(root,b)<1e-7: root=b
    edges.extend([(a,root),(root,b)])
    graph={}
    for a,b in edges:
        if a==b: continue
        length=math.dist(a,b)
        graph.setdefault(a,{})[b]=length
        graph.setdefault(b,{})[a]=length
    distances={root:0.}; previous={}; queue=[(0.,root)]
    while queue:
        check()
        dist,node=heapq.heappop(queue)
        if dist!=distances[node]: continue
        for nxt,length in graph[node].items():
            candidate=dist+length
            if candidate<distances.get(nxt,float('inf')):
                distances[nxt]=candidate; previous[nxt]=node
                heapq.heappush(queue,(candidate,nxt))
    # Prefer a terminal headwater. Interior maxima occur only in closed loops.
    heads=[node for node in distances if node!=root and len(graph[node])==1]
    source=max(heads or distances,key=lambda node: distances[node])
    route=[source]
    while route[-1]!=root: route.append(previous[route[-1]])
    edge_count=sum(sum(n in distances for n in graph[v]) for v in distances)//2
    return route, distances[source], len(graph)-len(distances), edge_count>=len(distances), distance


class TerrainTask(QgsTask):
    def __init__(self, dem_path, basin_wkt, lines, outlet, epsg, output, callback, raster_inputs=None):
        super().__init__('Pendientes de cuenca y cauce principal',QgsTask.CanCancel)
        self.dem_path,self.basin_wkt,self.lines=dem_path,basin_wkt,lines
        self.outlet,self.epsg,self.output,self.callback=outlet,epsg,output,callback
        self.report=''; self.error=''; self.channel=None; self.metrics={}
        self.raster_inputs=dict(raster_inputs or {})

    def check(self):
        if self.isCanceled(): raise InterruptedError('Cálculo de pendientes cancelado.')

    def run(self):
        try:
            from osgeo import gdal,ogr,osr
            import numpy as np
            self.check()
            source=gdal.Open(self.dem_path)
            if source is None: raise ValueError('No se pudo abrir el MDE con GDAL.')
            srs=osr.SpatialReference(); srs.ImportFromEPSG(self.epsg)
            vrt=gdal.AutoCreateWarpedVRT(source,None,srs.ExportToWkt(),gdal.GRA_Bilinear)
            if vrt is None: raise ValueError('No se pudo reproyectar el MDE.')
            gt=vrt.GetGeoTransform(); resolution=max(abs(gt[1]),abs(gt[5]))
            basin=ogr.CreateGeometryFromWkt(self.basin_wkt)
            xmin,xmax,ymin,ymax=basin.GetEnvelope()
            # Explicit analysis grid capped near 4 million cells; report resolution.
            resolution=max(resolution,math.sqrt((xmax-xmin)*(ymax-ymin)/4000000))
            bounds=(xmin-2*resolution,ymin-2*resolution,xmax+2*resolution,ymax+2*resolution)
            temp_dir=tempfile.TemporaryDirectory(prefix='dh_terrain_')
            directory=temp_dir.name
            dem=gdal.Warp(os.path.join(directory,'dem.tif'),source,dstSRS=srs.ExportToWkt(),
                outputBounds=bounds,xRes=resolution,yRes=resolution,resampleAlg='bilinear',
                outputType=gdal.GDT_Float32,dstNodata=-9999,
                callback=lambda *args: not self.isCanceled())
            if dem is None: self.check(); raise ValueError('Falló la reproyección del MDE.')
            self.setProgress(25); self.check()
            slope=gdal.DEMProcessing(os.path.join(directory,'slope.tif'),dem,'slope',
                slopeFormat='percent',scale=1,computeEdges=False,
                callback=lambda *args: not self.isCanceled())
            if slope is None: self.check(); raise ValueError('Falló el cálculo de pendiente.')
            mask=gdal.GetDriverByName('MEM').Create('',dem.RasterXSize,dem.RasterYSize,1,gdal.GDT_Byte)
            mask.SetGeoTransform(dem.GetGeoTransform()); mask.SetProjection(srs.ExportToWkt())
            vectors=ogr.GetDriverByName('Memory').CreateDataSource('')
            layer=vectors.CreateLayer('basin',srs,ogr.wkbUnknown)
            feature=ogr.Feature(layer.GetLayerDefn()); feature.SetGeometry(basin); layer.CreateFeature(feature)
            gdal.RasterizeLayer(mask,[1],layer,burn_values=[1])
            total=0.; count=0; inside=0; ztotal=0.; zcount=0; zmin=float('inf'); zmax=-float('inf')
            band=slope.GetRasterBand(1); nodata=band.GetNoDataValue()
            for y in range(0,dem.RasterYSize,256):
                self.check(); height=min(256,dem.RasterYSize-y)
                values=band.ReadAsArray(0,y,dem.RasterXSize,height)
                selection=mask.GetRasterBand(1).ReadAsArray(0,y,dem.RasterXSize,height)>0
                inside+=int(selection.sum())
                valid=selection & np.isfinite(values) & (values!=nodata)
                count+=int(valid.sum()); total+=float(values[valid].sum(dtype=np.float64))
                elevations=dem.GetRasterBand(1).ReadAsArray(0,y,dem.RasterXSize,height)
                zvalid=selection & np.isfinite(elevations) & (elevations!=-9999)
                if zvalid.any():
                    z=elevations[zvalid]; ztotal+=float(z.sum(dtype=np.float64)); zcount+=z.size
                    zmin=min(zmin,float(z.min())); zmax=max(zmax,float(z.max()))
            if not count: raise ValueError('No hay celdas de pendiente válidas dentro de la cuenca.')
            mean=total/count; coverage=100*count/max(inside,1)
            self.metrics['basin_slope_pct']=mean
            self.metrics.update(area_km2=basin.GetArea()/1e6,z_mean=ztotal/zcount if zcount else None,
                z_min=zmin,z_max=zmax,resolution_m=resolution,dem_path=self.dem_path)
            from .basin_products import clip_raster
            prefix=os.path.splitext(self.output)[0]
            dem.FlushCache(); slope.FlushCache()
            products={}
            sources=dict(self.raster_inputs)
            sources['mde']=os.path.join(directory,'dem.tif')
            sources['pendiente']=os.path.join(directory,'slope.tif')
            for name,source_path in sources.items():
                self.check()
                products[name]=clip_raster(source_path,self.basin_wkt,self.epsg,
                    prefix+'_'+name+'_cuenca.tif',self.check)
            self.metrics['basin_rasters']=products
            self.metrics['dem_path']=products['mde']
            self.report=(f'\nPENDIENTES Y CAUCE PRINCIPAL\nPendiente media de la cuenca = {mean:.3f} % ({mean/100:.5f} m/m)\n'
                f'Método: media de pendientes Horn por celda dentro del polígono; MDE reproyectado.\n'
                f'Resolución de análisis: {resolution:.2f} m; cobertura válida: {coverage:.1f} %. Elevaciones asumidas en metros.\n')
            if coverage<95: self.report+='ADVERTENCIA: cobertura incompleta del MDE; la media corresponde solo al área válida.\n'
            self.report+='MDE y escurrimientos recortados (exterior NoData):\n'+''.join(
                name+': '+path+'\n' for name,path in products.items())
            self.setProgress(60)
            try:
                if not self.lines: raise ValueError('Seleccione una red vectorial de líneas en Entradas.')
                if self.outlet is None: raise ValueError('Seleccione el punto de salida sobre la red en la pestaña 1.')
                polygon=QgsGeometry.fromWkt(self.basin_wkt)
                clipped=[]
                for line in self.lines:
                    self.check()
                    geometry=QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in line]).intersection(polygon)
                    if geometry.isEmpty(): continue
                    for part in geometry.asGeometryCollection():
                        if part.type()==1:
                            clipped.extend([[(p.x(),p.y()) for p in segment] for segment in
                                (part.asMultiPolyline() if part.isMultipart() else [part.asPolyline()])])
                route,length,disconnected,cycle,snap=principal_route(clipped,self.outlet,max(resolution*3,1),self.check)
                if length<=0: raise ValueError('La longitud del cauce es cero.')
                transform=dem.GetGeoTransform(); inv=gdal.InvGeoTransform(transform)
                def elevation(point):
                    x,y=gdal.ApplyGeoTransform(inv,*point); x,y=math.floor(x),math.floor(y)
                    if not (0<=x<dem.RasterXSize and 0<=y<dem.RasterYSize): return None
                    value=float(dem.GetRasterBand(1).ReadAsArray(x,y,1,1)[0,0])
                    return value if math.isfinite(value) and value!=-9999 else None
                z0,z1=elevation(route[0]),elevation(route[-1])
                direct=math.dist(route[0],route[-1]); sinuosity=length/direct if direct else float('nan')
                gradient=(z0-z1)/length if z0 is not None and z1 is not None else None
                self.report+=(f'Cauce principal: ruta conectada más larga hasta el outlet, dentro de la cuenca.\n'
                    f'Longitud = {length/1000:.3f} km; distancia recta = {direct/1000:.3f} km; sinuosidad = {sinuosity:.3f}.\n'
                    f'Ajuste del outlet a la red: {snap:.2f} m.\n')
                if gradient is None: self.report+='Pendiente longitudinal: N/D; falta elevación en un extremo del cauce.\n'
                else:
                    self.report+=(f'Cota cabecera = {z0:.2f} m; cota salida = {z1:.2f} m; desnivel = {z0-z1:.2f} m.\n'
                        f'Pendiente media longitudinal del cauce = {gradient*100:.3f} % ({gradient:.5f} m/m), calculada como ΔZ/L.\n')
                    if gradient<0: self.report+='ADVERTENCIA: pendiente negativa; revisar outlet, conectividad y MDE.\n'
                if disconnected: self.report+=f'ADVERTENCIA: {disconnected} nodos desconectados del outlet fueron excluidos.\n'
                if cycle: self.report+='ADVERTENCIA: red con ciclos; se usó la ruta mínima a cada cabecera y se eligió la más larga. Revisar bifurcaciones.\n'
                self.check()
                dest=ogr.GetDriverByName('GPKG').CreateDataSource(self.output)
                if dest is None: raise ValueError('No se pudo crear el GeoPackage del cauce principal.')
                output_layer=dest.CreateLayer('cauce_principal',srs,ogr.wkbLineString)
                for name in ('length_m','straight_m','sinuosity','z_head_m','z_out_m','drop_m','slope_pct','grid_m'):
                    output_layer.CreateField(ogr.FieldDefn(name,ogr.OFTReal))
                feat=ogr.Feature(output_layer.GetLayerDefn()); geom=ogr.Geometry(ogr.wkbLineString)
                for x,y in route: geom.AddPoint_2D(x,y)
                feat.SetGeometry(geom)
                vals=[length,direct,sinuosity,z0,z1,None if gradient is None else z0-z1,None if gradient is None else gradient*100,resolution]
                for i,v in enumerate(vals):
                    if v is not None and math.isfinite(v): feat.SetField(i,float(v))
                if output_layer.CreateFeature(feat)!=0: raise ValueError('No se pudo guardar el cauce principal.')
                feat=None; output_layer=None; dest=None
                self.channel=self.output
                self.metrics.update(length_m=length, channel_slope=gradient,z_out=z1,z_head=z0)
                try:
                    from .network_order import append_network
                    reaches,missing=append_network(self.output,clipped,self.outlet,max(resolution*3,1),self.epsg,self.check)
                    self.metrics['network_path']=self.output+'|layername=red_cuenca'
                    self.metrics['network_unordered']=missing
                    self.report+=f'Red recortada y orden Strahler: {reaches} tramos; {missing} tramos sin orden por desconexión o ciclos.\n'
                except InterruptedError: raise
                except Exception as exc: self.report+='Orden Strahler no calculado: '+str(exc)+'\n'
            except InterruptedError: raise
            except Exception as exc: self.report+='Cauce principal no calculado: '+str(exc)+'\n'
            mask=None; band=None; slope=None; dem=None; vrt=None; source=None
            self.setProgress(100)
            return True
        except Exception as exc:
            self.error=str(exc); return False
        finally:
            # Release GDAL handles before removing temporary files on Windows,
            # including cancellation and failures partway through the calculation.
            mask=None; band=None; slope=None; dem=None; vrt=None; source=None
            if 'temp_dir' in locals():
                try: temp_dir.cleanup()
                except OSError: pass

    def finished(self, result):
        if self.callback: self.callback(bool(result),self.report,self.channel,self.error)
