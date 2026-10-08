# DROPS-Hydro MX v0.4.11

CHIRPS obtiene automáticamente la longitud y latitud del centroide de la cuenca seleccionada en **Entradas**. El centroide se calcula en el CRS de la capa y se transforma a **WGS84 / EPSG:4326**. Los campos muestran seis decimales y son de solo lectura; la consulta utiliza las coordenadas sin redondear.

Las coordenadas se actualizan al cambiar de cuenca, al abrir la pestaña CHIRPS y justo antes de descargar. Si no hay una cuenca válida, los campos quedan vacíos y se solicita seleccionar una; no se utilizan coordenadas predeterminadas.

En ClimateSERV v2 se solicita el promedio de un área de **0.05° × 0.05° centrada en el centroide**, en lugar del promedio de toda la cuenca. No se garantiza que esa área coincida exactamente con una sola celda nativa de CHIRPS. En el modo v3 directo se muestrea el punto exacto del centroide; se corrige el desplazamiento que producía promediar los vértices de un polígono cerrado.

Se conservan las mejoras de descarga, pendientes y cauce principal de v0.4.10. El diagnóstico pendiente de r.to.vect y la validación remota de CHIRPS mantienen las limitaciones documentadas en esa versión.

## Uso

Instala el ZIP v0.4.11 desde el administrador de complementos y reinicia QGIS. Selecciona la cuenca en **Entradas** y abre **CHIRPS / extremos**: las coordenadas aparecerán automáticamente.

## Validación

Pruebas con las bibliotecas de QGIS 3.44.14: centroide conocido en EPSG:4326, transformación desde UTM, cambio de cuenca, edición de geometría antes de consultar, campos de solo lectura, consulta centrada y rechazo de cuenca ausente. Todas pasaron. No se realizó una descarga real del servicio en esta revisión.
