# DROPS-Hydro MX 0.5.1

Esta actualización conserva las funciones de la versión 0.5.0 y corrige el recorte permanente y la referencia espacial de los productos de cuenca.

## Cambios

- Al delimitar la cuenca, guarda y carga recortes del MDE de análisis, pendiente, MDE original, filled DEM, acumulación, dirección y raster de escurrimientos, cuando esos insumos están disponibles. Mantiene la red vectorial recortada y su orden Strahler de la versión anterior.
- El exterior del polígono queda como NoData. La máscara usa el centro de cada celda; el borde conserva la resolución del raster y puede verse escalonado.
- Los recortes de los insumos conservan la grilla y el CRS originales, sin interpolar direcciones ni acumulaciones. El MDE de análisis y su pendiente se guardan en UTM, con resolución indicada en el reporte; el análisis mantiene el límite previo de unos cuatro millones de celdas.
- Entradas selecciona automáticamente el MDE recortado de análisis. Las capas completas reemplazadas se ocultan en el mapa y sus archivos se conservan para delimitar otras cuencas.
- Morfometría calcula elevaciones y pendiente media usando únicamente celdas válidas dentro de la cuenca. La pendiente es la media de pendientes Horn, en porcentaje. Se usan celdas vecinas del MDE de origen para calcular derivadas junto al borde antes de aplicar la máscara: así NoData no crea un desnivel artificial. Si existe filled DEM, tiene prioridad como en la versión anterior. Las elevaciones deben estar en metros.
- Guarda las rutas de los recortes en la sesión para recuperarlos al abrir el plugin.
- La cuenca generada recibe el CRS completo del raster de dirección de flujo, tanto en la ruta GRASS como en la alternativa de poligonización. Este CRS se escribe en los archivos, no solo en la capa de la sesión. También se conserva en el raster de cuenca.
- CHIRPS transforma la cuenca desde su CRS real a WGS84 para obtener el centroide o el polígono de consulta. No se asigna EPSG:4326 a coordenadas proyectadas.

## Instalación y uso

1. Cierra el diálogo del plugin. En QGIS, usa **Complementos → Administrar e instalar complementos → Instalar a partir de ZIP** y selecciona `DROPS-Hydro_MX_v0.5.1.zip`. Reinicia QGIS para descargar de memoria los módulos anteriores.
2. Recupera el MDE, filled DEM, dirección, acumulación y red existentes; selecciona el outlet y vuelve a ejecutar **Delimitar cuenca**. No es necesario repetir la descarga del MDE si sus archivos y CRS son válidos.
3. Espera a que termine la tarea de pendientes y recortes. Sus rutas aparecen en el reporte y se guardan en la carpeta de trabajo.
4. Continúa con Morfometría y CHIRPS. Para cuencas antiguas sin CRS, vuelve a delimitar: esta versión no adivina el CRS de archivos externos sin referencia espacial. Si el propio MDE o la dirección carecen de CRS, primero debe definirse su sistema real a partir de la procedencia del dato.

## Verificación y límites

Probado con QGIS 3.44.14 y datos sintéticos: contorno triangular con 3200 celdas interiores, grilla y CRS conservados, archivos originales sin cambios; siete recortes abiertos nuevamente con CRS; pendiente conocida del 10%; estadísticas restringidas a la cuenca; transformación a WGS84 y geometría de consulta CHIRPS. Se reprodujo una salida vectorial sin CRS y se verificó la persistencia de EPSG:32614 después de guardarla. En esa prueba se sustituyeron las salidas de GRASS para aislar el problema de guardado; no constituye una nueva prueba completa de `r.water.outlet`.

También pasó la cadena de interfaz de ajuste estadístico, terreno, Strahler, CN, tiempos de concentración, 45 estimaciones de gasto y CSV. La descarga real de CHIRPS para tu cuenca y el funcionamiento con tus datos aún requieren comprobación en tu proyecto. Se mantienen los métodos y límites de inundación y de los demás módulos descritos en `NOVEDADES_0.5.0.md` dentro del ZIP.
