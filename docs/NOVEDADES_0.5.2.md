# DROPS-Hydro MX 0.5.2

Corrige el bloqueo «El archivo de cuenca no conservó el CRS de la dirección de flujo» introducido en la validación de la versión 0.5.1.

La cuenca recién generada se guarda en un GeoPackage final nuevo mediante asignación explícita del CRS completo de la dirección de flujo. Esta asignación conserva las coordenadas, pues el polígono procede de ese raster; no reproyecta ni intenta inferir el CRS de archivos externos. Se reabre el archivo y se verifica la equivalencia espacial con GDAL/PROJ, evitando depender de identificadores internos de QGIS o de diferencias en la representación del CRS. La capa de QGIS recibe también el CRS original.

Instala `DROPS-Hydro_MX_v0.5.2.zip`, reinicia QGIS y vuelve a ejecutar **Delimitar cuenca** con la dirección, acumulación, red y outlet existentes. No hace falta repetir la descarga del MDE. Espera a que termine el recorte y continúa con Morfometría y CHIRPS.

Pruebas: QGIS 3.44.14; CRS EPSG:32614 y proyección personalizada, con metadatos de origen ausentes o incorrectos en el vector intermedio. Los cuatro casos conservaron las coordenadas y persistieron el CRS correcto después de reabrir el archivo. Pasó también la prueba de delimitación con salidas GRASS simuladas, siete recortes, pendiente del 10%, estadísticas de cuenca y preparación de geometría CHIRPS. No se dispone del proyecto que produjo el error para confirmar cuál fue la diferencia concreta entre sus CRS.

Se conservan las funciones y límites de las versiones anteriores, documentados dentro del ZIP.
