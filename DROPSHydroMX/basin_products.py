"""Persistent basin masks on each source raster's own grid (no interpolation)."""
import math
import os


def finalize_basin(source_path, output, reference_wkt):
    """Assign known direction-grid CRS to a freshly derived basin, without reprojection.

    Only for polygons produced from r.water.outlet on that same direction grid.
    Never infer the CRS of an arbitrary external vector using this function.
    """
    from osgeo import gdal, osr
    reference = osr.SpatialReference()
    if reference.ImportFromWkt(reference_wkt) != 0 or reference.Validate() != 0:
        raise ValueError('El CRS de la dirección de flujo no es válido para guardar la cuenca.')
    result = gdal.VectorTranslate(output, source_path,
        options=['-f', 'GPKG', '-a_srs', reference_wkt, '-nln', 'cuenca'])
    if result is None:
        raise ValueError('No se pudo guardar el GeoPackage final de cuenca: ' + output)
    result = None
    reopened = gdal.OpenEx(output, gdal.OF_VECTOR)
    if reopened is None:
        raise ValueError('No se pudo reabrir el GeoPackage final: ' + output)
    layer = reopened.GetLayer(0)
    saved = layer.GetSpatialRef()
    if saved is None or layer.GetFeatureCount() == 0:
        raise ValueError('El GeoPackage final quedó sin geometría o referencia espacial: ' + output)
    saved = saved.Clone()
    # Compare coordinate systems semantically, not QGIS IDs or serialized WKT.
    for crs in (reference, saved):
        crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if not reference.IsSame(saved):
        raise ValueError('No se pudo verificar el CRS persistido. Archivo: ' + output
                         + '\nCRS de dirección: ' + reference_wkt
                         + '\nCRS guardado: ' + saved.ExportToWkt())
    layer = None; reopened = None
    return output


def clip_raster(source_path, basin_wkt, basin_epsg, output, check=lambda: None):
    from osgeo import gdal, ogr, osr
    import numpy as np
    source = gdal.Open(source_path)
    if source is None or not source.GetProjection():
        raise ValueError('El raster no tiene un CRS definido: ' + source_path)
    src_srs = osr.SpatialReference(wkt=source.GetProjection())
    basin_srs = osr.SpatialReference(); basin_srs.ImportFromEPSG(basin_epsg)
    for crs in (src_srs, basin_srs):
        crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    geometry = ogr.CreateGeometryFromWkt(basin_wkt)
    geometry.Transform(osr.CoordinateTransformation(basin_srs, src_srs))
    gt = source.GetGeoTransform()
    if gt[2] or gt[4] or gt[1] <= 0 or gt[5] >= 0:
        raise ValueError('El recorte requiere un raster orientado al norte: ' + source_path)
    xmin, xmax, ymin, ymax = geometry.GetEnvelope()
    inv = gdal.InvGeoTransform(gt)
    x0, y0 = gdal.ApplyGeoTransform(inv, xmin, ymax)
    x1, y1 = gdal.ApplyGeoTransform(inv, xmax, ymin)
    # Remove only floating point noise at exact pixel boundaries.
    left, top = max(0, math.floor(x0+1e-7)), max(0, math.floor(y0+1e-7))
    right, bottom = min(source.RasterXSize, math.ceil(x1-1e-7)), min(source.RasterYSize, math.ceil(y1-1e-7))
    if right <= left or bottom <= top:
        raise ValueError('El raster no intersecta la cuenca: ' + source_path)
    pending = output + '.part.tif'
    dest = mask = vectors = None
    try:
        check()
        # Float64 preserves flow direction integers and accumulation values.
        # A separate negative NoData is needed for unsigned source rasters.
        dest = gdal.Translate(pending, source, srcWin=[left, top, right-left, bottom-top],
            outputType=gdal.GDT_Float64, creationOptions=['COMPRESS=DEFLATE','TILED=YES','BIGTIFF=IF_SAFER'])
        if dest is None: raise ValueError('No se pudo crear el recorte: ' + output)
        mask = gdal.GetDriverByName('MEM').Create('', right-left, bottom-top, 1, gdal.GDT_Byte)
        mask.SetGeoTransform(dest.GetGeoTransform()); mask.SetProjection(dest.GetProjection())
        vectors = ogr.GetDriverByName('Memory').CreateDataSource('')
        layer = vectors.CreateLayer('mask', src_srs, ogr.wkbUnknown)
        feature = ogr.Feature(layer.GetLayerDefn()); feature.SetGeometry(geometry); layer.CreateFeature(feature)
        if gdal.RasterizeLayer(mask, [1], layer, burn_values=[1]) != 0:
            raise ValueError('No se pudo rasterizar el contorno de la cuenca.')
        band = dest.GetRasterBand(1); old_nodata = source.GetRasterBand(1).GetNoDataValue()
        nodata = old_nodata if old_nodata is not None else -9999.
        band.SetNoDataValue(nodata)
        for y in range(0, bottom-top, 256):
            check(); height = min(256, bottom-top-y)
            data = band.ReadAsArray(0, y, right-left, height)
            inside = mask.GetRasterBand(1).ReadAsArray(0, y, right-left, height) > 0
            data[~inside | ~np.isfinite(data)] = nodata
            band.WriteArray(data, 0, y)
        # Translate may copy cached full-raster statistics; discard after masking.
        for key in list(band.GetMetadata()):
            if key.startswith('STATISTICS_'): band.SetMetadataItem(key, None)
        band = None; dest = None
        check(); os.replace(pending, output)
        return output
    finally:
        band = None; dest = None; mask = None; vectors = None; source = None
        if os.path.exists(pending): os.remove(pending)
